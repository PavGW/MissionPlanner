# 05 – Data acquisition workflow (DJI Neo, hand-flown video survey)

From a survey area to an orthophoto and surface model. The Neo cannot fly waypoints, so the tool plans the lines,
you fly them by hand while recording 4K video, and the tool picks, geotags and stitches the frames.

```
 DESK                     FIELD                                  DESK
 1 Plan  ──► 2 Prepare ──► 3 Site check ──► 4 Fly ──► 5 Field QA ──► 6 Process ──► 7 Stitch ──► 8 Deliver
 (Plan tab)  (KML, PDF,    (wind, light,    (video,   (Track QA      (sharpness,   (NodeODM)    (QGIS)
              batteries)    take-off)        lines)    on laptop)     frames)
```

Figures below use the default plan: 2 cm/px, 80 % front and 70 % side overlap. Values marked *(measured)* come
from the test data or the test runs; the rest are planning figures and should be checked on the first real survey.

---

## 0. One-time setup

| Item | How |
|---|---|
| App | `run_app.bat` in the MissionPlanner folder → http://127.0.0.1:8080 |
| Stitching | Docker Desktop running; `docker run -d --name nodeodm -p 3000:3000 --restart unless-stopped opendronemap/nodeodm` (once; it then starts with Docker) |
| Phone | An app that opens KML files offline (e.g. Google Earth, or any offline map app with KML import) |
| Legal | Operator registration and pilot competency as required for your category; know the local geo-zone app (in Czechia: the ŘLP *Dronview* map) |

---

## 1. Plan (desk, Plan tab)

1. Create a project and draw the survey area, or load it from KML/GeoJSON/GPKG.
2. Choose **Target GSD** (usually) or **Flight height**. Rules of thumb for the Neo 4K video:

   | GSD | Height above take-off | Footprint | Line spacing (70 %) |
   |---|---|---|---|
   | 1.5 cm | ~21 m | 58 × 32 m | ~17 m |
   | 2.0 cm | ~28 m | 77 × 43 m | ~23 m |
   | 3.0 cm | ~43 m | 115 × 65 m | ~35 m |

   Fly **at least 10 m above the tallest trees and buildings**. If they are close to the planned height, raise the
   height and accept a coarser GSD.
3. Keep the default overlaps (80/70 %). Add **Cross grid** if you need a good surface model (DSM) or the site has
   steep terrain or buildings.
4. Press **Plan** and check:
   - **Flight time and batteries.** Plan 12 min of usable time per battery and leave a reserve.
   - **Max distance from centre.** It must stay within the control range of your setup. With a phone-only Wi-Fi
     connection the Neo's range is short, so check the DJI spec for your control mode and stay well inside it.
   - **Warnings** (height limits, speed limited by motion blur).
5. Download the **KML** to your phone and print the **PDF cheat sheet**. Note line 1 start and the take-off point.

**Large areas.** Split them into several projects or flights of one battery each. Give neighbouring blocks one
line of overlap.

---

## 2. Prepare (day before)

- [ ] All batteries charged; phone (or controller) charged.
- [ ] Free storage. 4K video uses about **0.43 GB per minute** *(measured, 57 Mbit/s)*, so a 12-minute flight takes
      about 5 GB. Copy old clips off the drone first.
- [ ] Geo-zones and restrictions checked for the date; land-owner permission if needed.
- [ ] Weather: wind **below ~5 m/s** at flight height (gusts tilt the drone and push it off the line); no rain.
- [ ] Light: ideally **overcast**, or sun high (late morning to early afternoon). Avoid low sun: long shadows
      move between lines and confuse matching. The dusk test clips reached ISO 3400 *(measured)*: noisy, blurred frames.
- [ ] KML open offline on the phone; cheat sheet printed.

---

## 3. Site check (on arrival)

1. Walk or scan the area for people, wires, trees taller than expected and birds. Pick a take-off point near the
   **start of line 1**, on ground typical of the site.
2. **Take-off height matters.** The Neo's height is relative to the take-off point. If you take off from a hill
   above the site, the real height above the site is larger (coarser GSD). If you take off from a hollow, it is
   smaller, with a collision risk. Note the difference roughly. It goes into *Take-off above ground* in the app.
3. Calibrate the compass if the app asks. **Wait for a GPS fix** and the home point before take-off.

---

## 4. Fly

**Camera settings (DJI Fly):**

| Setting | Value | Why |
|---|---|---|
| Mode | Video, 4K, 30 fps | frames are taken from the video |
| Gimbal | **−90°** (straight down) | frames tilted more than 5° from nadir are rejected by default |
| Exposure | Fast shutter: **1/500 s or faster** if manual exposure is available; otherwise auto, and fly slower in dull light | motion blur |
| Subtitles/SRT | On (default) | extra telemetry check |

**Speed vs shutter (2 cm GSD, max 0.5 px blur):**

| Shutter | 1/100 | 1/250 | 1/500 | 1/1000 |
|---|---|---|---|---|
| Max speed | 1 m/s | 2.5 m/s | 5 m/s | 10 m/s |

The shutter is in the DJI Fly display (and in the SRT). In bright sun it is usually 1/500 or faster. Use the
**planned speed** from the cheat sheet unless the shutter is slower than 1/500.

**Line procedure:**

1. Climb to the planned height **outside the area**, near the start of line 1. Set the gimbal to −90°.
2. **Start recording.** One recording per battery is fine. The tool splits it into lines automatically.
3. Turn to the line heading (cheat sheet), **nose forward**: fly forwards along the line, not sideways. The
   planned footprint assumes the long image side across the track.
4. Fly the line **straight, level and at constant speed**. Use the phone's KML map, landmarks or the drone's
   position on the map to stay on the line. Small offsets (a few metres) are fine; 70 % side overlap tolerates
   about ±7 m at 2 cm GSD.
5. At the end, continue past the area (lead-in), **stop, turn slowly**, move sideways by one line spacing, settle,
   then fly back. Frames taken while turning are rejected automatically, so don't rush the turn.
6. Tick the line on the cheat sheet. Watch the battery: **land at about 25 %**, change battery, resume at the next
   line (start a new recording).
7. Keep the drone in **visual line of sight** at all times. Abort a line rather than lose sight.

---

## 5. Field QA (before leaving the site)

If a laptop is with you, do this on site. A re-flight costs minutes now and a full trip later.

1. Copy the clips (`D:\DCIM\DJI_001\*.MP4` + `.SRT`) to the laptop. Also keep a copy on the drone until home.
2. In the app, **Flights → Import**, then **1 Track QA** (seconds per clip). Check the per-clip table:

   | Column | Good | If not |
   |---|---|---|
   | nadir % | > 80 % of the line time | gimbal was not at −90° → re-fly |
   | h m (min–max) | close to planned height on the lines | climb/descend issues → re-fly affected lines |
   | usable % | > 50 % (turns and climbs are excluded) | many tilt/turn flags → wind or rushed turns |

3. On the map, check that the **flight track covers every planned line**. Re-fly missing or short lines.
4. Optional, if time allows: run steps 2–4 and **Coverage**. Fly extra lines over red (0 views) and orange (1–2)
   patches inside the area. Note: steps 2–4 take about as long as the video itself, twice over (see 6).

---

## 6. Process (desk, Flights tab)

Per flight: **Run 1–4**, or step by step:

| Step | What | Time *(measured, software decoding ~36 fps)* |
|---|---|---|
| 1 Track QA | merge telemetry, flag frames | seconds |
| 2 Sharpness | decode all candidate frames, score sharpness | ≈ 0.9 × video length |
| 3 Select frames | one sharpest frame per overlap step | < 1 s |
| 4 Extract + geotag | decode again, write JPG + GPS/attitude EXIF/XMP | ≈ 0.8 × video length |

Then **Coverage of all flights** and check the QA tab: aim for **≥ 5 views on > 90 %** of the survey area.

Settings to adjust if needed (QA / selection settings):
- *Nadir tol*: 5° default. Raise it to 10–15° only if the gimbal was not quite vertical. The test flight needed
  35°, because it was not flown as a survey.
- *Frame overlap*: 80 % default. Raise it to 85–90 % for more frames over complex terrain or vegetation.
- *Min rel. sharpness*: 0.6. Lower it if too many frames are dropped in dull light.
- *Take-off above ground*: from step 3 of the site check.

---

## 7. Stitch (Stitch tab)

1. Check that NodeODM is reachable (the button next to the URL). Docker Desktop must be running.
2. Defaults: orthophoto at the plan GSD, features *high*, point cloud *medium*, DSM on. Use *Fast ortho* for a quick
   preview, without 3D.
3. **Start stitch**. Results go into `projects/<name>/odm/<run>/`.
   *(measured)* 29 frames: 10 min (orthophoto only) to 14 min (with DSM), on 14 cores and 16 GB RAM. A full
   survey of ~300 frames will take longer and has not been measured yet. It may need more memory for Docker.
4. Check `odm_report/report.pdf`: all images placed (*reconstructed shots* = number of frames), reprojection
   error around 1–2 px, one component. If images are missing, check coverage and sharpness for those areas.

---

## 8. Deliver

| Product | File | Use |
|---|---|---|
| Orthophoto | `odm/<run>/odm_orthophoto/odm_orthophoto.tif` | GeoTIFF (UTM) for QGIS |
| Surface model | `odm/<run>/odm_dem/dsm.tif` | heights incl. trees/buildings (relative accuracy good; absolute ± GPS error) |
| Point cloud | `odm/<run>/entwine_pointcloud/` | 3D viewing |
| QA layers | `projects/<name>/<name>.gpkg` | plan, tracks, frames, footprints, coverage |

**Accuracy.** The Neo's GPS is not survey grade. Expect the products to be **shifted by a few metres** as a whole,
and heights to be offset by several metres. Shapes and relative distances are good. For absolute accuracy you need
ground control points (planned for later: the frame and stitch steps keep a hook for GCPs).

---

## 9. Data management

- One project per site and date: `projects/<site>_<yyyymmdd>/`. The original clips are copied into `flights/`.
- Keep the **original MP4 + SRT** permanently. Everything else can be regenerated.
- Back up `project.json`, the GeoPackage and the final GeoTIFFs. `frames/` and `odm/*/all.zip` can be deleted
  after delivery to save space.
- Projects and test data are never committed to git (see `.gitignore`).

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Few frames selected | gimbal not at −90°, flying too low, many turns | check Track QA flags; relax nadir tolerance a little; re-fly |
| Frames blurred | slow shutter (dull light), flying too fast | fly slower, fly in better light |
| Gaps in coverage (red) | lines missed or drifted, height too low | fly extra lines over the gaps |
| ODM places only part of the images | poor overlap or texture (water, crops, snow) | more overlap (85–90 %), cross grid, fly higher |
| ODM fails: not enough memory | large set vs Docker RAM | raise the WSL/Docker memory limit, or process in blocks |
| Heights negative / wrong | barometric drift; take-off higher than the site | set *Take-off above ground*; short flights per battery |
| NodeODM not reachable | Docker Desktop not running | start Docker Desktop; the container restarts by itself |
