#!/usr/bin/env python3
"""Decode captured payloads offline using the pinned VESC Tool 6.00 schemas."""
import argparse
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
from capture import identity

DEFAULT_SCHEMA = Path(__file__).resolve().parents[2] / 'third_party/vesc_tool/6.00'


def crc32c(data):
    crc = 0xffffffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82f63b78 if crc & 1 else 0)
    return crc ^ 0xffffffff


class Reader:
    def __init__(self, data, offset=0):
        self.data, self.pos = data, offset

    def read(self, fmt):
        size = struct.calcsize('>' + fmt)
        if self.pos + size > len(self.data):
            raise ValueError('Truncated payload')
        value = struct.unpack_from('>' + fmt, self.data, self.pos)[0]
        self.pos += size
        return value

    def string(self):
        end = self.data.index(0, self.pos)
        value = self.data[self.pos:end].decode()
        self.pos = end + 1
        return value

    def auto_float(self):
        bits = self.read('I')
        exponent, fraction = (bits >> 23) & 255, bits & 0x7fffff
        value = 0.0 if exponent == fraction == 0 else math.ldexp(fraction/16777216.0+.5, exponent-126)
        return -value if bits & 0x80000000 else value


def decode_configuration(data, schema, command):
    if not data or data[0] != command:
        raise ValueError('Wrong response command')
    root = ET.parse(schema).getroot()
    params = {x.tag: x for x in root.find('Params')}
    order = [x.text for x in root.find('SerOrder')]
    signature_text = ''.join(name + params[name].findtext('type', '0') +
        params[name].findtext('vTx', '0') + ''.join(x.text or '' for x in params[name].findall('enumNames'))
        for name in order)
    reader = Reader(data, 1)
    signature = reader.read('I')
    expected = crc32c(signature_text.encode())
    if signature != expected:
        raise ValueError(f'Configuration schema signature mismatch: {signature:08x} != {expected:08x}')
    values, labels = {}, {}
    for name in order:
        param = params[name]
        kind, tx = int(param.findtext('type', '0')), int(param.findtext('vTx', '0'))
        if kind in (4, 5):
            value = reader.read('b')
        elif kind in (2, 6):
            value = reader.read({1:'B', 2:'b', 3:'H', 4:'h', 5:'I', 6:'i'}[1 if kind == 6 else tx])
        elif kind == 1:
            value = reader.auto_float() if tx == 9 else reader.read({7:'h', 8:'i'}[tx]) / float(param.findtext('vTxDoubleScale'))
        elif kind == 3:
            value = reader.string()
        else:
            raise ValueError(f'Unsupported field {name}: type {kind}, transport {tx}')
        values[name] = value
        enums = [x.text for x in param.findall('enumNames')]
        if enums and isinstance(value, int) and 0 <= value < len(enums):
            labels[name] = enums[value]
    if reader.pos != len(data):
        raise ValueError(f'Unexpected trailing bytes: consumed {reader.pos}/{len(data)}')
    return {'schema_version': '6.00', 'signature_hex': f'{signature:08x}',
            'signature_verified': True, 'bytes_consumed': reader.pos,
            'values': values, 'enum_labels': labels}


def decode_telemetry(data):
    # Full COMM_GET_VALUES payload for this captured 6.00 format, including status byte.
    if len(data) != 74 or data[0] != 4:
        raise ValueError('Expected the 74-byte firmware-6.00 telemetry format')
    reader, values = Reader(data, 1), {}
    fields = [('mosfet_temp_c','h',10), ('motor_temp_c','h',10),
        ('motor_current_a','i',100), ('battery_current_a','i',100), ('id_a','i',100),
        ('iq_a','i',100), ('duty','h',1000), ('erpm','i',1), ('input_voltage_v','h',10),
        ('amp_hours','i',10000), ('amp_hours_charged','i',10000), ('watt_hours','i',10000),
        ('watt_hours_charged','i',10000), ('tachometer','i',1), ('tachometer_absolute','i',1),
        ('fault_code','B',1), ('pid_position','i',1000000), ('controller_id','B',1),
        ('mosfet_temp_1_c','h',10), ('mosfet_temp_2_c','h',10), ('mosfet_temp_3_c','h',10),
        ('vd_v','i',1000), ('vq_v','i',1000)]
    for name, fmt, scale in fields:
        raw = reader.read(fmt)
        values[name] = raw / scale if scale != 1 else raw
    status = reader.read('B')
    values.update({'status_raw': status, 'timeout_active': bool(status & 1),
                   'kill_switch_active': bool(status & 2), 'bytes_consumed': reader.pos})
    return values


def decode_capture(directory, schema_dir=DEFAULT_SCHEMA):
    ident = identity((directory / 'firmware.bin').read_bytes())
    if ident['firmware'] != '6.00':
        raise ValueError('Only firmware 6.00 is supported')
    result = {'identity': ident}
    for name, schema, command in [('motor_configuration','parameters_mcconf.xml',14),
                                  ('app_configuration','parameters_appconf.xml',17)]:
        result[name] = decode_configuration((directory/(name+'.bin')).read_bytes(), schema_dir/schema, command)
    result['telemetry'] = decode_telemetry((directory/'telemetry.bin').read_bytes())
    # Preserve raw measurement while explicitly marking its physical validity.
    disabled = result['motor_configuration']['enum_labels'].get('m_motor_temp_sens_type') == 'Disabled'
    result['telemetry']['motor_temp_valid'] = False if disabled else None
    result['telemetry']['motor_temp_validity_reason'] = 'sensor_disabled_in_configuration' if disabled else 'sensor_wiring_not_verified'
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture_directory', type=Path)
    ap.add_argument('--schema-dir', type=Path, default=DEFAULT_SCHEMA)
    ap.add_argument('--out', type=Path, required=True, help='New output directory')
    args = ap.parse_args()
    decoded = decode_capture(args.capture_directory, args.schema_dir)
    args.out.mkdir(parents=True, exist_ok=False)
    for name, content in decoded.items():
        (args.out/(name+'.json')).write_text(json.dumps(content, indent=2)+'\n')
    print('Decoded identity, 189 motor fields, 187 app fields and telemetry; both schema signatures verified.')


if __name__ == '__main__':
    main()
