"""Shared receiver-contract checks against bytes produced by Eamon's C++ header."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('gkc_radio',ROOT/'tools/heltec/decode_radio.py')
radio=importlib.util.module_from_spec(spec);spec.loader.exec_module(radio)

class RadioPacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('clang++') or shutil.which('g++')
        if not compiler:raise unittest.SkipTest('C++ compiler needed for shared wire fixture')
        with tempfile.TemporaryDirectory() as directory:
            exe=Path(directory)/'radio-fixture'
            subprocess.run([compiler,'-std=c++17','-I',str(ROOT/'firmware/libraries/GoKartProtocol'),str(ROOT/'firmware/heltec/test/radio_fixture.cpp'),'-o',str(exe)],check=True,capture_output=True)
            cls.packet=bytes.fromhex(subprocess.check_output([str(exe)],text=True).strip())
    def test_cpp_receiver_contract_units_and_offsets(self):
        d=radio.decode(self.packet)
        self.assertEqual(len(self.packet),46);self.assertEqual(d['kart_id'],1)
        self.assertEqual(d['sequence'],17);self.assertEqual(d['battery_voltage_v'],76.2)
        self.assertEqual(d['battery_current_a'],-10.5);self.assertEqual(d['motor_current_a'],140)
        self.assertEqual(d['erpm'],-3000);self.assertEqual(d['longitude_deg'],-73)
        self.assertEqual(d['gps_speed_mps'],1);self.assertEqual(d['heading_deg'],90)
        self.assertIsNone(d['current_scale']);self.assertFalse(d['writes_enabled'])
    def test_stale_values_are_unavailable(self):
        b=bytearray(self.packet);b[19]=0;d=radio.decode(b)
        for k in ('battery_voltage_v','fault_code','latitude_deg','longitude_deg','motor_temperature_c','gps_speed_mps'):
            self.assertIsNone(d[k])
    def test_header_and_length_rejection(self):
        for bad in (self.packet[:-1],self.packet+b'\0',b'GK'+self.packet[2:],b'\2'+self.packet[1:],self.packet[:3]+b'\0'+self.packet[4:]):
            with self.assertRaises(ValueError):radio.decode(bad)
