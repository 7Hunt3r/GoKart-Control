"""Verify full on-air occupancy, not just packet start times, for all mixed-rate fleets."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
class RadioScheduleTests(unittest.TestCase):
    def test_all_mixed_rate_fleets(self):
        with tempfile.TemporaryDirectory(prefix="gokart-radio-test-") as temp:
            binary = Path(temp) / "schedule"
            subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-I", str(ROOT / "firmware/libraries/GoKartProtocol"),
                str(ROOT / "tests/firmware/radio_schedule_test.cpp"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
