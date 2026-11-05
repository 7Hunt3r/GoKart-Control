import contextlib
import importlib.util
import io
from pathlib import Path
import time
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('direct_ble', Path(__file__).parents[1] / 'direct_ble.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def sample(**changes):
    values = dict(input_voltage_v=76.8, mosfet_temp_c=25, fault_code=0,
                  erpm=0, motor_current_a=0, throttle=.014, throttle_v=.858,
                  sample_cycle_ms=120)
    values.update(changes)
    return values


class FakeLink:
    def __init__(self, values, zero_fails=False):
        self.sample_started = time.monotonic()
        self.values = values
        self.commands = []
        self.positive = 0
        self.zero_fails = zero_fails

    async def handoff(self, enabled):
        self.commands.append(('handoff', enabled))

    async def current(self, amps):
        self.commands.append(('current', amps))
        if amps:
            self.positive += 1
        elif self.zero_fails:
            raise OSError('write failed')

    async def samples(self):
        self.sample_started = time.monotonic()
        return self.values


class DirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_bad_sample_aborts_and_releases(self):
        link = FakeLink(sample(throttle=.11))
        args = types.SimpleNamespace(mode='probe', probe_ms=250, current_amps=5, handoff=True, duration_seconds=5, speed_limit_erpm=500)
        with patch.object(bench, 'emit') as output:
            with self.assertRaisesRegex(RuntimeError, 'Throttle'):
                await bench.exercise(link, args, sample())
        result = [call.args[0] for call in output.call_args_list if 'result' in call.args[0]][0]
        self.assertEqual(result['result'], 'aborted')
        self.assertEqual(link.commands[-2:], [('current', 0), ('handoff', False)])
        self.assertEqual(link.positive, 1)

    async def test_failed_zero_still_reenables_adc(self):
        link = FakeLink(sample(fault_code=1), zero_fails=True)
        args = types.SimpleNamespace(mode='probe', probe_ms=250, current_amps=1, handoff=True, duration_seconds=5, speed_limit_erpm=500)
        with patch.object(bench, 'emit'):
            with self.assertRaises(RuntimeError):
                await bench.exercise(link, args, sample())
        self.assertEqual(link.commands[-1], ('handoff', False))

    async def test_handoff_delay_prevents_positive_request(self):
        link = FakeLink(sample())
        async def slow_handoff(enabled):
            if enabled:
                link.sample_started -= 1
        link.handoff = slow_handoff
        args = types.SimpleNamespace(mode='probe', probe_ms=250, current_amps=5, handoff=True, duration_seconds=5, speed_limit_erpm=500)
        with patch.object(bench, 'emit'):
            with self.assertRaisesRegex(RuntimeError, 'Stale'):
                await bench.exercise(link, args, sample())
        self.assertEqual(link.positive, 0)

    def test_neutral_checks_preserve_margin_below_actual_deadband(self):
        bench.check(sample(throttle=.055, throttle_v=.925), current_amps=5)
        with self.assertRaises(RuntimeError):
            bench.check(sample(throttle=.055), stationary=True)
        with self.assertRaises(RuntimeError):
            bench.check(sample(throttle=.11))
        for values in (sample(erpm=501), sample(motor_current_a=7.01), sample(sample_cycle_ms=351)):
            with self.assertRaises(RuntimeError):
                bench.check(values, current_amps=5)

    def test_cli_defaults_and_required_direction(self):
        self.assertEqual(bench.arguments([]).mode, 'read-only')
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                bench.arguments(['--mode', 'spin', '--wheels-up-neutral'])
            with self.assertRaises(SystemExit):
                bench.arguments(['--current-amps', '100'])
