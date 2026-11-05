#!/usr/bin/env python3
"""macOS local hidden-password prompt; writes ignored firmware configuration."""
import json
import os
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[2]/'firmware/heltec'
def prompt(message, hidden=False):
    script='text returned of (display dialog '+json.dumps(message)+' default answer "" '+('with hidden answer ' if hidden else '')+'with title "GoKart Control Wi-Fi" buttons {"Cancel", "Save"} default button "Save")'
    result=subprocess.run(['osascript','-e',script],capture_output=True,text=True)
    if result.returncode:
        raise SystemExit('Wi-Fi provisioning cancelled or unavailable; no password printed.')
    return result.stdout.rstrip('\n')
ssid=prompt('Enter the 2.4 GHz Wi-Fi network name for Heltec OTA updates:')
password=prompt('Enter that Wi-Fi network password (stored locally, excluded from Git):',True)
if not 1<=len(ssid.encode())<=32 or not 8<=len(password)<=63:
    raise SystemExit('Expected a 1–32 byte SSID and an 8–63 character WPA password; no changes saved.')
path=root/'.local/wifi.json'
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:json.dump({'ssid':ssid,'password':password},f)
header=root/'include/wifi.local.h'
fd=os.open(header,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:f.write('#pragma once\n#define WIFI_SSID '+json.dumps(ssid)+'\n#define WIFI_PASSWORD '+json.dumps(password)+'\n')
print('Wi-Fi credentials saved locally; contents not printed.')
