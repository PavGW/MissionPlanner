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

## 2026-11-26 – Core modules: planner, track QA, coverage

- `camera.py`: camera model from the EXIF (2.598 mm, 14 mm equivalent, 16:9). Neo 4K video: sensor 7.00 × 3.94 mm,
  pixel 1.82 µm, FOV 106.8° × 74.3°, **GSD 2.1 cm at 30 m**, footprint 80.8 × 45.5 m at 30 m.
- `planner.py`: parallel lines over the survey polygon (auto bearing along the polygon's long axis, or given),
  snake order, optional cross grid, lead-in at the line ends, speed limited by motion blur (0.5 px at 1/500 s).
  Test polygon of 4 ha at 2 cm/px with 80/70 % overlap: 10 lines, 23 m spacing, 8.6 m frame spacing, 301 frames,
  11.6 min (1 battery); 100 % of the polygon covered by the planned footprints.
- `track.py`: merges djmd + SRT per frame, smoothed (1 s) positions → speed, along-track distance, yaw rate;
  QA flags no_gps / not_nadir / tilt / turning / low / hover. QA of all test clips: `_doc/data/test_flights_qa.csv`.
  Clip 0028 is the only one with nadir footage (6 % of frames within 5° of nadir).
- `coverage.py`: footprint = the four image-corner rays (plus edge points) intersected with flat ground, so it
  works for oblique frames too. Checked: nadir 80.8 × 45.5 m, rotates with heading, oblique footprints stretch
  forward. View-count grid → share of the area with ≥ 5, 3–4, 1–2 and 0 views.

## 2026-11-26 – Frame accuracy of video decoding (important)

**Problem.** A frame's telemetry is only valid if the decoded image really is that frame.
- Seeking with `ffmpeg -ss` was tested against a reference decode from frame 0, comparing by image correlation.
  It was exact early in the clip but **one frame off late in the clip** (frame 8000). Using the exact rate
  30000/1001 instead of 29.97 and seeking half a frame early did not fully fix it.
- The first extraction version also returned duplicate frames. One of my edits had put a comment inside the
  argument list, which commented out `-fps_mode passthrough`, so ffmpeg duplicated frames to keep a constant rate.
- A first test method was misleading: it compared ffmpeg-scaled with OpenCV-scaled images, and the
  high-pass correlation was dominated by the resampling differences. The test now scales both the same way.

**Solution.** Never seek. Scoring and extraction both decode from frame 0, count frames, and use
`select=eq(n,…)` with `-fps_mode passthrough`. The filter graph is passed from a file with `-/vf <file>`
(ffmpeg 9 removed `-filter_script`). Verified: the scores equal the reference Laplacian exactly (17/17), and
the extracted frames 3000, 3001, 3015 and 3033 match their reference frames exactly.

**Cost.** Decoding 4K HEVC in software runs at about 36 fps (≈ 1.2× real time); DXVA2/D3D11/QSV hardware decoding
gave no gain or failed. A 12-minute survey clip takes about 10 min to score plus the same again to extract.

## 2026-11-26 – First end-to-end run (project `test_0028`)

Clip 0028 (4 min 43 s). The nadir tolerance was relaxed to 35° because the clip was not flown as a survey.

| Step | Result | Time |
|---|---|---|
| Track | 8495 frames, 46.5 % pass the checks | 2 s |
| Sharpness | 3948 candidate frames scored | 260 s |
| Selection (80 % forward overlap) | **29 frames**, height 29.8–32.0 m, gimbal pitch −60 to −90°, relative sharpness 0.61–1.38 | <1 s |
| Extraction + geotag | 29 JPG (3840 × 2160) with EXIF GPS and DJI XMP | 225 s |
| Coverage | 2.7 ha footprint hull, median 3 views, 43 % with ≥ 5 views, 21 % unseen | <1 s |

Layers written to `test_0028.gpkg`: track_points, track_lines, frames, footprints, coverage.

## 2026-11-26 – GitHub survey
See `04_github_survey.md`. No public route to waypoint flight on the Neo. ExifTool documents field
4.4.1.1 (coordinate units: radians or degrees), which the reader now honours. Our pitch/roll order is confirmed
from our own data: gimbal roll tracks field 4.3.2.

## 2026-11-26 – Field exports, NodeODM client, pipeline fixes

**Pipeline tests.** `run_plan` and `import_flight` were tested on a 4 ha test rectangle (generic location).
Two bugs fixed:
- `import_flight`: an operator-precedence error made a given flight name be ignored when the file name had no
  `_`. Now the name is used if given, else the DJI timestamp part (`DJI_20250706110330_0028_D` → `20250706110330`).
- `run_plan`: the planned-coverage grid used one cell size and reported another. The cell is now 1/40 of the
  shorter footprint side (≈ 1.1 m at 28 m height), used for both.
- `project.json`: `runs` changed from a list to a dict keyed by run name (like `flights`).

Plan result (2 cm GSD, 80/70 %): 28.5 m height, 9 lines, ~324 frames, 11.7 min, 1 battery; planned
footprints give ≥ 5 views on 100 % of the area (median 15).

**Field exports** (`missionplanner/exports.py`):
- `export_kml`: survey area, numbered lines (red = main, cyan = cross grid), start/end points `1S`/`1E`, description
  with height and speed. Opens in Google Earth or any phone map app.
- `export_pdf`: one landscape A4 page. Left: plan drawing (north up, numbered lines with direction arrows,
  dashed transits, "start", north arrow, scale bar). Right: fly settings, DJI Fly camera settings, checklist and a
  line table with tick boxes.

**NodeODM** (`missionplanner/odm.py`, `pipeline.run_odm`):
- Docker Desktop 29.9.3; `docker run -d --name nodeodm -p 3000:3000 --restart unless-stopped opendronemap/nodeodm`
  (image 3.0 GB, NodeODM/ODM 3.6.2, container sees 16 GB RAM and 14 cores).
- Client: init → upload (one file per request) → commit → poll `/info` → download `all.zip` and unzip into
  `odm/<run>/`, plus `odm_log.txt`. The run is recorded in `project.json` (`runs`), with options, timing and the
  list of GeoTIFF outputs.
- Default options: `camera-lens brown` (wide lens), `feature-quality high`, `min-num-features 10000`,
  `orthophoto-resolution 2`, `dsm`, `auto-boundary`.

## 2026-11-26 – First stitch (NodeODM) and the web app

**First stitch, clip 0028 (29 frames, run01).** Completed in 10 min. All 29 frames reconstructed in one
component, 9 846 points, reprojection error 1.2 px; orthophoto 7 MB.
**Bug found from the log:** `camera_lens: auto`, no DSM, so none of my options had been applied. NodeODM parses
`/task/new/init` as multipart form data (multer) and silently ignores URL-encoded fields. The client now sends
multipart, and `run_odm` reads back the options the task actually received (`options_applied`, plus
`options_ignored` if any are missing). run02 confirmed: `camera-lens brown`, `dsm`, `orthophoto-resolution 2`
all applied.

**Web app** (`app/main.py`, NiceGUI 4.1, `run_app.bat`): http://127.0.0.1:8080, bound to localhost only.
- Header: project selector, new project, open project folder.
- Map (Leaflet): OSM + Esri imagery; polygon/rectangle drawing sets the survey area; layers survey area, plan
  lines/footprints, flight tracks, selected frames, frame footprints, coverage classes (0 / 1–2 / 3–4 / 5+ views).
  It zooms to the project on open.
- **Plan** tab: load an area file (KML/GeoJSON/GPKG) or draw; camera, GSD-or-height toggle, overlaps, bearing,
  speed, battery, cross grid, more (margin, turn time, blur); Plan → stats table + warnings; download KML, PDF, GPKG.
- **Flights** tab: import a folder or file list (copy or reference), per-flight steps 1–4 (track QA, sharpness,
  select, extract + geotag) or Run 1–4, coverage of all flights; QA settings; per-clip QA table.
- **Stitch** tab: NodeODM URL + status check, main options, run name, Start; table of runs, folder links,
  error messages.
- **QA** tab: layer toggles, coverage legend and stats.
- Long steps run in a worker thread, one at a time; a progress bar at the bottom shows the current job.

**Testing.** `app/tools/ui_smoke.py` drives the installed Chrome with Playwright (a dev tool, not in requirements).
It creates a project, loads an area file, plans, draws a rectangle with the drawing tool, plans again, visits every
tab and saves screenshots; 0 browser console errors. Fixed along the way: zoom-to-fit was called before the map was
initialised, grey margins around the map, thin stripes between merged coverage cells (cells now buffered by
2 % before merging), underscores in names rendered as italics in tables.

**run02 (options applied) vs run01 (NodeODM defaults), clip 0028, 29 frames.**

| Run | Lens model | Frames placed | Points | Reprojection error | Time | Outputs |
|---|---|---|---|---|---|---|
| run01_defaults | auto | 29/29 | 9 846 | 1.22 px | 10.1 min | orthophoto |
| run02_brown_dsm | brown | 29/29 | 10 352 | 1.17 px | 14.4 min | orthophoto + DSM |

The Brown lens model gives slightly more points and a slightly lower error. The DSM adds about 4 minutes. ODM reports
an average GSD of 1.3 cm against our 2.1 cm estimate for 30 m. **Not explained yet.** To check: ODM's estimated
focal length/sensor in `cameras.json`, and the frames' heights above the reconstructed surface (trees, frames tilted
up to 30° from nadir).

## 2026-11-26 – Acquisition workflow

`05_acquisition_workflow.md`: the field procedure from planning to delivery, built on the tool and on what the test
data showed: storage 0.43 GB/min (57 Mbit/s), dusk ISO 3400, barometric drift and take-off height, gimbal
tolerance, processing ≈ 1.7 × video length, stitch times. Figures not yet verified in a real survey (300-frame stitch
time, phone control range, wind limit) are marked as planning values to check on the first survey.

## 2026-11-27 – Self-built drone: simulator guide

The user asked whether an app could fly the Neo through the RC-N3. Conclusion: not realistically (no SDK, closed
and probably encrypted protocol, likely no waypoint mode in the Neo). The new learning project is a self-built
ArduPilot survey quad. First step: `06_simulator_guide.md` (+ PDF). It covers installing ArduPilot's Mission
Planner app, starting SITL, and five exercises (orientation, GUIDED flight, survey grid in AUTO, wind + geofence,
flight logs).
`app/tools/md_to_pdf.py` converts any `_doc` Markdown file to an A4 PDF with headless Chrome. Note for writing
docs: Python-Markdown needs **4-space indentation** for nested lists.
