#!/usr/bin/env python3
"""Generate cloud-init seed files for the GoKart pit Pi 4 from secrets.env.

Writes build/user-data, build/network-config, build/meta-data next to this file.
The whole pit/ folder (minus secrets, logs and build output) is bundled into
user-data as a base64 tarball and unpacked to /opt/gokart-pit on first boot,
so no network clone is needed.

Raspberry Pi OS Trixie uses cloud-init NoCloud, seeded from the FAT boot
partition. custom.toml and wpa_supplicant.conf are ignored.
Adapted from ~/studebaker-headunit/tools/gen_cloudinit.py (sha512-crypt copied verbatim).
"""
import base64
import hashlib
import io
import json
import os
import secrets as pysecrets
import shlex
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # pit/sdcard
PIT = HERE.parent                               # pit/
BUILD = HERE / "build"
# Never ship these in the payload (secrets, runtime state, generated files).
EXCLUDE_TOP = {"logs", "build"}
EXCLUDE_REL = {"sdcard/secrets.env", "sdcard/build", "server/config.json"}

# ---------------------------------------------------------------------------
# sha512-crypt ($6$) in pure Python. macOS ships LibreSSL (no `openssl passwd -6`)
# and Python 3.13 dropped the crypt module, so we carry our own. Verified against
# the reference vector in self_test() below.
# ---------------------------------------------------------------------------
_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _b64_24(b2, b1, b0, n):
    w = (b2 << 16) | (b1 << 8) | b0
    out = ""
    for _ in range(n):
        out += _ITOA64[w & 0x3F]
        w >>= 6
    return out


def sha512_crypt(password: str, salt: str | None = None) -> str:
    if salt is None:
        salt = "".join(pysecrets.choice(_ITOA64) for _ in range(16))
    salt = salt[:16]
    pw, s = password.encode(), salt.encode()
    rounds = 5000
    b = hashlib.sha512(pw + s + pw).digest()
    a = hashlib.sha512(pw + s)
    plen = len(pw)
    a.update((b * (plen // 64 + 1))[:plen])
    i = plen
    while i > 0:
        a.update(b if i & 1 else pw)
        i >>= 1
    a = a.digest()
    dp = hashlib.sha512(pw * plen).digest()
    p = (dp * (plen // 64 + 1))[:plen]
    ds = hashlib.sha512(s * (16 + a[0])).digest()
    sv = (ds * (len(s) // 64 + 1))[: len(s)]
    c = a
    for i in range(rounds):
        h = hashlib.sha512()
        h.update(p if i & 1 else c)
        if i % 3:
            h.update(sv)
        if i % 7:
            h.update(p)
        h.update(c if i & 1 else p)
        c = h.digest()
    order = [(0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4),
             (47, 5, 26), (6, 27, 48), (28, 49, 7), (50, 8, 29), (9, 30, 51),
             (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13), (56, 14, 35),
             (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19),
             (62, 20, 41)]
    out = "".join(_b64_24(c[x], c[y], c[z], 4) for x, y, z in order)
    out += _b64_24(0, 0, c[63], 2)
    return f"$6${salt}${out}"


def self_test():
    ref = ("$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJ"
           "uesI68u4OTLiBFdcbYEdFCoEOfaS35inz1")
    got = sha512_crypt("Hello world!", "saltstring")
    if got != ref:
        sys.exit("sha512_crypt self-test FAILED; refusing to generate a bad hash")


# ---------------------------------------------------------------------------
def load_env(path: Path) -> dict:
    """Parse KEY="value" lines the same way a shell would."""
    env = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        parts = shlex.split(v, comments=True)
        env[k.strip()] = parts[0] if parts else ""
    return env


def q(s: str) -> str:
    """JSON string literal == valid YAML double-quoted scalar."""
    return json.dumps(s)


def payload_b64() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(PIT.rglob("*")):
            rel = p.relative_to(PIT).as_posix()
            if (rel.split("/")[0] in EXCLUDE_TOP or any(rel == e or rel.startswith(e + "/") for e in EXCLUDE_REL)
                    or "__pycache__" in p.parts or p.name in (".DS_Store",) or p.name.startswith("._")):
                continue
            info = tar.gettarinfo(str(p), arcname=rel)
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            if p.is_file():
                with open(p, "rb") as f:
                    tar.addfile(info, f)
            else:
                tar.addfile(info)
    return base64.b64encode(buf.getvalue()).decode()


def access_points(env) -> str:
    aps = [(env["WIFI_SSID"], env["WIFI_PASSWORD"])]
    if env.get("WIFI2_SSID"):
        if not env.get("WIFI2_PASSWORD"):
            sys.exit("WIFI2_SSID is set but WIFI2_PASSWORD is empty")
        aps.append((env["WIFI2_SSID"], env["WIFI2_PASSWORD"]))
    pad = " " * 8
    return "\n".join(f"{pad}{q(ssid)}:\n{pad}  password: {q(pw)}" for ssid, pw in aps)


def main():
    self_test()
    env_path = Path(os.environ.get("GOKART_SECRETS") or HERE / "secrets.env")
    if not env_path.exists():
        sys.exit("pit/sdcard/secrets.env not found. Copy secrets.env.example and fill it in.")
    env = load_env(env_path)
    required = ["WIFI_SSID", "WIFI_PASSWORD", "WIFI_COUNTRY", "PI_USER", "PI_PASSWORD", "PIT_WEB_PASSWORD"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        sys.exit(f"secrets.env is missing: {', '.join(missing)}")
    if len(env["WIFI_PASSWORD"]) < 8:
        sys.exit("WIFI_PASSWORD is shorter than 8 characters (WPA2 minimum); check secrets.env")
    hostname = env.get("PI_HOSTNAME") or "gokart-pit"
    tz = env.get("TIMEZONE") or "America/New_York"
    user = env["PI_USER"]
    if not user.isidentifier() or user != user.lower():
        sys.exit("PI_USER must be lowercase letters/digits/underscore")

    BUILD.mkdir(exist_ok=True)
    instance_id = f"gokart-pit-{int(time.time())}"
    pw_hash = sha512_crypt(env["PI_PASSWORD"])
    example = json.loads((PIT / "server" / "config.example.json").read_text())
    example.pop("_comment", None)
    example["password"] = env["PIT_WEB_PASSWORD"]
    pit_config = json.dumps(example, indent=2)

    tmpl = (HERE / "cloud-init" / "user-data.tmpl").read_text()
    user_data = (tmpl
                 .replace("{{HOSTNAME}}", q(hostname))
                 .replace("{{TIMEZONE}}", q(tz))
                 .replace("{{USER}}", q(user))
                 .replace("{{PASSWORD_HASH}}", q(pw_hash))
                 .replace("{{WIFI_SSID}}", shlex.quote(env["WIFI_SSID"]))
                 .replace("{{PIT_CONFIG_JSON}}", q(pit_config))
                 .replace("{{TAILSCALE_AUTHKEY}}", q(env.get("TAILSCALE_AUTHKEY", "")))
                 .replace("{{PAYLOAD_B64}}", payload_b64()))
    (BUILD / "user-data").write_text(user_data)

    net = (HERE / "cloud-init" / "network-config.tmpl").read_text()
    net = (net.replace("{{WIFI_COUNTRY}}", q(env["WIFI_COUNTRY"].upper()))
              .replace("{{ACCESS_POINTS}}", access_points(env)))
    (BUILD / "network-config").write_text(net)

    # dsmode: local matches the stock Pi OS meta-data: user-data and network-config
    # are applied in the local stage, before networking is brought up.
    (BUILD / "meta-data").write_text(
        f"dsmode: local\ninstance-id: {instance_id}\ninstance_id: {instance_id}\n"
        f"local-hostname: {hostname}\n")
    (BUILD / "pi-user").write_text(user + "\n")
    (BUILD / "pi-hostname").write_text(hostname + "\n")
    for f in ("user-data", "network-config"):
        os.chmod(BUILD / f, 0o600)
    n_ap = 2 if env.get("WIFI2_SSID") else 1
    print(f"generated sdcard/build/user-data, network-config, meta-data "
          f"(instance-id {instance_id}, user {user}, host {hostname}, {n_ap} Wi-Fi network(s), "
          f"tailscale {'on' if env.get('TAILSCALE_AUTHKEY') else 'skipped'})")


if __name__ == "__main__":
    main()
