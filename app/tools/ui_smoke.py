"""UI smoke test of the running web app (dev tool, needs `pip install playwright` and an installed Chrome).

    python app/tools/ui_smoke.py [url] [out_dir]

Creates a project 'ui_smoke' (generic test area, no real site), loads an area file, plans, draws a new area with
the rectangle tool, re-plans, visits every tab and saves screenshots."""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "_work/ui_smoke")
OUT.mkdir(parents=True, exist_ok=True)

# ~4 ha generic rectangle
lon0, lat0, dx, dy = 15.0, 50.0, 250 / 71600, 160 / 111200
area = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
    "type": "Polygon", "coordinates": [[[lon0, lat0], [lon0 + dx, lat0], [lon0 + dx, lat0 + dy], [lon0, lat0 + dy],
                                        [lon0, lat0]]]}}]}
area_file = OUT / "area.geojson"
area_file.write_text(json.dumps(area))

errors = []
with sync_playwright() as pw:
    br = pw.chromium.launch(channel="chrome", headless=True)
    pg = br.new_page(viewport={"width": 1500, "height": 900})
    pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
    pg.on("pageerror", lambda e: errors.append(str(e)))

    def shot(name):
        time.sleep(1.5)
        pg.screenshot(path=str(OUT / f"{name}.png"))
        print("shot", name)

    def wait_idle(timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            txt = pg.locator(".q-card .text-caption").first.inner_text()
            if "done" in txt or "failed" in txt or "Error" in txt or txt == "idle":
                return txt
            time.sleep(0.5)
        return "timeout"

    pg.goto(URL)
    pg.wait_for_selector(".leaflet-container")
    time.sleep(4)
    shot("01_open_existing")

    # new project
    pg.get_by_placeholder("new project name").fill(f"ui_smoke_{time.strftime('%H%M%S')}")
    pg.locator("header button").first.click()
    time.sleep(2)
    # load the area file through the uploader
    pg.locator(".q-uploader input[type=file]").set_input_files(str(area_file))
    time.sleep(3)
    pg.get_by_role("button", name="Plan", exact=True).click()
    time.sleep(1)
    print("plan:", wait_idle())
    shot("02_plan")

    # draw a rectangle with leaflet-draw inside the current view
    pg.locator(".leaflet-draw-draw-rectangle").click()
    box = pg.locator(".leaflet-container").bounding_box()
    x0, y0 = box["x"] + box["width"] * 0.30, box["y"] + box["height"] * 0.25
    pg.mouse.move(x0, y0)
    pg.mouse.down()
    pg.mouse.move(x0 + box["width"] * 0.3, y0 + box["height"] * 0.35, steps=10)
    pg.mouse.up()
    time.sleep(2)
    pg.get_by_role("button", name="Plan", exact=True).click()
    time.sleep(1)
    print("plan 2:", wait_idle())
    shot("03_drawn_plan")

    for tab in ("Flights", "Stitch", "QA"):
        pg.get_by_role("tab", name=tab).click()
        if tab == "Stitch":
            pg.locator("button", has_text="wifi_find").click()
        shot(f"04_{tab.lower()}")

    # back to the existing project with flights + runs
    pg.locator("header .q-select").click()
    pg.get_by_role("option", name="test_0028").click()
    time.sleep(4)
    for tab in ("Flights", "Stitch", "QA"):
        pg.get_by_role("tab", name=tab).click()
        shot(f"05_test0028_{tab.lower()}")
    br.close()

print("console errors:", errors or "none")
