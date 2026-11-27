# ArduPilot simulator – step-by-step guide and first exercises

*A learning guide for the self-built survey drone project. No hardware is needed.*

**Names.** *Mission Planner* (two words) in this guide is **ArduPilot's ground-control app**, not our own
MissionPlanner survey tool. The simulator is called **SITL** ("software in the loop"). It is the real ArduPilot
flight software running on your PC against a physics model, so everything you learn here works the same way on a
real drone.

Menu names can differ slightly between Mission Planner versions. If a button is not exactly where this guide
says, look for the same word nearby.

---

## Part A – Install and start

### A1. Install Mission Planner (about 10 min)
1. Download the Windows installer (`MissionPlanner-latest.msi`) from the ArduPilot website:
   **ardupilot.org → Downloads → Mission Planner**.
2. Run it with the default options. Accept any driver installation; the drivers are needed later for a real
   flight controller over USB.
3. Start **Mission Planner**. The first start can take a minute. Allow it through the Windows firewall
   (private networks).

### A2. Start a simulated quadcopter
1. Click **SIMULATION** in the top menu bar.
2. Select the **Multirotor** (quadcopter) icon. If asked for a version, choose **Stable**.
3. Mission Planner downloads the simulator (first time only), starts it and **connects automatically**. The
   connection is a network port on your PC (TCP 5760) instead of USB.
4. Switch to **DATA** (Flight Data). You should see:
    - the **HUD** (artificial horizon) top left, showing *DISARMED*;
    - the **map**, with the drone at the ArduPilot test field in Australia (the default simulator home);
    - the GPS status (3D fix) and the battery voltage.

> **Check:** the HUD moves slightly and the status shows a GPS fix. If nothing connects, close Mission Planner and
> start it again, then repeat A2.

**Optional – start somewhere else.** The simulator can start at another location: the Simulation screen has a
field for extra command-line options, where `--home=LAT,LON,ALT_m,HEADING` sets the start point. The default
field is fine for these exercises.

---

## Part B – Exercises

Do them in order. Each one ends with a short **check** of what you should have learned.

### Exercise 1 – Look around (10 min)
1. In **DATA**, find the tabs under the HUD: **Quick**, **Actions**, **Messages**, **Status**,
   **DataFlash Logs**.
2. **Quick** shows altitude, ground speed, distance to home and so on. Double-click a value to choose a different
   one.
3. **Messages** shows what the flight software reports: pre-arm checks, mode changes, warnings.
4. **Status** lists every live value (hundreds). Find `battery_voltage`, `gpsstatus`, `satcount`.

**Check:** you know where to read altitude, mode, GPS and battery, and where warnings appear.

### Exercise 2 – First flight in GUIDED mode (15 min)
GUIDED = the drone goes where you click on the map.

1. **Actions** tab → mode drop-down → **GUIDED** → **Set Mode**.
2. **Arm/Disarm** → the HUD shows *ARMED*. The motors are now "spinning", and the drone disarms itself after a
   few seconds if you don't take off.
3. Right-click on the map → **Takeoff** → enter **20** (m) → OK. Watch the altitude climb to 20 m.
4. Right-click on the map ~100 m away → **Fly To Here**. Watch speed and heading.
5. Right-click → **Fly To Here Alt** → change the altitude to 30 m.
6. **Actions** → mode **RTL** → **Set Mode**. RTL (return to launch) flies home, descends and lands, then
   disarms.

**Check:** you can arm, take off, move the drone, and bring it home. RTL is the "come home" safety mode you'll
use most.

### Exercise 3 – A survey grid mission (30 min)
This is the core of the project: an automatic lawn-mower pattern with camera triggers.

1. Go to **PLAN** (Flight Plan). Zoom to a field near the drone's home (within ~300 m).
2. Right-click on the map → **Polygon** → **Draw a Polygon**. Click 4–6 corners around an area of about
   150 × 100 m. Right-click when done if needed.
3. Right-click inside the polygon → **Auto WP** → **Survey (Grid)**. In the survey window:
    - **Camera:** pick any camera from the list. We're learning the workflow here, not the exact numbers; a real
      camera definition comes with the real build.
    - **Altitude:** 30 m. **Overlap:** 80 %, **Sidelap:** 70 %. (These match our Neo plans.)
    - **Angle:** try the auto value, then change it and watch the lines rotate.
    - Note the figures shown: number of pictures, flight time, distance between lines and between photos.
    - **Accept**.
4. The waypoint list at the bottom now holds the grid, including a `DO_SET_CAM_TRIGG_DIST` command. This command
   makes the drone trigger the camera every *N* metres: the photo equivalent of our frame selection.
5. Make the mission end safely: select the last row → **Add Below** → set the command to
   **RETURN_TO_LAUNCH**.
6. **Write WPs** (right side). The mission is uploaded to the "drone"; Messages confirms it.
7. Fly it:
    1. **DATA** → **Actions** → **GUIDED** → **Arm** → right-click map → **Takeoff** 30 m.
    2. When at 30 m: mode **AUTO** → **Set Mode**. The drone flies the grid on its own.
    3. Watch the turns, the speed, the line spacing, and the camera-trigger events in Messages.
    4. At the end it returns and lands by itself (RTL).

**Check:** you can plan a survey from a polygon, upload it and fly it automatically, and you know which settings
control GSD, overlap and flight time.

### Exercise 4 – Wind and a geofence (20 min)
Real conditions and a safety net.

1. **CONFIG** → **Full Parameter List** → search `SIM_WIND`. Set **SIM_WIND_SPD = 6** (m/s) and
   **SIM_WIND_DIR = 270** (wind from the west) → **Write Params**. Parameters starting with `SIM_` exist only in
   the simulator.
2. Fly the survey again (Exercise 3, step 7). Watch:
    - the drone leaning into the wind (HUD roll/pitch);
    - different ground speed upwind vs downwind;
    - how well it still holds the lines. That's the GPS position control at work.
3. Set a geofence: in the parameter list set **FENCE_ENABLE = 1**, **FENCE_TYPE** to include *circle*,
   **FENCE_RADIUS = 150** (m), **FENCE_ACTION = RTL** → Write Params.
4. In GUIDED, take off and **Fly To Here** at a point ~250 m away. When the drone crosses 150 m, the fence
   triggers: the Messages tab reports the breach and the drone returns home.
5. Reset: **SIM_WIND_SPD = 0**, and leave the fence on. You'll want it on the real drone too.

**Check:** you've seen wind effects and a failsafe in action, and you can find and change parameters.

### Exercise 5 – Flight logs (15 min)
Logs are how you find out what went wrong on a real drone.

1. **DATA** → **DataFlash Logs** tab → **Download DataFlash Log Via Mavlink** → pick the latest log →
   download.
2. **Review a Log** → open the downloaded `.bin` file. A graph window opens.
3. Plot a few values. Tick them in the tree on the right:
    - `CTUN.Alt` and `CTUN.DAlt` → actual vs desired altitude;
    - `GPS.Spd` → ground speed (compare upwind and downwind from Exercise 4);
    - `ATT.Roll`, `ATT.DesRoll` → how well the drone follows its commands;
    - `CAM` messages → one row per camera trigger, with position and attitude. This is the photo geotag data our
      stitching pipeline will use.

**Check:** you can download a log and plot altitude, speed, attitude and camera events.

---

## Part C – Optional extras

- **Fly by hand with a transmitter.** A radio transmitter (e.g. an ExpressLRS radio) connected by USB acts as a
  joystick. In DATA → Actions → **Joystick**, set it up, then practise flying in **LOITER** mode: the drone holds
  position when you let go of the sticks. This is good practice before the real first flight.
- **Battery failsafe.** Find the `BATT_FS_*` and `SIM_BATT_*` parameters and make the simulated battery run low.
  The drone should return home by itself.
- **Plan your own area.** Draw the polygon of a site you know (move the simulator home there first) and compare
  Mission Planner's grid with the plan from our MissionPlanner tool for the same GSD and overlaps.

---

## Part D – What you should know before buying parts

| Topic | You can… |
|---|---|
| Modes | explain GUIDED, AUTO, RTL, LOITER, LAND and when to use each |
| Missions | draw a polygon, make a survey grid, add RTL, upload, fly |
| Survey settings | relate altitude, camera, overlap and sidelap to GSD, photos and flight time |
| Safety | set a geofence and RTL; know what happens on low battery or lost link |
| Parameters | find, change and save parameters; know that `SIM_*` is simulator-only |
| Logs | download a log; plot altitude, speed, attitude and camera triggers |

When you are comfortable with these, the next step is choosing the real parts. After that, I can connect the
simulator to our MissionPlanner tool: export our KML plans as ArduPilot missions and test them in the simulator
before they ever fly.
