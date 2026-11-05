#!/usr/bin/env python3
"""Decode the 47-byte GKC bench LoRa payload; supply its hex bytes."""
import argparse
import json
import struct

FORMAT=struct.Struct('<2sBBIIIHBBHhhihiiHBHH')
assert FORMAT.size==47

def decode(data):
    if len(data)!=FORMAT.size: raise ValueError('Expected 47 bytes')
    (magic,version,kart,boot,seq,uptime,age,flags,fault,voltage,battery,motor,erpm,
     esc,lat,lon,speed,sats,hdop,gps_age)=FORMAT.unpack(data)
    if magic!=b'GK' or version!=1 or kart>8 or flags&~3: raise ValueError('Unknown frame/header')
    v=bool(flags&1);g=bool(flags&2)
    return dict(version=version,kart_id=kart,bench_uncommissioned=kart==0,boot_id=boot,
                sequence=seq,uptime_ms=uptime,vesc_age_ms=None if age==65535 else age,
                vesc_valid_at_sender=v,gps_valid_at_sender=g,fault_code=fault if v else None,
                battery_voltage_v=voltage/10 if v else None,battery_current_a=battery/10 if v else None,
                motor_current_a=motor/10 if v else None,erpm=erpm if v else None,
                esc_temperature_c=esc/10 if v else None,motor_temperature_c=None,
                latitude_deg=lat/1e7 if g else None,longitude_deg=lon/1e7 if g else None,
                gps_speed_mps=None if speed==65535 else speed/100,
                satellites=None if sats==255 else sats,hdop=None if hdop==65535 else hdop/100,
                gps_age_ms=None if gps_age==65535 else gps_age)

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('hex_payload');a=ap.parse_args()
    print(json.dumps(decode(bytes.fromhex(a.hex_payload)),indent=2))
