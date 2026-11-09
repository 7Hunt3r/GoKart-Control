#!/usr/bin/env python3
# DEV TOOL (Mac): needs Pillow + numpy, never runs on a Pi. Produces the *_hc.jpg map imagery served by the pit:
#   python3 pit/tools/enhance_map.py pit/server/map/course_shop_cteco.jpg pit/server/map/course_shop_cteco_hc.jpg --lo 1 --hi 99.5 --gamma 0.85 --sharp 110 --sat 1.2
#   python3 pit/tools/enhance_map.py pit/server/map/course.jpg       pit/server/map/course_hc.jpg       --lo 1 --hi 99.5 --gamma 0.85 --sharp 110 --sat 1.0 --wb 0.6
#   python3 pit/tools/enhance_map.py pit/server/map/course_skyfi.jpg pit/server/map/course_skyfi_hc.jpg --lo 1 --hi 99.5 --gamma 0.85 --sharp 110 --sat 1.1 --wb 0.3
# (quality 82). Why: the CT ECO orthos use only half the tonal range (shop export luma p0.5..p99.5 = 31..228, Manresa 37..151),
# so buildings and road edges were grey mush on the sunlit 7" kart screen. Owner, 2026-11-09: "change the map contrast so the buildings show, more clear".
"""Aerial map contrast/clarity pass for the pit/kart map canvas. PIL + numpy (+ cv2 CLAHE when available).
usage: enhance.py IN.jpg OUT.jpg [--clahe 2.0] [--tiles 12] [--sat 1.15] [--sharp 90] [--lo 0.5 --hi 99.5] [--gamma 0.92]
Steps: percentile level stretch (lo..hi -> 0..255, luminance-based so hue is kept) -> gamma -> CLAHE on L (or a
large-radius unsharp 'local contrast' fallback) -> small-radius unsharp sharpen -> saturation. Same pixel size out."""
import sys, argparse, numpy as np
from PIL import Image, ImageFilter, ImageEnhance
ap = argparse.ArgumentParser(); ap.add_argument("inp"); ap.add_argument("out")
ap.add_argument("--clahe", type=float, default=2.0); ap.add_argument("--tiles", type=int, default=12)
ap.add_argument("--sat", type=float, default=1.15); ap.add_argument("--sharp", type=int, default=90)
ap.add_argument("--lo", type=float, default=0.5); ap.add_argument("--hi", type=float, default=99.5)
ap.add_argument("--gamma", type=float, default=0.92); ap.add_argument("--quality", type=int, default=88)
ap.add_argument("--wb", type=float, default=0.0, help="grey-world white balance strength 0..1 (fixes an ortho colour cast)")
a = ap.parse_args()
im = Image.open(a.inp).convert("RGB")
arr = np.asarray(im, dtype=np.float32)
if a.wb > 0:                                   # grey-world: pull each channel mean toward the overall mean
    means = arr.reshape(-1, 3).mean(axis=0); gain = 1 + a.wb * (means.mean() / means - 1)
    arr = np.clip(arr * gain, 0, 255)
luma = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
lo, hi = np.percentile(luma, [a.lo, a.hi])
arr = np.clip((arr - lo) / max(hi - lo, 1) * 255.0, 0, 255)
arr = 255.0 * (arr / 255.0) ** a.gamma
im = Image.fromarray(arr.astype(np.uint8))
try:
    import cv2
    lab = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2LAB)
    cl = cv2.createCLAHE(clipLimit=a.clahe, tileGridSize=(a.tiles, a.tiles))
    lab[:, :, 0] = cl.apply(lab[:, :, 0])
    im = Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)); method = f"CLAHE clip {a.clahe} tiles {a.tiles}"
except ImportError:
    im = im.filter(ImageFilter.UnsharpMask(radius=40, percent=45, threshold=0)); method = "local-contrast unsharp r40 45%"
im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=a.sharp, threshold=2))
im = ImageEnhance.Color(im).enhance(a.sat)
im.save(a.out, quality=a.quality, optimize=True, subsampling=0)
g = np.asarray(im.convert("L"), dtype=np.float32)
print(f"{a.out}: {im.size} | levels {lo:.0f}..{hi:.0f} -> 0..255, gamma {a.gamma}, {method}, sharpen {a.sharp}%, sat {a.sat}, wb {a.wb} | out luma mean {g.mean():.0f} std {g.std():.0f} p5/50/95 {np.percentile(g,[5,50,95]).round(0).tolist()}")
