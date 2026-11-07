"""Pi power/thermal health for the pit server: `vcgencmd get_throttled` + CPU temperature.

Sampled every 10 s on a Raspberry Pi; elsewhere (Mac, CI) `available` is False and the
pages show nothing. Bits of get_throttled (Raspberry Pi documentation):
  0 under-voltage now          16 under-voltage has occurred since boot
  1 ARM frequency capped now   17 frequency capping has occurred
  2 currently throttled        18 throttling has occurred
  3 soft temperature limit     19 soft temperature limit has occurred
Standard library only.
"""
from __future__ import annotations

import shutil
import subprocess
import time

PERIOD_S = 10.0
TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"
# status, most severe first; what the pages show
OK, UNDERVOLT, THROTTLED, HAD_UNDERVOLT, UNAVAILABLE = "ok", "undervolt", "throttled", "had_undervolt", "unavailable"


def parse_throttled(text: str):
    """'throttled=0x50005' -> 0x50005; None when unparseable."""
    if not text:
        return None
    s = text.strip()
    if "=" in s:
        s = s.split("=", 1)[1].strip()
    try:
        return int(s, 0)
    except ValueError:
        return None


def flags(v: int) -> dict:
    return {
        "undervolt_now": bool(v & 0x1),
        "freq_capped_now": bool(v & 0x2),
        "throttled_now": bool(v & 0x4),
        "soft_temp_limit_now": bool(v & 0x8),
        "undervolt_since_boot": bool(v & 0x10000),
        "freq_capped_since_boot": bool(v & 0x20000),
        "throttled_since_boot": bool(v & 0x40000),
        "soft_temp_limit_since_boot": bool(v & 0x80000),
    }


def status_of(v) -> str:
    """One word for the pages: undervolt > throttled > had_undervolt > ok."""
    if v is None:
        return UNAVAILABLE
    if v & 0x1:
        return UNDERVOLT
    if v & 0xE:
        return THROTTLED
    if v & 0x10000:
        return HAD_UNDERVOLT
    return OK


def read_temp_c(path=TEMP_PATH):
    try:
        with open(path) as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def vcgencmd_reader():
    """Returns a no-arg function giving the raw get_throttled text, or None when not on a Pi."""
    exe = shutil.which("vcgencmd")
    if not exe:
        return None

    def read():
        r = subprocess.run([exe, "get_throttled"], capture_output=True, text=True, timeout=5)
        return r.stdout if r.returncode == 0 else ""
    return read


class Health:
    """Holds the latest sample. sample() is called by the server's thread every PERIOD_S;
    on_change(prev_status, snapshot) fires on every status change (and on the first sample
    when something is already wrong)."""

    def __init__(self, reader="auto", temp_reader=read_temp_c, on_change=None, clock=time.monotonic):
        self.reader = vcgencmd_reader() if reader == "auto" else reader
        self.temp_reader = temp_reader
        self.on_change = on_change
        self.clock = clock
        self.value = None
        self.temp_c = None
        self.status = UNAVAILABLE if self.reader is None else None
        self.sampled_at = None
        self.errors = 0

    @property
    def available(self):
        return self.reader is not None

    def sample(self):
        self.temp_c = self.temp_reader() if self.temp_reader else None
        if self.reader is None:
            self.sampled_at = self.clock()
            return self.snapshot()
        try:
            v = parse_throttled(self.reader())
        except (OSError, subprocess.SubprocessError):
            v = None
        if v is None:
            self.errors += 1
        prev = self.status
        self.value = v
        self.status = status_of(v)
        self.sampled_at = self.clock()
        snap = self.snapshot()
        if self.on_change and self.status != prev and not (prev is None and self.status == OK):
            self.on_change(prev, snap)
        return snap

    def snapshot(self):
        v = self.value
        return {
            "available": self.available,
            "status": self.status or UNAVAILABLE,
            "throttled": None if v is None else hex(v),
            "flags": None if v is None else flags(v),
            "temp_c": self.temp_c,
            "age_s": None if self.sampled_at is None else round(self.clock() - self.sampled_at, 1),
        }
