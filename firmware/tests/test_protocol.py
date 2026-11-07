#!/usr/bin/env python3
"""Host-side checks of firmware/libraries/GoKartProtocol/GoKartProtocol.h.

Dependency-free (standard library only). Run from anywhere:

    python3 -I firmware/tests/test_protocol.py

What it checks:
  1. The packed wire structs parsed out of the header have the agreed sizes (47/27/14/16) and the
     field offsets the pit/kart decoders and this file's hand-built layouts expect.
  2. Hand-built little-endian packets decode field by field against those offsets.
  3. The beacon position nibble helpers (Python mirror of gkBeaconPos/gkBeaconSetPos).
  4. If a host C++ compiler is available (c++/clang++/g++): the header itself is compiled and run
     to confirm sizeof/offsetof, gkValidate (old and new lengths, relay bit), the nibble helpers,
     gkAirtimeUs, the slot plan (no two scheduled transmissions overlap at 1 Hz / 2 Hz), and that
     gkTelemetryToJson emits exactly the documented field order. Skipped (reported) otherwise.
  5. The field names the Pi readers use (pit/server/pitcore.py, kart/server/kartcore.py) exist in
     the firmware's JSON (read as text; the Python modules are not imported).
"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.dirname(HERE)
REPO = os.path.dirname(FW)
HEADER = os.path.join(FW, "libraries", "GoKartProtocol", "GoKartProtocol.h")

CTYPES = {"uint8_t": "B", "int8_t": "b", "uint16_t": "H", "int16_t": "h",
          "uint32_t": "I", "int32_t": "i", "GkHeader": "6s"}

# Field order of gkTelemetryToJson, as documented in base-tbeam-s3/SERIAL_PROTOCOL.md ("tele").
TELE_JSON_FIELDS = ["t", "kart", "seq", "lat", "lon", "fix", "gps_age_s", "sats", "gps_kph", "heading",
                    "volts", "motor_a", "batt_a", "erpm", "duty", "esc_c", "fault", "mode_req",
                    "mode_actual", "scale", "stopped", "vesc_link", "mode_confirmed", "pps", "wifi",
                    "writes", "last_cmd_seq", "cmd_result", "ah", "uptime_s", "base_rssi", "slow_mph",
                    "rssi", "snr", "rx_ms", "relayed"]
# Extra keys the kart adds to its own USB "self" line (kart-heltec-v4.ino usbReport()).
SELF_EXTRA_FIELDS = ["hdop", "fix_age_ms", "gps_hz", "base_lat", "base_lon", "base_fix_age_s",
                     "race_pos", "race_state"]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_structs(src):
    """{name: [(field, ctype, count)]} for every struct inside #pragma pack(push, 1)."""
    m = re.search(r"#pragma pack\(push, 1\)(.*?)#pragma pack\(pop\)", src, re.S)
    assert m, "packed block not found"
    out = {}
    for sm in re.finditer(r"struct (\w+) \{(.*?)\n\};", m.group(1), re.S):
        fields = []
        for line in sm.group(2).split("\n"):
            line = line.split("//")[0].strip()
            if not line:
                continue
            fm = re.match(r"(\w+)\s+(\w+)(?:\[(\d+)\])?;", line)
            assert fm, f"unparsed field line in {sm.group(1)}: {line!r}"
            fields.append((fm.group(2), fm.group(1), int(fm.group(3) or 1)))
        out[sm.group(1)] = fields
    return out


def layout(fields):
    """[(name, offset, struct-format)] and total size, little-endian packed."""
    off, res = 0, []
    for name, ctype, count in fields:
        fmt = CTYPES[ctype]
        if count > 1:
            fmt = f"{count}{fmt}" if fmt != "B" else f"{count}s"
        size = struct.calcsize("<" + fmt)
        res.append((name, off, fmt))
        off += size
    return res, off


def decode(fields, buf):
    lay, size = layout(fields)
    assert len(buf) == size
    return {name: struct.unpack_from("<" + fmt, buf, off)[0] for name, off, fmt in lay}


def beacon_pos(pos4, kart):
    if not 1 <= kart <= 8:
        return 0
    return (pos4[(kart - 1) // 2] >> (((kart - 1) % 2) * 4)) & 0xF


def beacon_set_pos(pos4, kart, p):
    if not 1 <= kart <= 8:
        return pos4
    i, sh = (kart - 1) // 2, ((kart - 1) % 2) * 4
    pos4 = list(pos4)
    pos4[i] = (pos4[i] & ~(0xF << sh) & 0xFF) | ((p & 0xF) << sh)
    return pos4


def header_bytes(version, fleet, typ, src, dst, seq):
    return bytes([version, fleet, typ, src, dst, seq])


class StructLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.structs = parse_structs(read(HEADER))

    def test_sizes(self):
        sizes = {n: layout(f)[1] for n, f in self.structs.items()}
        self.assertEqual(sizes["GkHeader"], 6)
        self.assertEqual(sizes["GkTelemetry"], 47)
        self.assertEqual(sizes["GkBaseInfo"], 27)
        self.assertEqual(sizes["GkCommandMsg"], 14)
        self.assertEqual(sizes["GkRepeaterInfo"], 16)

    def test_telemetry_offsets(self):
        offs = {n: o for n, o, _ in layout(self.structs["GkTelemetry"])[0]}
        expect = {"h": 0, "lat_e7": 6, "lon_e7": 10, "gps_kph_x10": 14, "heading_x10": 16, "sats": 18,
                  "flags": 19, "volts_x10": 20, "motor_a_x10": 22, "batt_a_x10": 24, "erpm": 26,
                  "duty_pct": 30, "esc_temp_x10": 31, "fault": 33, "mode_req": 34, "mode_actual": 35,
                  "last_cmd_seq": 36, "cmd_result": 37, "ah_x100": 38, "uptime_s": 40, "base_rssi": 42,
                  "gps_age_s": 43, "scale_x1000": 44, "slow_mph": 46}
        self.assertEqual(offs, expect)

    def test_decode_telemetry(self):
        buf = (header_bytes(0x81, 0x4B, 1, 3, 0xFF, 200)
               + struct.pack("<iiHHBBHhhibhBBBBBHHbBHB", 410939900, -734163700, 523, 1805, 9, 0x8F,
                             784, -125, 312, -4200, -37, 245, 0, 2, 2, 17, 2, 1234, 65000, -71, 0, 571, 12))
        d = decode(self.structs["GkTelemetry"], buf)
        self.assertEqual(d["h"][3], 3)
        self.assertEqual(d["h"][0] & 0x7F, 1)                  # version with the relay bit masked
        self.assertTrue(d["h"][0] & 0x80)
        self.assertEqual((d["lat_e7"], d["lon_e7"]), (410939900, -734163700))
        self.assertEqual((d["motor_a_x10"], d["batt_a_x10"], d["erpm"]), (-125, 312, -4200))
        self.assertEqual((d["duty_pct"], d["esc_temp_x10"]), (-37, 245))
        self.assertEqual((d["last_cmd_seq"], d["cmd_result"], d["ah_x100"]), (17, 2, 1234))
        self.assertEqual((d["base_rssi"], d["scale_x1000"], d["slow_mph"]), (-71, 571, 12))

    def test_decode_baseinfo(self):
        pos = beacon_set_pos(beacon_set_pos([0, 0, 0, 0], 1, 2), 8, 1)
        buf = (header_bytes(1, 0x4B, 3, 0, 0xFF, 9)
               + struct.pack("<iiIHBBB4s", 410940000, -734164000, 1793990400, 907, 1, 3, 2, bytes(pos)))
        d = decode(self.structs["GkBaseInfo"], buf)
        self.assertEqual((d["utc_s"], d["phase_ms"], d["sync_src"], d["karts_online"], d["race_state"]),
                         (1793990400, 907, 1, 3, 2))
        self.assertEqual([beacon_pos(d["pos"], k) for k in range(1, 9)], [2, 0, 0, 0, 0, 0, 0, 1])

    def test_decode_command(self):
        buf = header_bytes(1, 0x4B, 2, 0, 4, 42) + struct.pack("<BBHI", 7, 5, 10, 1793990400)
        d = decode(self.structs["GkCommandMsg"], buf)
        self.assertEqual((d["cmd"], d["arg"], d["ttl_s"], d["issued_utc_s"]), (7, 5, 10, 1793990400))

    def test_decode_repeater(self):
        buf = header_bytes(0x81, 0x4B, 4, 0xF0, 0, 3) + struct.pack("<HHHbBH", 65535, 100, 4012, -60, 0b10000010, 77)
        d = decode(self.structs["GkRepeaterInfo"], buf)
        self.assertEqual((d["heard"], d["relayed"], d["batt_mv"], d["pit_rssi"], d["karts_mask"], d["uptime_min"]),
                         (65535, 100, 4012, -60, 0x82, 77))

    def test_nibbles(self):
        pos = [0, 0, 0, 0]
        for k in range(1, 9):
            pos = beacon_set_pos(pos, k, 9 - k)
        self.assertEqual([beacon_pos(pos, k) for k in range(1, 9)], [8, 7, 6, 5, 4, 3, 2, 1])
        pos = beacon_set_pos(pos, 3, 15)
        self.assertEqual([beacon_pos(pos, k) for k in range(1, 9)], [8, 7, 15, 5, 4, 3, 2, 1])
        self.assertEqual(beacon_pos(pos, 0), 0)
        self.assertEqual(beacon_pos(pos, 9), 0)


HARNESS = r'''
#include <cstdio>
#include <cstddef>
#include <cstring>
#include "GoKartProtocol.h"
static void pr(const char* k, long v, bool last = false) { printf("\"%s\":%ld%s", k, v, last ? "" : ","); }
int main() {
  printf("{");
  pr("sz_tele", sizeof(GkTelemetry)); pr("sz_base", sizeof(GkBaseInfo)); pr("sz_cmd", sizeof(GkCommandMsg));
  pr("sz_rep", sizeof(GkRepeaterInfo));
  pr("off_slow", offsetof(GkTelemetry, slow_mph)); pr("off_scale", offsetof(GkTelemetry, scale_x1000));
  pr("off_pos", offsetof(GkBaseInfo, pos)); pr("off_issued", offsetof(GkCommandMsg, issued_utc_s));
  pr("air47", gkAirtimeUs(47)); pr("air46", gkAirtimeUs(46)); pr("air27", gkAirtimeUs(27)); pr("air22", gkAirtimeUs(22));
  pr("air16", gkAirtimeUs(16)); pr("air14", gkAirtimeUs(14));
  uint8_t b[47] = {0}; GkTelemetry t; memset(&t, 0, sizeof t);
  gkFillHeader(t.h, GK_MSG_TELEMETRY, 2, GK_ADDR_ALL, 5); memcpy(b, &t, 47);
  pr("val47", gkValidate(b, 47)); pr("val46", gkValidate(b, 46)); pr("val45", gkValidate(b, 45));
  b[0] |= GK_HDR_RELAY_BIT; pr("val47relay", gkValidate(b, 47)); b[0] = 2; pr("valver2", gkValidate(b, 47));
  b[0] = 1; b[1] = 0x4C; pr("valfleet", gkValidate(b, 47));
  GkBaseInfo bi; memset(&bi, 0, sizeof bi); gkFillHeader(bi.h, GK_MSG_BASE_INFO, 0, GK_ADDR_ALL, 1);
  pr("valb27", gkValidate((uint8_t*)&bi, 27)); pr("valb22", gkValidate((uint8_t*)&bi, 22)); pr("valb26", gkValidate((uint8_t*)&bi, 26));
  for (int k = 1; k <= 8; k++) gkBeaconSetPos(bi, k, (uint8_t)(k * 2 - 1));
  printf("\"pos\":["); for (int k = 1; k <= 8; k++) printf("%u%s", gkBeaconPos(bi, k), k < 8 ? "," : "],");
  printf("\"posraw\":[%u,%u,%u,%u],", bi.pos[0], bi.pos[1], bi.pos[2], bi.pos[3]);
  // busy check: kart 2 at 2 Hz occupies 42..58+23 ms and 542..; kart 1 opens at 992
  pr("busy_k2_50", gkKartBusy(50000, 1000, 2, 2, false)); pr("busy_k2_545", gkKartBusy(545000, 1000, 2, 2, false));
  pr("busy_k2_100", gkKartBusy(100000, 1000, 2, 2, false)); pr("busy_k1_995", gkKartBusy(995000, 1000, 1, 1, false));
  pr("busy_k1_wrap", gkKartBusy(990000, 5000, 1, 1, false)); pr("busy_beacon_910", gkBeaconBusy(910000, 1000));
  pr("busy_beacon_450", gkBeaconBusy(450000, 1000));
  GkTelemetry j; memset(&j, 0, sizeof j); gkFillHeader(j.h, GK_MSG_TELEMETRY, 3, GK_ADDR_ALL, 7);
  j.flags = GK_FLAG_GPS_FIX | GK_FLAG_STOPPED; j.lat_e7 = 410939900; j.lon_e7 = -734163700; j.mode_req = 255; j.mode_actual = 1;
  j.scale_x1000 = 714; j.slow_mph = 5; j.h.version |= GK_HDR_RELAY_BIT;
  char out[700]; gkTelemetryToJson(out, sizeof out, j, "tele", -80.0f, 9.5f, 1234);
  printf("\"json\":%s,", out);
  char esc[16]; gkJsonStr(esc, sizeof esc, "a\"b\\c\x01\xff-long-tail");
  printf("\"esc\":\"%s\"}\n", esc);
  return 0;
}
'''


def find_cxx():
    for c in ("c++", "clang++", "g++"):
        p = shutil.which(c)
        if p:
            return p
    return None


@unittest.skipUnless(find_cxx(), "no host C++ compiler")
class CompiledHeader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = tempfile.mkdtemp(prefix="gkproto-")
        src, exe = os.path.join(d, "h.cpp"), os.path.join(d, "h")
        with open(src, "w") as f:
            f.write(HARNESS)
        inc = os.path.dirname(HEADER)
        subprocess.run([find_cxx(), "-std=c++17", "-Wall", "-I", inc, src, "-o", exe], check=True)
        out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
        cls.r = json.loads(out)
        shutil.rmtree(d, ignore_errors=True)

    def test_sizes_offsets(self):
        r = self.r
        self.assertEqual((r["sz_tele"], r["sz_base"], r["sz_cmd"], r["sz_rep"]), (47, 27, 14, 16))
        self.assertEqual((r["off_slow"], r["off_scale"], r["off_pos"], r["off_issued"]), (46, 44, 23, 10))

    def test_validate(self):
        r = self.r
        self.assertEqual((r["val47"], r["val46"], r["val45"], r["val47relay"]), (1, 1, 0, 1))
        self.assertEqual((r["valver2"], r["valfleet"]), (0, 0))
        self.assertEqual((r["valb27"], r["valb22"], r["valb26"]), (3, 3, 0))

    def test_nibbles_match_python(self):
        r = self.r
        self.assertEqual(r["pos"], [1, 3, 5, 7, 9, 11, 13, 15])
        self.assertEqual([beacon_pos(r["posraw"], k) for k in range(1, 9)], r["pos"])

    def test_airtime(self):
        r = self.r
        self.assertEqual((r["air47"], r["air46"], r["air27"], r["air22"], r["air16"], r["air14"]),
                         (23104, 23104, 16704, 14144, 12864, 11584))

    def test_busy(self):
        r = self.r
        self.assertEqual((r["busy_k2_50"], r["busy_k2_545"], r["busy_k2_100"]), (1, 1, 0))
        self.assertEqual((r["busy_k1_995"], r["busy_k1_wrap"]), (1, 1))
        self.assertEqual((r["busy_beacon_910"], r["busy_beacon_450"]), (1, 0))

    def test_slot_plan_no_overlap(self):
        """Every kart at the same rate, worst-case late start, beacon in the tail: nothing overlaps.
        At 1 Hz this includes repeater copies; at 2 Hz it does not (documented limitation)."""
        air = self.r["air47"]
        late, guard, relay = 16000, 8000, 25000
        for rate, slot, with_relay in ((1, 100000, True), (2, 50000, False)):
            iv = []
            for k in range(1, 9):
                for n in range(rate):
                    s = (n * (1000000 // rate) + (k - 1) * slot - guard) % 1000000
                    e = s + late + air + ((relay + air) if with_relay else 0)
                    iv.append((s, e, f"kart{k}r{n}"))
            b_air = self.r["air27"]
            iv.append((905000, 905000 + 8000 + 2 * b_air + relay + 3000 + self.r["air16"], "beacon"))
            iv = sorted((s if s < 900000 or e <= 1000000 else s - 1000000, e if s < 900000 or e <= 1000000 else e - 1000000, n)
                        for s, e, n in iv)
            for (s1, e1, n1), (s2, e2, n2) in zip(iv, iv[1:]):
                self.assertLessEqual(e1, s2, f"rate {rate}: {n1} ends {e1} after {n2} starts {s2}")

    def test_json_field_order(self):
        j = self.r["json"]
        self.assertEqual(list(j.keys()), TELE_JSON_FIELDS)
        self.assertEqual((j["kart"], j["seq"], j["mode_req"], j["mode_actual"], j["scale"], j["slow_mph"]),
                         (3, 7, None, 1, 0.714, 5))
        self.assertIs(j["relayed"], True)
        self.assertIs(j["stopped"], True)
        self.assertAlmostEqual(j["lat"], 41.09399)

    def test_json_escape(self):
        # quote and backslash escaped, control/non-ASCII bytes dropped, truncated to the 16-byte buffer
        self.assertEqual(self.r["esc"], 'a"b\\c-long-ta')


class ReaderFieldNames(unittest.TestCase):
    """Names the Pi readers ask for must be ones the firmware writes."""

    def _tuple(self, src, name):
        m = re.search(name + r"\s*=\s*\((.*?)\)", src, re.S)
        self.assertIsNotNone(m, name)
        return re.findall(r'"(\w+)"', m.group(1))

    def test_pit_reader(self):
        src = read(os.path.join(REPO, "pit", "server", "pitcore.py"))
        for name in ("VESC_FIELDS", "STATUS_FIELDS"):
            missing = set(self._tuple(src, name)) - set(TELE_JSON_FIELDS)
            self.assertFalse(missing, f"pitcore {name} reads fields the firmware never sends: {missing}")

    def test_kart_reader(self):
        src = read(os.path.join(REPO, "kart", "server", "kartcore.py"))
        for name in ("VESC_FIELDS", "STATUS_FIELDS"):
            missing = set(self._tuple(src, name)) - set(TELE_JSON_FIELDS + SELF_EXTRA_FIELDS)
            self.assertFalse(missing, f"kartcore {name} reads fields the firmware never sends: {missing}")

    def test_documented(self):
        doc = read(os.path.join(FW, "base-tbeam-s3", "SERIAL_PROTOCOL.md"))
        for f in TELE_JSON_FIELDS[1:]:
            self.assertIn(f"`{f}`", doc, f"tele field {f} not documented in SERIAL_PROTOCOL.md")

    def test_kart_sketch_extras(self):
        src = read(os.path.join(FW, "kart-heltec-v4", "kart-heltec-v4.ino"))
        for f in SELF_EXTRA_FIELDS:
            self.assertIn('\\"%s\\":' % f, src, f"self extra {f} missing from usbReport()")


if __name__ == "__main__":
    unittest.main(verbosity=2)
