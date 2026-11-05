"""Offline checks against the real captured baseline and invalid wire data."""
import binascii
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/vesc'))
from capture import PacketParser, query_frame, identity
from decode import decode_configuration, decode_telemetry, crc32c

RAW = ROOT / 'hardware/vesc/baseline-kart/2026-11-05/raw'
SCHEMA = ROOT / 'third_party/vesc_tool/6.00'


def packet(payload):
    length = len(payload)
    header = bytes([2, length]) if length < 256 else b'\x03' + length.to_bytes(2, 'big')
    return header + payload + binascii.crc_hqx(payload, 0).to_bytes(2, 'big') + b'\x03'


class ProtocolTests(unittest.TestCase):
    def test_mutating_commands_rejected(self):
        for command in (1, 2, 3, 5, 6, 7, 8, 13, 16, 20, 29, 30, 48, 49, 255):
            with self.assertRaises(ValueError):
                query_frame(command)

    def test_known_crc_vectors(self):
        self.assertEqual(crc32c(b'123456789'), 0xe3069283)
        self.assertEqual(query_frame(0), bytes.fromhex('020100000003'))

    def test_fragmented_real_configuration(self):
        payload = (RAW / 'motor_configuration.bin').read_bytes()
        parser, result = PacketParser(), []
        for byte in packet(payload):
            result.extend(parser.feed(bytes([byte])))
        self.assertEqual(result, [payload])

    def test_multiple_packets_and_noise(self):
        payload = (RAW / 'firmware.bin').read_bytes()
        self.assertEqual(PacketParser().feed(b'\xff\xff' + packet(payload)*2), [payload, payload])

    def test_bad_crc_and_truncated_packets_are_not_accepted(self):
        wire = bytearray(packet((RAW / 'firmware.bin').read_bytes()))
        wire[-2] ^= 1
        self.assertEqual(PacketParser().feed(wire), [])
        self.assertEqual(PacketParser().feed(bytes(wire[:10])), [])

    def test_baseline_identity(self):
        data = identity((RAW / 'firmware.bin').read_bytes())
        self.assertEqual((data['firmware'], data['hardware'], data['firmware_name']),
                         ('6.00', '75_100_V2', 'no_hw_limits'))

    def test_full_schema_decode_and_measured_limits(self):
        for name, schema, command, count in [('motor_configuration','parameters_mcconf.xml',14,189),
                                             ('app_configuration','parameters_appconf.xml',17,187)]:
            raw = (RAW / (name+'.bin')).read_bytes()
            decoded = decode_configuration(raw, SCHEMA/schema, command)
            self.assertEqual(len(decoded['values']), count)
            self.assertEqual(decoded['bytes_consumed'], len(raw))
            if command == 14:
                self.assertEqual(decoded['values']['l_in_current_max'], 100)
                self.assertEqual(decoded['values']['l_current_max'], 140)
                self.assertEqual(decoded['enum_labels']['m_motor_temp_sens_type'], 'Disabled')
            for invalid in (raw[:-1], raw+b'\x00', raw[:1]+b'\x00'*4+raw[5:]):
                with self.assertRaises(ValueError):
                    decode_configuration(invalid, SCHEMA/schema, command)

    def test_telemetry_snapshot(self):
        d = decode_telemetry((RAW/'telemetry.bin').read_bytes())
        self.assertEqual(d['input_voltage_v'], 76.4)
        self.assertEqual(d['fault_code'], 0)
        self.assertEqual(d['controller_id'], 69)
        self.assertEqual(d['bytes_consumed'], 74)


if __name__ == '__main__':
    unittest.main()
