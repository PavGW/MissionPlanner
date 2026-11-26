# 02 – Design

## Overview

```
                 ┌──────────────── MissionPlanner web app (NiceGUI, http://localhost:8080) ───────────────┐
 survey polygon ─┤ 1 PLAN     polygon + camera + GSD/height + overlaps → flight lines, stats             ├─► KML, PDF cheat sheet, GPKG
 (draw/import)   │ 2 FLY      (pilot flies by hand, Neo records 4K video + SRT)                          │
 MP4 + SRT ─────►│ 3 TRACK    djmd (MP4) / SRT telemetry → per-frame points, track line, QA flags         ├─► GPKG layers
                 │ 4 FRAMES   select frames by ground spacing + sharpness + nadir check → JPG + EXIF GPS  ├─► frames/*.jpg
                 │ 5 QA       footprints, overlap grid, coverage vs. survey area, gaps, statistics        ├─► GPKG + HTML/PDF report
                 │ 6 ODM      upload to NodeODM (Docker), poll, download orthophoto/DSM/point cloud       ├─► odm/<run>/
                 └──────────────────────────────────────────────────────────────────────────────────────────┘
```

The processing logic lives in a plain Python package (`app/missionplanner/`) that has no GUI dependency, so every step
can also be scripted and tested from the command line. The web UI is a thin layer on top of it.

## Project folder (one per survey)

```
projects/<name>/
  project.json            settings: camera profile, planner parameters, CRS (EPSG), extraction parameters
  <name>.gpkg             all spatial data (see layers below)
  flights/                MP4 + SRT copied/linked from the drone (one sub-folder per flight)
  frames/<flight>/        extracted, geotagged JPG frames
  exports/                KML, cheat-sheet PDF, QA report
  odm/<run>/              NodeODM results (orthophoto.tif, dsm.tif, report)
```

### GeoPackage layers

| Layer | Geometry | Content |
|---|---|---|
| `survey_area` | Polygon | area to map (drawn in the app or imported) |
| `plan_lines` | LineString | planned flight lines: line no., direction, length, height, speed |
| `plan_points` | Point | line start/end points (numbered for the pilot) |
| `plan_footprints` | Polygon | expected photo footprints at planned spacing (for the plan coverage check) |
| `track_points` | Point | every telemetry frame: time, alt, attitude, gimbal pitch, speed, flags |
| `track_lines` | LineString | flight path, one per clip |
| `frames` | Point | selected frames: file, sharpness, pitch, height, spacing, status |
| `footprints` | Polygon | ground footprint of each selected frame |
| `coverage` | Polygon | overlap-count grid cells (number of frames seeing each cell) |
| `runs` | – (table) | NodeODM runs: task id, options, status, outputs |

All layers are stored in the project's UTM zone (auto-detected from the first polygon or track, EPSG 326xx/327xx).

## Camera model (DJI Neo, measured from test data)

| Item | Value | Source |
|---|---|---|
| Photo size | 4000 × 2256 (16:9) | JPG |
| Video size | 3840 × 2160, 29.97 fps, HEVC | ffmpeg probe |
| Focal length | 2.598 mm (14 mm 35 mm-equivalent) | JPG EXIF |
| Sensor width (16:9 crop) | ≈ 7.0 mm (derived: diagonal from 35 mm equivalence) | computed |
| FOV horizontal / vertical | ≈ 106.8° / 74.3° | computed from 14 mm equivalent, 16:9 |
| Ground footprint | ≈ 2.68 H × 1.51 H | |
| GSD (video) | ≈ 0.70 mm per m of height → 2.1 cm/px at 30 m | |

The video is assumed to have the same field of view as the photo (no extra stabilisation crop). This will be
verified by ODM's self-calibration on the first real survey and can be edited in the camera profile.

## Telemetry sources
1. **djmd track inside the MP4** (preferred): protobuf per frame with position (radians, double), absolute and
   relative altitude, **drone attitude** and **gimbal attitude** (0.1°). Field map identified empirically, see
   `03_dev_log.md`.
2. **SRT** (fallback): position, altitudes, exposure (ISO, shutter, f-number, EV, colour temperature).
   Exposure is only in the SRT, so both are read and merged by frame index.

## Planner
- Inputs: survey polygon, camera profile, either *target GSD* or *flight height*, front/side overlap,
  line direction (auto = along the polygon's longest axis, or given), margin outside the polygon, flight speed
  limit, optional cross-hatch grid.
- Line spacing `= footprint_across × (1 − side_overlap)`, frame spacing `= footprint_along × (1 − front_overlap)`.
- Because the pilot keeps the drone's nose (and the camera's long image side) **across** the line or **along** it,
  the orientation is a setting: *landscape across track* (default, widest swath → fewest lines) or *along track*.
- Speed: limited so that motion blur during the exposure stays below 0.5 px at a conservative shutter (1/500 s)
  and so that ≥ 3 candidate video frames exist per required frame spacing.
- Outputs: lines in boustrophedon (snake) order, statistics (lines, total length, flight time incl. turns,
  number of frames, area, battery count at a configurable usable flight time).

## Frame selection
1. Merge djmd + SRT per frame, compute ground speed and along-track distance.
2. Reject frames: no GPS, gimbal pitch not within ±`tol` of −90° (default 5°), drone tilt > limit, height outside
   the planned band, speed ≈ 0 (hover → duplicates), turns (yaw rate high).
3. Walk along the track and choose a frame every `spacing` metres; within each spacing window take the frame
   with the **highest sharpness** (variance of Laplacian on a down-scaled grey image).
4. Decode the chosen frames at full resolution (OpenCV / ffmpeg) and save as high-quality JPG.
5. Write EXIF: GPS lat/lon/alt (absolute), DateTimeOriginal (+ sub-seconds), Make/Model (DJI / FC8671),
   FocalLength 2.598 mm, FocalLengthIn35mmFilm 14, image size, plus gimbal pitch/yaw in the XMP block in the
   DJI `drone-dji:` namespace, which ODM reads.

## Coverage / QA
- Footprint of each frame from its position, height above ground, heading (drone yaw) and the camera FOV.
  The ground is treated as flat at the take-off level (relative altitude). A DEM can be added later.
- Overlap grid (default 2 m cells): number of footprints that cover each cell. Report the share of the survey area
  with ≥ 5 views (good), 3–4 (marginal) and < 3 (gap).
- Flight QA: share of frames rejected and why, height and speed statistics, gimbal pitch histogram.

## ODM
- NodeODM in Docker (`opendronemap/nodeodm`, port 3000), started from the app when Docker Desktop is running.
- REST: `POST /task/new/init` → `POST /task/new/upload/<uuid>` (images) → `POST /task/new/commit/<uuid>` →
  poll `GET /task/<uuid>/info` → `GET /task/<uuid>/download/all.zip`.
- Default options for wide-angle video frames: `camera-lens=brown`, `feature-quality=high`,
  `orthophoto-resolution` = 2 × GSD, `fast-orthophoto` as an option for quick previews.

## Technology
Python 3.14 venv; NiceGUI (web UI with Leaflet map and drawing tools); pyogrio/GDAL for GeoPackage;
shapely + pyproj for geometry; OpenCV + bundled ffmpeg for video; piexif for EXIF; reportlab for the PDF;
simplekml for KML; requests for NodeODM.
