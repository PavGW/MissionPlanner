# 01 – Requirements (from the interview, 2026-11-26)

## Goal
Plan and process **hand-flown aerial survey campaigns with a DJI Neo**. The Neo cannot fly waypoint missions
(no waypoint mode in DJI Fly, no DJI Mobile SDK support), so the pilot flies the planned lines manually while
recording 4K video. Survey images are then **extracted from the video** using the per-frame telemetry in the
DJI `.SRT` file, geotagged, and stitched with OpenDroneMap.

Photo interval mode was considered and rejected in favour of video: the SRT gives a position for every frame
(29.97 fps), so frames can be chosen by ground distance and sharpness instead of by a fixed timer.

## Decisions

| Topic | Decision | Notes |
|---|---|---|
| Scope v1 | Flight planner, SRT → flight track, frame extraction + geotag, coverage/QA report | all four |
| Stitching | **NodeODM** (headless ODM in Docker), driven by the app over its REST API | Docker Desktop is installed but stopped |
| Interface | **Local web app** (browser at `localhost`) with an interactive map | user asked for a viability check of a Python GUI + local database → assessed, see below |
| Database | **GeoPackage** per project (no server) | Postgres/PostGIS assessed and judged unnecessary for a single user; keep the storage layer thin so it can be swapped later |
| Planner input | **Either** target GSD or flight height (switchable) | the other is computed |
| Field outputs | **KML** (phone), **printable PDF cheat sheet**, **GeoPackage** layers (QGIS/QField) | all three |
| CRS | **Auto UTM zone** (WGS84) from the survey location | works for Czechia (33N) and Australia |
| GCPs | **Later** – GPS-only first, keep a hook for GCP import / ODM `gcp_list.txt` | |
| Test data | **All clips on the drone** copied to `test_data/raw/` (16 MP4 + SRT, 23 JPG, MISC) | 5.5 GB, gitignored |
| Version control | Local git + GitHub (user creates the repo, I push) | media and outputs gitignored |
| Documentation | `_doc/` – thinking, steps, results; at the end an acquisition workflow | |

## Viability assessment – Python GUI + local database
- **Feasible.** Machine: Core Ultra 7 165U, 31 GB RAM, Python 3.14, Docker Desktop, QGIS 3.40.5.
  All dependencies install cleanly on Python 3.14 (NiceGUI, OpenCV 5.1, pyogrio/GDAL 3.12, pyproj, shapely,
  bundled ffmpeg via `imageio-ffmpeg`).
- **Web app vs desktop GUI:** a local web app (NiceGUI + Leaflet map) gives drawing tools, map layers and
  background jobs with much less code than a Qt desktop app.
- **Postgres vs GeoPackage:** Postgres/PostGIS would add a server, Docker dependence and backups. For one user
  on one PC a GeoPackage per project stores the same spatial tables, opens directly in QGIS and travels with the
  project folder. → GeoPackage chosen by the user.
- **Compute:** frame decoding of 4K HEVC and NodeODM runs are the heavy parts; small ODM jobs (a few hundred
  images) are within the laptop's capacity.
