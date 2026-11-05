#!/usr/bin/env python3
"""Decode Eamon's shared 46-byte GoKartProtocol v1 telemetry payload."""
import argparse
import json
import struct

FORMAT=struct.Struct('<6BiiHHBBHhhibhBBBBBHHbBH')
assert FORMAT.size==46

def decode(data):
    if len(data)!=FORMAT.size:raise ValueError('Expected 46-byte shared telemetry')
    (version,fleet,kind,kart,dst,seq,lat,lon,speed,heading,sats,flags,volts,motor,
     battery,erpm,duty,esc,fault,requested,actual,last_cmd,result,ah,uptime,rssi,
     gps_age,scale)=FORMAT.unpack(data)
    if (version,fleet,kind)!=(1,0x4b,1) or not 1<=kart<=8 or dst not in (0,255):
        raise ValueError('Unknown/invalid telemetry header')
    v=bool(flags&4);g=bool(flags&1)
    return dict(version=version,fleet=fleet,kart_id=kart,sequence=seq,
                vesc_valid_at_sender=v,gps_valid_at_sender=g,
                latitude_deg=lat/1e7 if g else None,longitude_deg=lon/1e7 if g else None,
                gps_speed_mps=speed/36 if g else None,heading_deg=heading/10 if g else None,
                satellites=sats,gps_age_s=None if gps_age==255 else gps_age,
                battery_voltage_v=volts/10 if v else None,motor_current_a=motor/10 if v else None,
                battery_current_a=battery/10 if v else None,erpm=erpm if v else None,
                duty=duty/100 if v else None,esc_temperature_c=esc/10 if v else None,
                fault_code=fault if v else None,motor_temperature_c=None,
                mode_requested=None if requested==255 else requested,
                mode_actual=None if actual==255 else actual,mode_confirmed=bool(flags&16),
                writes_enabled=bool(flags&128),pps_aligned=bool(flags&2),
                last_command_sequence=last_cmd,command_result=result,
                amp_hours=ah/100 if v else None,uptime_s_wrapping=uptime,
                current_scale=None if scale==65535 else scale/1000,
                base_rssi_dbm=rssi or None)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('hex_payload');a=p.parse_args()
    print(json.dumps(decode(bytes.fromhex(a.hex_payload)),indent=2))
