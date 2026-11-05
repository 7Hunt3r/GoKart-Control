#!/usr/bin/env python3
"""USB runner for the isolated diagnostic; defaults to read-only observation."""
import argparse
import json
import sys
import time


def ready(d):
    return (d.get('firmware')=='bench-control-0.3.1' and d.get('identity')
            and d.get('motor_commands_compiled') and d.get('baseline_match') and d.get('values_valid') and d.get('adc_valid')
            and d.get('values_age_ms',9999)<300 and d.get('adc_age_ms',9999)<300
            and d.get('erpm')==0 and abs(d.get('motor_a',999))<=.2
            and d.get('fault')==0 and 0<=d.get('throttle',999)<=.02
            and .70<=d.get('throttle_v',0)<=1.00)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',required=True)
    p.add_argument('--test',choices=('read-only','dry-run','probe','five-seconds'),default='read-only')
    p.add_argument('--wheels-up-neutral',action='store_true')
    p.add_argument('--positive-is-forward',action='store_true')
    a=p.parse_args()
    active=a.test!='read-only'
    if active and not a.wheels_up_neutral:p.error('Confirm supported wheels, released pedals and a person at the physical power cutoff with --wheels-up-neutral')
    if a.test=='five-seconds' and not a.positive_is_forward:p.error('Observe the probe first; --positive-is-forward is required')
    import serial
    s=serial.Serial(port=None,baudrate=115200,timeout=.1)
    s.dtr=True;s.rts=False;s.port=a.port;s.open()
    armed=False;sent=False;running=False;heartbeat=0;started=time.monotonic()
    result=False
    try:
        s.write(b'preflight\n')
        while time.monotonic()-started < (45 if active else 20):
            now=time.monotonic()
            if active and armed and now-heartbeat>=.1:s.write(b'keepalive\n');heartbeat=now
            try:d=json.loads(s.readline())
            except (ValueError,UnicodeDecodeError):continue
            print(json.dumps(d),flush=True)
            if d.get('firmware')!='bench-control-0.3.1':continue
            if not active:continue
            if not armed and now-started>=1 and ready(d):s.write(b'arm wheels-up-neutral\n');armed=True;continue
            if armed and not sent and d.get('state')==1:
                command={'dry-run':'dry-run','probe':'probe','five-seconds':'run positive-forward-confirmed'}[a.test]
                s.write((command+'\n').encode());sent=True;continue
            if sent and d.get('state')==2:running=True
            if sent and d.get('state')==0:
                if d.get('release_pending',True):continue
                result=running and d.get('reason')=='pulse_complete_coasting'
                print('Pulse ended; wheels may coast.' if result else 'Test aborted or completion unconfirmed: '+str(d.get('reason')),file=sys.stderr)
                break
        if active and not result:raise RuntimeError('No successful bounded test completion; do not retry automatically')
    finally:
        if active and armed:
            try:s.write(b'abort\n');s.flush()
            except serial.SerialException:pass
        s.close()


if __name__=='__main__':
    try:main()
    except (RuntimeError,OSError,KeyboardInterrupt) as e:
        print(f'Bench runner stopped: {e}',file=sys.stderr);sys.exit(1)
