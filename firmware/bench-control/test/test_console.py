import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('bench_console',Path(__file__).parents[1]/'console.py')
console=importlib.util.module_from_spec(spec);spec.loader.exec_module(console)

class FakeTime:
    def __init__(self):self.t=0
    def monotonic(self):self.t+=.05;return self.t

class Port:
    def __init__(self,**kwargs):self.commands=[];self.phase=0;self.running_reads=0
    def open(self):pass
    def close(self):pass
    def flush(self):pass
    def write(self,b):
        self.commands.append(b)
        if b==b'arm wheels-up-neutral\n':self.phase=1
        if b in (b'probe\n',b'dry-run\n',b'run positive-forward-confirmed\n'):self.phase=2
    def readline(self):
        d=dict(firmware='bench-control-0.3.1',identity=True,motor_commands_compiled=True,release_pending=False,baseline_match=True,values_valid=True,adc_valid=True,values_age_ms=10,adc_age_ms=20,erpm=0,motor_a=0,fault=0,throttle=0,throttle_v=.837,state=self.phase,reason='idle')
        if self.phase==2:
            self.running_reads+=1
            if self.running_reads>2:self.phase=0;d.update(state=0,reason='pulse_complete_coasting')
        return json.dumps(d).encode()+b'\n'

class ConsoleTests(unittest.TestCase):
    def run_console(self,args):
        port=Port();fake=types.SimpleNamespace(Serial=lambda **kw:port,SerialException=OSError)
        with patch.dict(sys.modules,serial=fake),patch.object(console,'time',FakeTime()),patch.object(sys,'argv',['console','--port','fake']+args),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):console.main()
        return port.commands
    def test_default_has_no_arming_or_keepalive(self):
        self.assertEqual(self.run_console([]),[b'preflight\n'])
    def test_probe_requires_physical_confirmation(self):
        with self.assertRaises(SystemExit):self.run_console(['--test','probe'])
    def test_five_seconds_requires_direction_confirmation(self):
        with self.assertRaises(SystemExit):self.run_console(['--test','five-seconds','--wheels-up-neutral'])
    def test_probe_arms_then_runs_and_aborts_on_exit(self):
        commands=self.run_console(['--test','probe','--wheels-up-neutral'])
        self.assertEqual(commands[0:2],[b'preflight\n',b'arm wheels-up-neutral\n'])
        self.assertIn(b'probe\n',commands);self.assertIn(b'keepalive\n',commands)
        self.assertEqual(commands[-1],b'abort\n')
    def test_dry_run_never_sends_torque_test(self):
        commands=self.run_console(['--test','dry-run','--wheels-up-neutral'])
        self.assertIn(b'dry-run\n',commands);self.assertNotIn(b'probe\n',commands)
        self.assertNotIn(b'run positive-forward-confirmed\n',commands)
    def test_readiness_rejects_stale_or_non_neutral(self):
        d=json.loads(Port().readline());self.assertTrue(console.ready(d))
        for key,value in [('values_age_ms',351),('baseline_match',False),('erpm',1),('throttle',.03),('adc_valid',False),('motor_commands_compiled',False)]:
            v=dict(d);v[key]=value;self.assertFalse(console.ready(v))

if __name__=='__main__':unittest.main()
