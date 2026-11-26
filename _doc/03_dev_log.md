# 03 – Development log

Thinking, steps and results, newest at the bottom.

## 2026-11-26 – Setup and test data

**Interview.** See `01_requirements.md`. Main decisions: local web app, GeoPackage, NodeODM, auto UTM,
GPS-only first, all clips from the drone as test data.

**Environment.**
- `python -m venv .venv` (Python 3.14.4) + `app/requirements.txt`. All packages installed from wheels without issues:
  OpenCV 5.1.0, GDAL 3.12.4 (pyogrio), ffmpeg 9.0.2 bundled by `imageio-ffmpeg` (no system ffmpeg needed).
- Docker Desktop is installed (service stopped) → used later for NodeODM.

**Test data.** All files on the drone's internal storage (`D:\DCIM\DJI_001`, `D:\MISC`) copied to
`test_data/raw/`: 16 MP4 + 16 SRT, 23 JPG, 5.5 GB. Inventory: `_doc/data/test_data_inventory.csv`
(`app/tools/inventory_test_data.py`).

| Finding | Value |
|---|---|
| Video | 3840 × 2160, HEVC Main, 29.97 fps, plus a `djmd` data track |
| Photo | 4000 × 2256 (16:9), FocalLength 2.598 mm, 35 mm-equiv. 14 mm |
| SRT | one block per video frame: time, ISO, shutter, f-number, EV, colour temperature, lat, lon, rel_alt, abs_alt |
| Longest/highest clip | `DJI_20250706110330_0028_D`: 283 s, 8495 frames, 343 m path, height 3.6–32.1 m above take-off |
| Others | mostly short low test flights (< 11 m), useful for parser tests only |
| Speeds | median 0.1–2.4 m/s, max 4.0 m/s (no survey-style flying in the test data) |
| Shutter | 1/100 – 1/1250 s; dusk clips reach ISO 3400 (expect noisy frames) |

Note: in several clips `rel_alt` goes negative (down to −15 m in clip 0003): barometric drift or take-off from a
higher point. Height above ground for footprints therefore needs care (see QA design).

**SRT parser** (`missionplanner/srt.py`): key/value based, tolerant of other DJI variants
(`longtitude` typo, `altitude` key), treats 0/0 as "no fix".

## 2026-11-26 – djmd metadata stream (camera angle)

**Why.** The SRT has no camera angle, yet the camera must point straight down for mapping. The JPG XMP shows
gimbal angles, so the drone knows them. The MP4 has a second track (`djmd`, ~75 kb/s).

**Steps.**
1. `ffmpeg -i clip.MP4 -map 0:d:0 -c copy -f data out.bin` → 2.75 MB for 8495 frames (~324 B/frame).
2. Strings in it: `dvtm_dji_neo.proto`, `DJI NEO`, `DJI FC8671`, firmware `10.00.04.91` → protobuf.
3. Wrote a schema-less protobuf wire decoder (`missionplanner/djmd.py`). Top level: field 1 (header), field 2
   (device info), then **field 3 × 8495 = one message per video frame**.
4. Flattened each frame and compared value ranges/time series with the SRT and with the flight's behaviour.

**Results – field map (DJI Neo, firmware 10.00.04.91).**

| Path | Type | Meaning |
|---|---|---|
| 1.2 | varint | timestamp, µs |
| 4.4.1.2 / 4.4.1.3 | double | latitude / longitude, **radians** |
| 4.4.2 | varint | absolute altitude, mm |
| 4.5.1 | float | relative altitude (above take-off), mm |
| 4.3.1 / 4.3.2 / 4.3.3 | signed varint | **drone** pitch / roll / yaw, 0.1° |
| 5.2.1 / 5.2.2 / 5.2.3 | signed varint | **gimbal** pitch / roll / yaw, 0.1° (0 = horizon, −90 = nadir) |
| 2.3.1, 2.6.1 | float / varint | ISO, colour temperature |

Zero values are omitted (proto3), so a missing angle field means 0°.

**Validation** on all 16 clips: frame counts identical to the SRT; position differences ≤ 0.06 m (SRT rounding
to 6 decimals); relative and absolute altitudes identical. Exception: clip 0003 (GPS lost for 60 % of frames,
gimbal angles wrapping ±180°, absolute altitude differs by 225 m) → a clip to be flagged by QA, not trusted.
In clip 0028 the gimbal pitch goes 0° → −39° → −60° → −90° (t ≈ 110 s) → −76° → −17°, consistent with the
pilot tilting the camera during the flight.

Observation: gimbal yaw differs from drone yaw by a near-constant ≈ −20°. Not explained yet (not magnetic
declination, which is ≈ +5° in Czechia). For footprints the drone yaw is used; to be checked against ODM's
computed camera orientations.
