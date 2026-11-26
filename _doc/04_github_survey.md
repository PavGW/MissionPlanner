# 04 – GitHub survey: open-source projects targeting the DJI Neo (2026-11-26)

Done by a research sub-agent. It used the GitHub REST API and web search, read the top 19 of the 41 repositories
returned for "dji neo", and verified claims against each README or its source. Star counts and dates are as of
2026-11-26.

## Programmatic / waypoint flight – conclusion
**No official SDK or public project currently gives the Neo autonomous or waypoint flight.**
- DJI Mobile SDK v5 (latest 5.20.1): Neo and Neo 2 are not supported products. Of the consumer models it covers
  only the Mini 3 / Mini 3 Pro / Mini 4 Pro.
- No Litchi, Dronelink or DroneDeploy support for the Neo. An ODM community thread (2025) concludes that people
  fly grids by hand, using cruise control:
  https://community.opendronemap.org/t/mapping-with-a-dji-neo-and-manual-flight/24350
- The closest projects reverse-engineer **stick-level** control:
  - `lslv1243/neo-control` (iOS + gamepad, original Neo): DUML over BLE and UDP 192.168.2.1:9003. Telemetry and video go
    only to an account-bound controller, so it flies without position feedback and closed-loop waypointing is
    not possible with it.
  - `smichea/dji-neo2-lan-client` (Neo 2 only, experimental): decodes some telemetry, adds virtual stick.

→ This confirms the design choice: **plan + fly by hand + extract frames from video**.

## Relevant projects

| Repo | What it does for the Neo | Maturity |
|---|---|---|
| exiftool/exiftool (`DJI.pm`) | `dvtm_dji_neo` tag table: GPS, absolute altitude, drone attitude, ISO, shutter, colour temperature | mature; Neo tags mostly "not confirmed" |
| AdrianEddy/telemetry-parser (Gyroflow) | lists Neo and Neo 2; parses the generic `dvtm_wm169.proto` (camera + IMU quaternion, no GPS or gimbal) | mature |
| lslv1243/neo-control | stick control via reverse-engineered DUML | early |
| smichea/dji-neo2-lan-client | Neo 2 video, telemetry, virtual stick (Linux/Python) | experimental |
| o-gs/dji-firmware-tools, linnin233/dji-neo2-tools | DUML/firmware tooling; the fork reads/writes Neo 2 flight-controller parameters | mature / small |
| Penkov-D/DJI-Neo-to-PC | drives DJI Fly on Android from a PC (ADB touch injection) | hack |
| YarosMallorca/DJI-Mapper | survey mission planner for waypoint-capable DJI models (not Neo) | reference for planner features |

No Neo-specific ODM tooling, FC8671 calibration, Gyroflow lens profile or public `dvtm_dji_neo.proto` was found.

## Cross-check of our djmd field map

| Our map | ExifTool / DJI proto | Result |
|---|---|---|
| 1.2 timestamp µs | `3-1-2` TimeStamp | agrees |
| 4.4.1.2/.3 lat/lon (double, rad) | GPSLatitude/Longitude; **4.4.1.1 = CoordinateUnits (0 rad, 1 deg)** | agrees → reader now honours 4.4.1.1 |
| 4.4.2 abs alt mm | AbsoluteAltitude /1000 | agrees |
| 4.5.1 rel alt mm (float) | RelativeAltitude in the Avata 2 table | agrees |
| 4.3.1/2/3 drone pitch/roll/yaw | ExifTool shared table: 1 = Roll, 2 = Pitch (not confirmed) | **conflict, resolved in our favour**: the Neo gimbal is single-axis (pitch), so gimbal roll follows drone roll. Recorded gimbal roll 5.2.2 tracks 4.3.2 (e.g. −12.8/−12.8, −15.2/−15.3, −4.1/−3.9 in clip 0028) → 4.3.2 is **roll**, 4.3.1 is pitch |
| 5.2.1/2/3 gimbal pitch/roll/yaw | not in the Neo table; generic GimbalInfo has 1 = Pitch, 2 = Roll, 3 = Yaw | agrees (new for the Neo) |
| 2.3.1 ISO, 2.6.1 colour temperature | DJI `dvtm_wm169.proto` FrameMetaOfCamera: 3 = iso, 6 = white_balance_cct | agrees |

Other fields that ExifTool documents and could be added later: shutter `2.4.1` (rational), f-number `2.10.1`, frame
info `2-3` (width, height, fps).

Gimbal yaw vs drone yaw offset (≈ −20°): not documented anywhere. Still open, to be checked against ODM's computed
camera orientations.
