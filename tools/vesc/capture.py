#!/usr/bin/env python3
"""Capture VESC 6.00 runtime state over macOS/Linux serial; read commands only."""
import argparse
import binascii
import datetime
import hashlib
import json
import os
from pathlib import Path
import select
import termios
import time
import tty

READ_COMMANDS = {0: 'firmware', 4: 'telemetry', 14: 'motor_configuration', 17: 'app_configuration'}
MAX_PAYLOAD = 8192


def query_frame(command):
    if command not in READ_COMMANDS:
        raise ValueError('Only firmware, telemetry, motor-config and app-config reads are allowed')
    payload = bytes([command])
    return b'\x02\x01' + payload + binascii.crc_hqx(payload, 0).to_bytes(2, 'big') + b'\x03'


class PacketParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        packets = []
        while len(self.buffer) >= 2:
            header = self.buffer[0]
            if header not in (2, 3, 4):
                del self.buffer[0]
                continue
            if len(self.buffer) < header:
                break
            size = int.from_bytes(self.buffer[1:header], 'big')
            if not 1 <= size <= MAX_PAYLOAD:
                del self.buffer[0]
                continue
            total = header + size + 3
            if len(self.buffer) < total:
                break
            payload = bytes(self.buffer[header:header + size])
            crc = int.from_bytes(self.buffer[header + size:header + size + 2], 'big')
            if self.buffer[total - 1] != 3 or crc != binascii.crc_hqx(payload, 0):
                del self.buffer[0]
                continue
            packets.append(payload)
            del self.buffer[:total]
        return packets


def receive(fd, command, timeout=3):
    parser = PacketParser()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], max(0, min(.2, deadline-time.monotonic())))
        if not ready:
            continue
        try:
            chunk = os.read(fd, MAX_PAYLOAD)
        except BlockingIOError:
            continue
        for payload in parser.feed(chunk):
            if payload[0] == command:
                return payload
    raise TimeoutError(f'No CRC-valid reply for read command {command}; no write commands sent')


def identity(payload):
    if len(payload) < 4 or payload[0] != 0:
        raise ValueError('Invalid identity response')
    end = payload.index(0, 3)
    pos = end + 1
    if len(payload) < pos + 20:
        raise ValueError('Identity reply lacks expected 6.00 fields')
    result = {'firmware': f'{payload[1]}.{payload[2]:02d}', 'hardware': payload[3:end].decode(),
              'uuid': payload[pos:pos+12].hex()}
    pos += 12
    for field in ('paired', 'test_version', 'hardware_type', 'custom_config_count',
                  'has_phase_filters', 'qml_hardware', 'qml_app', 'nrf_flags'):
        result[field] = payload[pos]
        pos += 1
    if pos < len(payload):
        end = payload.index(0, pos)
        result['firmware_name'] = payload[pos:end].decode()
        pos = end + 1
    result['remaining_hex'] = payload[pos:].hex()
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', required=True, help='Explicit VESC serial device; close VESC Tool first')
    ap.add_argument('--out', required=True, type=Path, help='New capture directory; existing paths rejected')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    manifest = {'started_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'transport': 'usb_serial', 'port': args.port,
                'scope': 'current_runtime_not_verified_saved_flash',
                'capture_complete': False, 'queries': []}
    fd = None
    old = None
    try:
        fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        cfg = termios.tcgetattr(fd)
        cfg[2] = (cfg[2] | termios.CLOCAL | termios.CREAD) & ~termios.HUPCL
        cfg[4] = cfg[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, cfg)
        for command, name in READ_COMMANDS.items():
            frame = query_frame(command)
            # These frames are six bytes; fail rather than replay after a partial write.
            if os.write(fd, frame) != len(frame):
                raise IOError('Partial serial query write')
            payload = receive(fd, command)
            (args.out / (name+'.bin')).write_bytes(payload)
            manifest['queries'].append({'command': command, 'name': name, 'crc_verified': True,
                'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'payload_bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
            if command == 0:
                manifest['identity'] = identity(payload)
                if manifest['identity']['firmware'] != '6.00':
                    raise ValueError('Only firmware 6.00 is currently supported; stopped after identity')
            print(f'Read {name}: {len(payload)} bytes, CRC verified', flush=True)
            time.sleep(.1)
        manifest['capture_complete'] = True
    finally:
        if fd is not None:
            try:
                if old is not None:
                    termios.tcsetattr(fd, termios.TCSANOW, old)
            finally:
                os.close(fd)
        (args.out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')


if __name__ == '__main__':
    main()
