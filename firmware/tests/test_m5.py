"""M5 v1.5 native state/protocol tests and pit course export validation. No hardware."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('export_course', ROOT/'tools/m5/export_course.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

class M5Tests(unittest.TestCase):
    def test_native_state_protocol(self):
        compiler = shutil.which('c++')
        self.assertIsNotNone(compiler, 'C++ compiler required for M5 regression checks')
        candidates = [Path(os.environ.get('ARDUINOJSON_DIR', '/nonexistent')),
                      Path.home()/'Arduino/libraries/ArduinoJson/src',
                      Path.home()/'Documents/Arduino/libraries/ArduinoJson/src']
        include = next((p for p in candidates if (p/'ArduinoJson.h').exists()), None)
        self.assertIsNotNone(include, 'Set ARDUINOJSON_DIR to the installed ArduinoJson src directory (see M5 README)')
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp)/'test'
            subprocess.run([compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-g', '-I'+str(include), '-I'+str(ROOT/'firmware/libraries/M5DisplayLink'),
                            '-I'+str(ROOT/'firmware/libraries/GoKartProtocol'), '-I'+str(ROOT/'firmware/m5-core2'),
                            str(ROOT/'firmware/m5-core2/tests/core_test.cpp'), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True, timeout=30)

    def test_course_from_pit_contract(self):
        zones = json.loads((ROOT/'pit/server/map/zones.json').read_text())
        out = exporter.export(zones, 'Shop test course')
        self.assertEqual(out['points'], zones['track']['points'])
        self.assertEqual(out['closed'], zones['track']['closed'])
        self.assertNotIn('zones', out)  # no game engine on M5 v1.5

    def test_bad_course(self):
        for points in ([], [[0,0]], [[91,0],[0,0]], [[True,0],[0,0]], [[float('nan'),0],[0,0]], [[0,0]]*129):
            with self.subTest(points=str(points)[:50]), self.assertRaises(ValueError):
                exporter.export({'track':{'points':points}}, 'bad')
        with self.assertRaises(ValueError):exporter.export({}, 'missing')

    def test_no_automatic_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'course.json'
            cmd=['python3', '-I', str(ROOT/'tools/m5/export_course.py'), '--zones',
                 str(ROOT/'pit/server/map/zones.json'), '--name', 'Test', '--out', str(path)]
            subprocess.run(cmd, check=True, capture_output=True)
            before=path.read_bytes()
            self.assertNotEqual(subprocess.run(cmd,capture_output=True).returncode, 0)
            self.assertEqual(path.read_bytes(), before)

if __name__ == '__main__':unittest.main()
