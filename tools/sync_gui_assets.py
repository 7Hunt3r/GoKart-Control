#!/usr/bin/env python3
"""Copy shared display assets into the two existing static roots; no server changes.

Edit the source in kart/server/static, then run this script. --check verifies parity.
"""
from pathlib import Path
import argparse
import shutil

ROOT = Path(__file__).resolve().parents[1]
ASSETS = (
    "display-theme.css", "display.js", "font-segments.css", "DSEG-LICENSE.txt",
    "sono-dark.svg", "sono-light.svg", "sono-dark-high.svg", "sono-light-high.svg",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name in ASSETS:
        source = ROOT / "kart/server/static" / name
        target = ROOT / "pit/server/static" / name
        if args.check:
            if not target.exists() or source.read_bytes() != target.read_bytes():
                raise SystemExit(f"Shared GUI asset differs: {name}; run tools/sync_gui_assets.py")
        else:
            shutil.copyfile(source, target)
    print("Shared GUI assets match." if args.check else "Shared GUI assets copied.")


if __name__ == "__main__":
    main()
