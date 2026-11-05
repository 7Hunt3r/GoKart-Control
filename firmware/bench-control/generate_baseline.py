"""Generate exact comparison bytes from immutable baseline evidence into build output."""
from pathlib import Path

try:
    Import('env')
except NameError:
    env=None
ROOT=Path(env['PROJECT_DIR']).resolve().parents[1] if env is not None else Path(__file__).resolve().parents[2]

def generate(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    lines=['#pragma once','#include <cstdint>']
    for name,filename in [('MOTOR_BASELINE','motor_configuration.bin'),('APP_BASELINE','app_configuration.bin')]:
        raw=(ROOT/'hardware/vesc/baseline-kart/2026-11-05-bench-preflight/raw'/filename).read_bytes()
        lines.append('static constexpr uint8_t '+name+'[]={'+','.join(str(x) for x in raw)+'};')
    (directory/'baseline_bytes.h').write_text('\n'.join(lines)+'\n')

if env is not None:
    out=Path(env.subst('$BUILD_DIR'))/'generated'
    generate(out);env.Append(CPPPATH=[str(out)])
elif __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory');generate(p.parse_args().directory)
