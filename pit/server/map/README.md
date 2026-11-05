# Course map package (pit side)

Status: **no imagery, course line or pit location is bundled.** The pit map
currently draws karts and the base on a blank local east/north grid in metres.

`map.json` in this folder drives both the pit map background and
`GET /map/package.json` (the endpoint kart displays will fetch on the pit Wi-Fi,
per `firmware/base-tbeam-s3/SERIAL_PROTOCOL.md`). Files placed here are served
at `/map/<name>` (png, jpg, webp, json only; no authentication, since kart
displays fetch them without the operator password).

```json
{
  "version": 1,
  "image": "course.jpg",
  "bounds": {"north": 41.0745, "south": 41.0710, "east": -73.4010, "west": -73.4085},
  "origin": {"lat": 41.0727, "lon": -73.4047},
  "course": [[41.0721, -73.4060], [41.0721, -73.4030]],
  "pit": {"lat": 41.0727, "lon": -73.4047},
  "source": "CT ECO 2023 orthoimagery, tile ...",
  "attribution": "CT ECO / CT State GIS Office"
}
```

The numbers above are placeholders, not the site.

- `image`: file name in this folder. North-up, WGS84 lat/lon edges given by `bounds`.
- `bounds`: decimal degrees of the image's outer pixel edges.
- `origin`: optional fallback map origin. The pit uses, in order: base GPS
  (first fix of the session), else the first kart fix, else this, else
  `origin` in `server/config.json`. It only sets the centre of the local
  metre grid; positions are never moved to it when a fix is missing.
- `course`, `pit`: optional overlay line and pit point (lat/lon).
- Bump `version` whenever the package changes so kart displays re-fetch.

The pit draws the image by placing its four bounds in local metres
(equirectangular approximation around the origin). That is fine over a site a
few hundred metres across; it is not a reprojection. The image must already
be in (or warped to) plain WGS84 lat/lon (EPSG:4326) with north up.

## Adding CT ECO imagery later (proposed procedure, not yet done)

1. Download the 2023 3-inch orthoimagery tile(s) covering Manresa Island from
   https://maps.cteco.uconn.edu/data/flight2023/ (GeoTIFF). Keep the original
   download and its metadata outside git (large; use an asset store or Git LFS
   per CONTRIBUTING.md).
2. On a workstation with GDAL, crop and warp to WGS84 and a Pi-friendly size:
   ```sh
   gdalwarp -t_srs EPSG:4326 -te <west> <south> <east> <north> -ts 2048 0 \
     -r bilinear source.tif course_4326.tif
   gdal_translate -of JPEG -co QUALITY=85 course_4326.tif course.jpg
   gdalinfo course_4326.tif   # read the corner coordinates for "bounds"
   ```
3. Put `course.jpg` here, fill `bounds` from the gdalinfo corners, set
   `source`/`attribution`, bump `version`, then restart nothing (map.json is
   re-read on each request).
4. Field-check alignment: stand at two known points with a kart or the base
   and confirm the dots land on the right pixels. Record the check in the PR.

Imagery date may not match current site conditions, and imagery says nothing
about the intended course route: draw `course` separately.
