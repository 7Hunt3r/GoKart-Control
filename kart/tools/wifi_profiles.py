#!/usr/bin/env python3
"""Write persistent NetworkManager keyfile profiles for the kart Wi-Fi (and eth0 DHCP).

    sudo python3 wifi_profiles.py /etc/gokart-kart/wifi.env [--dir /etc/NetworkManager/system-connections]

Why: on 2026-11-06 the pit Pi lost its Wi-Fi after repeated reboots. The profile
cloud-init had created lived in netplan (/etc/netplan/90-NM-<uuid>.yaml, written by
NetworkManager when firstboot ran `nmcli connection modify` on it) and power cuts left
those YAML files 0 bytes long, so NetworkManager came back with no Wi-Fi profile.
Plain keyfiles in /etc/NetworkManager/system-connections are what NetworkManager
itself persists and are written here atomically (temp file + fsync + rename).

The env file uses the secrets.env format: WIFI_SSID, WIFI_PASSWORD (primary,
priority 20) and optional WIFI2_SSID / WIFI2_PASSWORD (priority 10). Idempotent:
a profile is only rewritten when its content would change. An existing keyfile for
the same SSID (e.g. one made by hand with `nmcli con add`) is adopted: same file,
same UUID, so the live connection is updated in place and not dropped.
Never prints a password. Standard library only.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid

NM_DIR = "/etc/NetworkManager/system-connections"
PRIMARY_PRIO, SECONDARY_PRIO = 20, 10
ETH_ID = "gokart-eth0"


def load_env(text: str) -> dict:
    """KEY=value lines, values shell-quoted as in secrets.env (same rules as gen_cloudinit.load_env)."""
    env = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        try:
            parts = shlex.split(v, comments=True)
        except ValueError:
            parts = [v]
        env[k.strip()] = parts[0] if parts else ""
    return env


def networks(env: dict) -> list[tuple[str, str, int]]:
    """[(ssid, psk, priority)] from the env; raises ValueError on bad input (message has no password)."""
    out = []
    for prefix, prio in (("WIFI", PRIMARY_PRIO), ("WIFI2", SECONDARY_PRIO)):
        ssid, psk = env.get(prefix + "_SSID", ""), env.get(prefix + "_PASSWORD", "")
        if not ssid:
            continue
        if not 8 <= len(psk) <= 63:
            raise ValueError(f"{prefix}_PASSWORD for {ssid!r} must be 8..63 characters (WPA2-PSK)")
        for name, val in (("SSID", ssid), ("PASSWORD", psk)):
            if any(c in val for c in "\n\r\0") or val != val.strip():
                raise ValueError(f"{prefix}_{name} has leading/trailing spaces or control characters")
        if len(ssid.encode()) > 32:
            raise ValueError(f"{prefix}_SSID {ssid!r} is longer than 32 bytes")
        if any(ssid == s for s, _, _ in out):
            continue
        out.append((ssid, psk, prio))
    return out


def kf_escape(v: str) -> str:
    """GKeyFile value escaping (backslash first; ';' is the list separator NetworkManager splits on)."""
    return v.replace("\\", "\\\\").replace(";", "\\;")


def render_wifi(ssid: str, psk: str, prio: int, uid: str) -> str:
    return (
        "[connection]\n"
        f"id={kf_escape(ssid)}\n"
        f"uuid={uid}\n"
        "type=wifi\n"
        "interface-name=wlan0\n"
        "autoconnect=true\n"
        f"autoconnect-priority={prio}\n"
        "autoconnect-retries=0\n"           # 0 = retry forever (default 4, then blocked for 5 min)
        "\n[wifi]\n"
        "mode=infrastructure\n"
        f"ssid={kf_escape(ssid)}\n"
        "\n[wifi-security]\n"
        "key-mgmt=wpa-psk\n"
        f"psk={kf_escape(psk)}\n"
        "\n[ipv4]\n"
        "method=auto\n"
        "\n[ipv6]\n"
        "addr-gen-mode=default\n"
        "method=auto\n"
    )


def render_eth(uid: str) -> str:
    """eth0 DHCP fallback: lowest priority, may-fail so a dead cable never blocks anything."""
    return (
        "[connection]\n"
        f"id={ETH_ID}\n"
        f"uuid={uid}\n"
        "type=ethernet\n"
        "interface-name=eth0\n"
        "autoconnect=true\n"
        "autoconnect-priority=0\n"
        "autoconnect-retries=0\n"
        "\n[ethernet]\n"
        "\n[ipv4]\n"
        "method=auto\n"
        "may-fail=true\n"
        "\n[ipv6]\n"
        "addr-gen-mode=default\n"
        "method=auto\n"
        "may-fail=true\n"
    )


def slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "wifi"
    return s[:60]


def parse_keyfile(text: str) -> dict:
    """{'section.key': value} (values unescaped only for \\\\ and \\;)."""
    out, sec = {}, ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1]
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[f"{sec}.{k.strip()}"] = v.strip().replace("\\;", ";").replace("\\\\", "\\")
    return out


def existing_profiles(nm_dir: str) -> list[tuple[str, dict]]:
    out = []
    try:
        names = sorted(os.listdir(nm_dir))
    except OSError:
        return out
    for n in names:
        if not n.endswith(".nmconnection"):
            continue
        p = os.path.join(nm_dir, n)
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                out.append((p, parse_keyfile(f.read())))
        except OSError:
            continue
    return out


def plan(env: dict, nm_dir: str, eth: bool = True) -> list[tuple[str, str, str]]:
    """[(path, content, label)] for every profile that should exist. Pure apart from reading nm_dir."""
    have = existing_profiles(nm_dir)
    out = []
    for ssid, psk, prio in networks(env):
        match = next(((p, kv) for p, kv in have
                      if kv.get("connection.type") in ("wifi", "802-11-wireless") and kv.get("wifi.ssid") == ssid), None)
        if match:
            path, uid = match[0], match[1].get("connection.uuid") or str(uuid.uuid5(uuid.NAMESPACE_DNS, "gokart-kart-wifi:" + ssid))
        else:
            path = os.path.join(nm_dir, slug(ssid) + ".nmconnection")
            uid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "gokart-kart-wifi:" + ssid))
        out.append((path, render_wifi(ssid, psk, prio, uid), f"wifi {ssid!r} priority {prio}"))
    if eth:
        match = next(((p, kv) for p, kv in have if kv.get("connection.id") == ETH_ID), None)
        path = match[0] if match else os.path.join(nm_dir, ETH_ID + ".nmconnection")
        uid = (match[1].get("connection.uuid") if match else None) or str(uuid.uuid5(uuid.NAMESPACE_DNS, "gokart-kart-eth0"))
        out.append((path, render_eth(uid), "eth0 DHCP fallback"))
    return out


def write_atomic(path: str, content: str) -> bool:
    """Write (0600) only if different. Returns True when the file changed."""
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == content:
                return False
    except OSError:
        pass
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".gokart-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("env_file")
    ap.add_argument("--dir", default=NM_DIR)
    ap.add_argument("--no-eth", action="store_true", help="do not write the eth0 DHCP profile")
    ap.add_argument("--no-reload", action="store_true", help="do not run `nmcli connection reload`")
    a = ap.parse_args(argv)
    try:
        with open(a.env_file, encoding="utf-8") as f:
            env = load_env(f.read())
        items = plan(env, a.dir, eth=not a.no_eth)
    except (OSError, ValueError) as e:
        print(f"wifi_profiles: {e}", file=sys.stderr)
        return 2
    if not any(lbl.startswith("wifi") for _, _, lbl in items):
        print("wifi_profiles: no WIFI_SSID in the env file; nothing to do for Wi-Fi")
    changed = 0
    for path, content, label in items:
        did = write_atomic(path, content)
        changed += did
        print(f"  {label}: {path} ({'written' if did else 'unchanged'})")
    if changed and not a.no_reload:
        r = subprocess.run(["nmcli", "connection", "reload"], capture_output=True, text=True)
        print("  nmcli connection reload: " + ("ok" if r.returncode == 0 else "FAILED " + r.stderr.strip()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
