#!/usr/bin/env python3
"""Authenticated ArduinoOTA upload with bounded waits and stream-safe result parsing.

Protocol interoperates with Arduino ESP32 ArduinoOTA; MD5 is required by that
protocol, not a claim of signed firmware. Credentials never enter command args.
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import threading
import time


def upload(address, image, password, host_ip=None, port=3232):
    remote=socket.gethostbyname(address)
    if not host_ip:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as route:
            route.connect((remote,port));host_ip=route.getsockname()[0]
    content=Path(image).read_bytes()
    if not content or content[0]!=0xe9:raise ValueError('Expected an ESP application firmware.bin')
    digest=hashlib.md5(content).hexdigest()
    with socket.socket() as listener, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        listener.bind((host_ip,0));listener.listen(1);listener.settimeout(15)
        udp.bind((host_ip,0));udp.settimeout(8)
        invitation=f'0 {listener.getsockname()[1]} {len(content)} {digest}\n'.encode()
        udp.sendto(invitation,(remote,port))
        challenge,peer=udp.recvfrom(128)
        if peer[0]!=remote or not challenge.startswith(b'AUTH '):raise RuntimeError('Expected authenticated OTA challenge')
        nonce=challenge.decode().strip().split()[1]
        cnonce=secrets.token_hex(16)
        password_hash=hashlib.md5(password.encode()).hexdigest()
        response=hashlib.md5(f'{password_hash}:{nonce}:{cnonce}'.encode()).hexdigest()
        udp.sendto(f'200 {cnonce} {response}\n'.encode(),(remote,port))
        ack,peer=udp.recvfrom(128)
        if peer[0]!=remote or ack.strip()!=b'OK':raise RuntimeError('OTA authentication rejected')
        print('Authenticated; waiting for device.',flush=True)
        conn,peer=listener.accept()
        with conn:
            if peer[0]!=remote:raise RuntimeError('Unexpected OTA peer')
            conn.settimeout(10)
            send_errors=[];sent=threading.Event()
            def sender():
                try:
                    for offset in range(0,len(content),1024):conn.sendall(content[offset:offset+1024])
                except Exception as e:send_errors.append(e)
                finally:sent.set()
            worker=threading.Thread(target=sender,daemon=True);worker.start()
            tail=b'';deadline=time.monotonic()+120;received_ok=False
            while time.monotonic()<deadline:
                if send_errors:raise RuntimeError('Firmware transfer failed') from send_errors[0]
                try:chunk=conn.recv(4096)
                except socket.timeout:continue
                if not chunk:break
                # TCP boundaries need not align with numeric progress or final "OK".
                tail=(tail+chunk)[-8192:]
                if b'OK' in tail:received_ok=True;break
            sent.wait(2)
            if not received_ok or not sent.is_set() or send_errors:
                raise RuntimeError('No confirmed completion. Inspect device version before retrying; do not assume failure means old firmware.')
            print('Device confirmed OTA success; verify new version and telemetry after reboot.',flush=True)


def main():
    root=Path(__file__).resolve().parents[2]
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('address');ap.add_argument('--host-ip')
    ap.add_argument('--image',type=Path,default=root/'firmware/heltec/.pio/build/heltec_v43/firmware.bin')
    args=ap.parse_args()
    password=json.loads((root/'firmware/heltec/.local/credentials.json').read_text())['ota_password']
    try:upload(args.address,args.image,password,args.host_ip)
    except (OSError,ValueError,RuntimeError) as e:ap.exit(1,f'OTA: {e}\n')

if __name__=='__main__':main()
