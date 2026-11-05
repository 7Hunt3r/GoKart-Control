"""Bench packet decoding, including absent/stale measurements and signed values."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('gkc_radio',Path(__file__).resolve().parents[1]/'tools/heltec/decode_radio.py')
radio=importlib.util.module_from_spec(spec);spec.loader.exec_module(radio)

class RadioPacketTests(unittest.TestCase):
    def packet(self,flags=3):
        return radio.FORMAT.pack(b'GK',1,0,123,4,5000,20,flags,0,764,-105,1400,-3000,223,410000000,-730000000,123,7,125,200)
    def test_signed_values_and_units(self):
        d=radio.decode(self.packet())
        self.assertEqual(d['battery_current_a'],-10.5)
        self.assertEqual(d['motor_current_a'],140)
        self.assertEqual(d['erpm'],-3000)
        self.assertEqual(d['longitude_deg'],-73)
        self.assertEqual(d['gps_speed_mps'],1.23)
        self.assertTrue(d['bench_uncommissioned'])
    def test_invalid_flags_do_not_turn_zeros_into_measurements(self):
        d=radio.decode(self.packet(flags=0))
        for key in ('battery_voltage_v','fault_code','latitude_deg','longitude_deg','motor_temperature_c'):
            self.assertIsNone(d[key])
    def test_unknown_header_and_size_rejected(self):
        good=self.packet()
        for bad in (good[:-1],good+b'\0',b'NO'+good[2:],good[:2]+b'\x02'+good[3:]):
            with self.assertRaises(ValueError):radio.decode(bad)
