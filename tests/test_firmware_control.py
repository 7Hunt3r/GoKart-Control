"""Compile the actual VESC parser/write transaction against captured hardware replies."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class FirmwareControlTests(unittest.TestCase):
    def test_native_vesc_write_guards(self):
        with tempfile.TemporaryDirectory(prefix='gokart-control-test-') as temp:
            binary = Path(temp) / 'control-test'
            subprocess.run([
                'clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-fsanitize=address,undefined',
                '-I', str(ROOT / 'tests/firmware'),
                '-I', str(ROOT / 'firmware/libraries/VescLink'),
                str(ROOT / 'tests/firmware/vesc_control_test.cpp'),
                str(ROOT / 'firmware/libraries/VescLink/VescLink.cpp'),
                '-o', str(binary)
            ], check=True)
            baseline = ROOT / 'hardware/vesc/baseline-kart/2026-11-05/raw'
            subprocess.run([str(binary), str(baseline / 'firmware.bin'),
                            str(baseline / 'motor_configuration.bin'),
                            str(baseline / 'telemetry.bin')], check=True)
