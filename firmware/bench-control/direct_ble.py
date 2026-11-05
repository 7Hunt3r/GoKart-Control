#!/usr/bin/env python3
"""Removable VESC Tool-style Bluetooth bench runner; read-only by default."""
import argparse
import asyncio
import binascii
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/vesc'))
from capture import PacketParser, identity
from decode import decode_telemetry

RX = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
TX = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'
UUID = '2c004c000951343037353739'
SNAPSHOT = ROOT / 'hardware/vesc/baseline-kart/2026-11-05-bench-preflight/raw'


def emit(value):
    print(json.dumps(value), flush=True)


def frame(payload):
    return (bytes([2, len(payload)]) + payload +
            binascii.crc_hqx(payload, 0).to_bytes(2, 'big') + b'\x03')


class Link:
    def __init__(self, client):
        self.client = client
        self.parser = PacketParser()
        self.waiters = {}
        self.positive = 0
        self.sample_started = 0.0

    def notify(self, _, data):
        for payload in self.parser.feed(data):
            future = self.waiters.get(payload[0])
            if future and not future.done():
                future.set_result(payload)

    async def send(self, payload):
        # Keep potentially state-changing writes bounded. A timeout does not
        # establish non-delivery; the controller's finite timer is still needed.
        timeout = .2 if payload[0] in (6, 63) else .7
        await asyncio.wait_for(
            self.client.write_gatt_char(RX, frame(payload), response=True), timeout)
        if payload[0] == 6 and int.from_bytes(payload[1:], 'big', signed=True) > 0:
            self.positive += 1

    async def query(self, command):
        if command not in (0, 4, 14, 17, 32):
            raise ValueError('Read command not allowed')
        future = asyncio.get_running_loop().create_future()
        self.waiters[command] = future
        try:
            await self.send(bytes([command]))
            return await asyncio.wait_for(future, 3 if command in (14, 17) else .7)
        finally:
            self.waiters.pop(command, None)

    async def samples(self):
        self.sample_started = time.monotonic()
        values = decode_telemetry(await self.query(4))
        values['motor_temp_c'] = None  # No sensor wired on this kart.
        adc = await self.query(32)
        if len(adc) != 17:
            raise RuntimeError('Invalid ADC reply')
        level, volts = struct.unpack('>ii', adc[1:9])
        values.update(throttle=level / 1e6, throttle_v=volts / 1e6,
                      sample_cycle_ms=round((time.monotonic() - self.sample_started) * 1000))
        return values

    async def current(self, amps):
        if not 0 <= amps <= 10:
            raise ValueError('Only reviewed bench currents are allowed')
        await self.send(bytes([6]) + struct.pack('>i', int(amps * 1000)))

    async def handoff(self, enabled):
        await self.send(bytes([63, 0]) + struct.pack('>i', 300 if enabled else 0))


def check(values, *, stationary=False, current_amps=1, speed_limit=500):
    if not 60 <= values['input_voltage_v'] <= 85 or not 0 <= values['mosfet_temp_c'] <= 60:
        raise RuntimeError('Voltage/ESC-temperature guard')
    if (values['fault_code'] != 0 or abs(values['erpm']) > speed_limit or
            abs(values['motor_current_a']) > current_amps + 2):
        raise RuntimeError('Fault/current/RPM guard')
    # Exact matched baseline: Current Reverse Button, hyst=0.15. Keep the
    # starting check strict; running limit 0.10 remains below that deadband.
    # The first 1 A probe measured 0.055 / 0.925 V without pedal movement.
    neutral_limit = .02 if stationary else .10
    if not 0 <= values['throttle'] <= neutral_limit or not .7 <= values['throttle_v'] <= 1:
        raise RuntimeError('Throttle not neutral')
    if values['sample_cycle_ms'] > 350:
        raise RuntimeError('Sample cycle too slow')
    if stationary and (values['erpm'] != 0 or abs(values['motor_current_a']) > .2):
        raise RuntimeError('Motor not stationary')


async def exercise(link, args, values):
    check(values, stationary=True, current_amps=args.current_amps, speed_limit=args.speed_limit_erpm)
    duration = args.probe_ms / 1000 if args.mode == 'probe' else args.duration_seconds
    started = time.monotonic()
    peak = 0
    failure = None
    release_errors = []
    try:
        while time.monotonic() - started < duration:
            check(values, current_amps=args.current_amps, speed_limit=args.speed_limit_erpm)
            if time.monotonic() - link.sample_started > .35:
                raise RuntimeError('Stale sample before handoff')
            lease_started = time.monotonic()
            if args.handoff:
                await link.handoff(True)
            # No new positive request after duration expiry or stale feedback.
            if time.monotonic() - started >= duration:
                break
            if time.monotonic() - link.sample_started > .35:
                raise RuntimeError('Stale sample before current request')
            if args.handoff and time.monotonic() - lease_started > .1:
                raise RuntimeError('Handoff write too slow')
            target = min(800, args.speed_limit_erpm * .3)
            amps = min(args.current_amps, max(0, (target - abs(values['erpm'])) * .02)) if args.mode != 'zero' else 0
            await link.current(amps)
            values = await link.samples()
            peak = max(peak, abs(values['erpm']))
            emit(dict(values, elapsed_ms=round((time.monotonic() - started) * 1000)))
            # Validate the LAST sample too, including a one-iteration probe.
            check(values, current_amps=args.current_amps, speed_limit=args.speed_limit_erpm)
            await asyncio.sleep(.02)
    except BaseException as error:
        failure = error
    finally:
        # Try both release actions independently, including if zero write fails.
        try:
            await link.current(0)
        except BaseException as error:
            release_errors.append('zero current: ' + repr(error))
        if args.handoff:
            try:
                await link.handoff(False)
            except BaseException as error:
                release_errors.append('ADC re-enable: ' + repr(error))
        emit(dict(result='aborted' if failure or release_errors else 'completed',
                  error=repr(failure) if failure else None,
                  requested_current_a=args.current_amps if args.mode != 'zero' else 0,
                  requested_duration_ms=round(duration * 1000),
                  speed_limit_erpm=args.speed_limit_erpm,
                  release_elapsed_ms=round((time.monotonic() - started) * 1000),
                  positive_requests=link.positive, peak_erpm=peak,
                  release_errors=release_errors))
    if not release_errors:
        for _ in range(5):
            emit(await link.samples())
    if failure:
        raise failure
    if release_errors:
        raise RuntimeError('Release unconfirmed: ' + '; '.join(release_errors))


async def run(args):
    from bleak import BleakClient, BleakScanner
    device = await BleakScanner.find_device_by_filter(
        lambda device, advertisement: advertisement.local_name == 'VESC BLE UART', timeout=10)
    if not device:
        raise RuntimeError('VESC Bluetooth device not found; release other BLE clients')
    async with BleakClient(device, timeout=10) as client:
        link = Link(client)
        await client.start_notify(TX, link.notify)
        ident = identity(await link.query(0))
        emit({'identity': ident})
        if ident.get('firmware') != '6.00' or ident.get('uuid', '').lower() != UUID:
            raise RuntimeError('Wrong VESC')
        for command, name in ((14, 'motor'), (17, 'app')):
            if await link.query(command) != (SNAPSHOT / f'{name}_configuration.bin').read_bytes():
                raise RuntimeError(f'{name} configuration differs from reviewed snapshot')
        emit({'preflight': 'baseline_match'})
        values = await link.samples()
        emit(values)
        if args.mode == 'read-only':
            for _ in range(10):
                emit(await link.samples())
            return
        await exercise(link, args, values)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['read-only', 'zero', 'probe', 'spin'], default='read-only')
    parser.add_argument('--current-amps', type=int, choices=[1, 2, 5, 10], default=1)
    parser.add_argument('--duration-seconds', type=int, choices=[5, 30], default=5)
    parser.add_argument('--speed-limit-erpm', type=int, choices=[500, 2000], default=500)
    parser.add_argument('--probe-ms', type=int, choices=[250, 500], default=250)
    parser.add_argument('--wheels-up-neutral', action='store_true')
    parser.add_argument('--positive-is-forward', action='store_true')
    parser.add_argument('--handoff', action='store_true', help='Finite 300 ms ADC override')
    args = parser.parse_args(argv)
    if args.mode != 'read-only' and not args.wheels_up_neutral:
        parser.error('Physical wheels-up/neutral confirmation required')
    if args.mode == 'spin' and not args.positive_is_forward:
        parser.error('Observe the probe and confirm physical forward first')
    return args


if __name__ == '__main__':
    asyncio.run(run(arguments()))
