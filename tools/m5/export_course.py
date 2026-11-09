#!/usr/bin/env python3
"""Export the pit's drawn track as an offline M5 outline; no fabricated default course."""
import argparse
import json
import math
from pathlib import Path


def export(zones, name):
    track = zones.get('track')
    if not isinstance(track, dict):
        raise ValueError('No track in zones.json: draw/record a track in the pit first')
    pts = track.get('points')
    if not isinstance(pts, list) or not 2 <= len(pts) <= 128:
        raise ValueError('Track must have 2..128 points; simplify it in the pit first')
    for p in pts:
        if not isinstance(p, list) or len(p) != 2 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in p):
            raise ValueError('Each point must be [latitude, longitude] with finite numbers')
        if not -90 <= p[0] <= 90 or not -180 <= p[1] <= 180:
            raise ValueError('Point outside latitude/longitude range')
    if not isinstance(name, str) or not name.strip() or len(name.encode('utf-8')) > 40:
        raise ValueError('Course name must be 1..40 UTF-8 bytes')
    return {'schema': 1, 'name': name, 'closed': bool(track.get('closed', False)), 'points': pts,
            'source_zones_version': zones.get('version'), 'note': 'Outline only; no zone effects or enforcement'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--zones', type=Path, required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    try:
        result = export(json.loads(args.zones.read_text()), args.name)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('x') as f:
            json.dump(result, f, indent=2)
            f.write('\n')
    except (ValueError, OSError) as exc:
        ap.exit(2, str(exc)+'\n')
    print(f'{args.out}: {len(result["points"])} points; copy to SD /gokart/course.json')

if __name__ == '__main__':
    main()
