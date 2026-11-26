"""MissionPlanner local web app.  Run:  .venv\\Scripts\\python app\\main.py   ->  http://localhost:8080

One page: a map on the right, a tabbed control panel on the left (Plan / Flights / Stitch / QA).
Long steps run in a worker thread; one job at a time, with progress shown at the bottom of the panel."""
from __future__ import annotations

import os
import re
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import geopandas as gpd  # noqa: E402
from nicegui import run, ui  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from missionplanner import __version__  # noqa: E402
from missionplanner import exports, pipeline as pl  # noqa: E402
from missionplanner.camera import CAMERAS  # noqa: E402
from missionplanner.odm import DEFAULT_OPTIONS, DEFAULT_URL, NodeODM  # noqa: E402
from missionplanner.project import DEFAULT_ROOT, Project  # noqa: E402

ROOT = Path(os.environ.get("MISSIONPLANNER_PROJECTS", DEFAULT_ROOT))
ROOT.mkdir(parents=True, exist_ok=True)

# map styles per layer: (label, leaflet style, default visible)
STYLES = {
    "survey_area": ("Survey area", {"color": "#d4a000", "weight": 2, "fillOpacity": 0.15}, True),
    "plan_lines": ("Plan lines", {"color": "#d01010", "weight": 3}, True),
    "plan_footprints": ("Plan footprints", {"color": "#888", "weight": 0.5, "fillOpacity": 0.02}, False),
    "track_lines": ("Flight tracks", {"color": "#1060e0", "weight": 2}, True),
    "frames": ("Selected frames", {"color": "#00a050", "weight": 1, "fillOpacity": 0.8}, True),
    "footprints": ("Frame footprints", {"color": "#00a050", "weight": 0.5, "fillOpacity": 0.03}, False),
    "coverage": ("Coverage (views)", None, True),
}
COV_CLASSES = [(0, 0, "#d7191c", "0"), (1, 2, "#fdae61", "1-2"), (3, 4, "#a6d96a", "3-4"), (5, 10**6, "#1a9641", "5+")]


class Job:
    """Single background job with progress (shared by all browser tabs - this is a one-user local app)."""
    def __init__(self):
        self.name, self.progress, self.msg, self.running, self.error = "", 0.0, "", False, ""

    def cb(self, p: float, m: str = "") -> None:
        self.progress, self.msg = max(0.0, min(1.0, p)), m


JOB = Job()


async def run_job(name: str, fn, *args, done=None, **kw):
    if JOB.running:
        ui.notify(f"Busy: {JOB.name}", type="warning")
        return None
    JOB.name, JOB.progress, JOB.msg, JOB.running, JOB.error = name, 0.0, "starting", True, ""
    t0 = time.time()
    try:
        res = await run.io_bound(fn, *args, **kw)
        JOB.cb(1.0, f"done in {time.time() - t0:.0f} s")
        ui.notify(f"{name}: done", type="positive")
        if done:
            done(res)
        return res
    except Exception as e:  # noqa: BLE001
        JOB.error = f"{type(e).__name__}: {e}"
        JOB.msg = JOB.error
        traceback.print_exc()
        ui.notify(f"{name} failed: {e}", type="negative", multi_line=True, timeout=10000)
        return None
    finally:
        JOB.running = False


def esc(v) -> str:
    """Escape markdown in table cells (file names contain underscores)."""
    return re.sub(r"([_*|`])", lambda mo: "\\" + mo.group(1), str(v))


def fmt_table(d: dict, keys: list[tuple[str, str, str]]) -> str:
    rows = [f"| {lab} | {esc(d[k])}{unit} |" for k, lab, unit in keys if k in d]
    return "| | |\n|---|---|\n" + "\n".join(rows)


@ui.page("/")
def index():
    S: dict = {"prj": None, "layers": {}, "visible": {k: v[2] for k, v in STYLES.items()}}
    ui.page_title("MissionPlanner")
    ui.query(".nicegui-content").classes("p-0 gap-0")
    ui.add_css(".q-tab-panel{padding:8px 4px} .q-uploader{max-height:76px} .q-uploader__list{display:none}"
               ".mdtab table{font-size:12px} .mdtab td,.mdtab th{padding:1px 6px}")

    # ------------------------------------------------------------------ header
    with ui.header().classes("items-center bg-blue-grey-9 q-py-none").style("height:56px"):
        ui.label("MissionPlanner").classes("text-h6")
        ui.label(f"v{__version__} · DJI Neo survey toolkit").classes("text-caption opacity-70")
        ui.space()
        proj_sel = ui.select(Project.list(ROOT), label="Project", on_change=lambda e: open_project(e.value)) \
            .props("dense dark outlined options-dark").classes("w-56")
        new_name = ui.input(placeholder="new project name").props("dense dark outlined").classes("w-44")
        ui.button(icon="add", on_click=lambda: create_project()).props("flat round dense color=white") \
            .tooltip("Create project")
        ui.button(icon="folder_open", on_click=lambda: S["prj"] and os.startfile(S["prj"].root)) \
            .props("flat round dense color=white").tooltip("Open project folder")

    # ------------------------------------------------------------------ layout
    with ui.splitter(value=30, limits=(22, 55)).classes("w-full").style("height:calc(100vh - 56px)") as sp:
        with sp.before:
            with ui.column().classes("w-full q-pa-xs no-wrap").style("height:100%"):
                with ui.tabs().classes("w-full").props("dense") as tabs:
                    t_plan = ui.tab("Plan", icon="route")
                    t_fl = ui.tab("Flights", icon="flight")
                    t_st = ui.tab("Stitch", icon="layers")
                    t_qa = ui.tab("QA", icon="fact_check")
                panels = ui.tab_panels(tabs, value=t_plan).classes("w-full col").style("overflow:auto")
                # job status bar
                with ui.card().classes("w-full q-pa-xs").props("flat bordered"):
                    job_lab = ui.label("idle").classes("text-caption")
                    job_bar = ui.linear_progress(value=0, show_value=False).props("size=8px")
        with sp.after:
            m = ui.leaflet(center=(49.8, 15.5), zoom=7,
                           draw_control={"draw": {"polygon": True, "rectangle": True, "polyline": False,
                                                  "circle": False, "marker": False, "circlemarker": False},
                                         "edit": {"edit": False, "remove": False}},
                           hide_drawn_items=True).classes("w-full").style("height:100%")

    def tick():
        job_bar.value = JOB.progress
        job_lab.text = f"{JOB.name}: {JOB.msg}" if JOB.name else "idle"
        job_lab.classes(replace="text-caption " + ("text-negative" if JOB.error else ""))
    ui.timer(0.5, tick)

    # ------------------------------------------------------------------ map
    base_osm = None

    def setup_base():
        nonlocal base_osm
        m.clear_layers()
        base_osm = m.tile_layer(url_template="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                                options={"maxZoom": 22, "maxNativeZoom": 19, "attribution": "© OpenStreetMap"})
        m.tile_layer(url_template="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     options={"maxZoom": 22, "maxNativeZoom": 19, "opacity": 0.85, "attribution": "Esri World Imagery"})

    def layer_geojson(name: str) -> list[tuple[dict, dict]]:
        prj: Project = S["prj"]
        g = prj.read_layer(name)
        if g is None or g.empty:
            return []
        if name == "frames":  # points -> small circles (generic geoJSON has no pointToLayer from Python)
            g = g.copy(); g["geometry"] = g.geometry.buffer(1.0, 8)
        if name == "coverage":
            out = []
            for lo, hi, col, lab in COV_CLASSES:
                sub = g[(g["views"] >= lo) & (g["views"] <= hi)]
                if len(sub):
                    eps = 0.02 * (sub.geometry.iloc[0].length / 4)  # close float gaps between grid cells
                    geom = unary_union(list(sub.geometry.buffer(eps, join_style="mitre"))).simplify(eps)
                    gj = gpd.GeoSeries([geom], crs=g.crs).to_crs(4326).__geo_interface__
                    out.append((gj, {"color": col, "weight": 0, "fillColor": col, "fillOpacity": 0.45}))
            return out
        return [(g[["geometry"]].to_crs(4326).__geo_interface__, STYLES[name][1])]

    def refresh_map(fit: bool = False):
        prj: Project = S["prj"]
        setup_base()
        if prj is None:
            return
        have = set(prj.layers())
        bounds = None
        for name in STYLES:
            if name not in have or not S["visible"][name]:
                continue
            for gj, style in layer_geojson(name):
                m.generic_layer(name="geoJSON", args=[gj, {"style": style}])
        for name in ("survey_area", "plan_lines", "track_lines", "frames"):
            if name in have:
                g = prj.read_layer(name)
                if g is not None and not g.empty:
                    b = g.to_crs(4326).total_bounds
                    bounds = b if bounds is None else [min(bounds[0], b[0]), min(bounds[1], b[1]),
                                                       max(bounds[2], b[2]), max(bounds[3], b[3])]
        if fit and bounds is not None:
            async def _fit(b=bounds):
                await m.initialized()
                m.run_map_method("fitBounds", [[b[1], b[0]], [b[3], b[2]]], {"maxZoom": 19})
            ui.timer(0.05, _fit, once=True)
        refresh_layer_toggles(have)

    def on_draw(e):
        if S["prj"] is None:
            ui.notify("Create or open a project first", type="warning")
            return
        lay = e.args.get("layer", {})
        ll = lay.get("_latlngs")
        if not ll:
            return
        ring = ll[0] if isinstance(ll[0], list) else ll
        poly = Polygon([(p["lng"], p["lat"]) for p in ring])
        if not poly.is_valid or poly.area == 0:
            ui.notify("Invalid polygon", type="negative")
            return
        pl.set_survey_area(S["prj"], poly)
        for lyr in ("plan_lines", "plan_points", "plan_footprints"):
            S["prj"].delete_layer(lyr)
        ui.notify(f"Survey area set ({S['prj'].read_layer('survey_area')['area_ha'].iloc[0]:.2f} ha) - press Plan")
        refresh_map()
        refresh_plan_info()
    m.on("draw:created", on_draw)

    # ------------------------------------------------------------------ projects
    def create_project():
        n = (new_name.value or "").strip()
        if not n:
            ui.notify("Enter a project name", type="warning")
            return
        prj = Project.create(n, ROOT)
        proj_sel.options = Project.list(ROOT)
        proj_sel.update()
        proj_sel.value = prj.root.name
        new_name.value = ""

    def open_project(name):
        if not name:
            return
        S["prj"] = Project(ROOT / name)
        load_params()
        refresh_all(fit=True)

    def refresh_all(fit=False):
        refresh_map(fit)
        refresh_plan_info()
        refresh_flights()
        refresh_runs()
        refresh_qa()

    # ------------------------------------------------------------------ PLAN tab
    with panels:
        with ui.tab_panel(t_plan):
            ui.label("1. Draw the survey area with the polygon/rectangle tool (top-left of the map) "
                     "or load a KML/GeoJSON/GPKG.").classes("text-caption")
            ui.upload(label="Load area file", auto_upload=True, on_upload=lambda e: load_area(e)) \
                .props("dense flat bordered accept=.kml,.geojson,.json,.gpkg").classes("w-full")
            ui.label("2. Parameters").classes("text-subtitle2 q-mt-sm")
            cam_sel = ui.select(list(CAMERAS), label="Camera").props("dense").classes("w-full")
            mode = ui.toggle({"gsd": "Target GSD", "height": "Flight height"}, value="gsd").props("dense")
            with ui.row().classes("w-full no-wrap"):
                gsd = ui.number("GSD cm/px", min=0.5, max=10, step=0.1, format="%.1f").props("dense").classes("col")
                hgt = ui.number("Height m", min=5, max=120, step=1).props("dense").classes("col")
            gsd.bind_visibility_from(mode, "value", value="gsd")
            hgt.bind_visibility_from(mode, "value", value="height")
            with ui.row().classes("w-full no-wrap"):
                fo = ui.number("Front overlap %", min=50, max=95, step=5).props("dense").classes("col")
                so = ui.number("Side overlap %", min=30, max=90, step=5).props("dense").classes("col")
            with ui.row().classes("w-full no-wrap"):
                brg = ui.number("Bearing ° (empty = auto)", min=0, max=360).props("dense clearable").classes("col")
                spd = ui.number("Max speed m/s", min=1, max=12, step=0.5).props("dense").classes("col")
            with ui.row().classes("w-full no-wrap items-center"):
                bat = ui.number("Battery min", min=3, max=18, step=1).props("dense").classes("col")
                cross = ui.switch("Cross grid")
            with ui.expansion("More", icon="tune").classes("w-full").props("dense"):
                with ui.row().classes("w-full no-wrap"):
                    marg = ui.number("Margin m", min=0, max=50).props("dense").classes("col")
                    turn = ui.number("Turn s", min=0, max=60).props("dense").classes("col")
                    blur = ui.number("Blur px", min=0.1, max=2, step=0.1).props("dense").classes("col")
            with ui.row().classes("w-full"):
                ui.button("Plan", icon="route", on_click=lambda: do_plan()).props("color=primary")
                ui.button("KML", icon="download", on_click=lambda: do_export("kml")).props("outline dense")
                ui.button("PDF", icon="picture_as_pdf", on_click=lambda: do_export("pdf")).props("outline dense")
                ui.button("GPKG", icon="download", on_click=lambda: do_export("gpkg")).props("outline dense")
            plan_md = ui.markdown().classes("w-full mdtab")
            plan_warn = ui.column().classes("w-full")

        # ------------------------------------------------------------------ FLIGHTS tab
        with ui.tab_panel(t_fl):
            ui.label("Import the MP4 (+SRT) clips of one flight: a folder or file paths, one per line.") \
                .classes("text-caption")
            imp_path = ui.textarea(placeholder=r"D:\DCIM\DJI_001  or  C:\...\DJI_..._D.MP4").props("dense outlined autogrow") \
                .classes("w-full")
            with ui.row().classes("w-full items-center no-wrap"):
                imp_name = ui.input("Flight name (optional)").props("dense").classes("col")
                imp_copy = ui.checkbox("Copy into project", value=True)
            ui.button("Import", icon="upload", on_click=lambda: do_import()).props("dense")
            ui.separator().classes("q-my-sm")
            fl_sel = ui.select([], label="Flight").props("dense").classes("w-full")
            fl_md = ui.markdown().classes("w-full mdtab")
            with ui.expansion("QA / selection settings", icon="tune").classes("w-full").props("dense"):
                with ui.row().classes("w-full no-wrap"):
                    nadir = ui.number("Nadir tol °", min=1, max=60).props("dense").classes("col")
                    tilt = ui.number("Max tilt °", min=5, max=45).props("dense").classes("col")
                    minh = ui.number("Min height m", min=0, max=100).props("dense").classes("col")
                with ui.row().classes("w-full no-wrap"):
                    ffo = ui.number("Frame overlap %", min=50, max=95, step=5).props("dense").classes("col")
                    msh = ui.number("Min rel. sharpness", min=0, max=1.5, step=0.05).props("dense").classes("col")
                    goff = ui.number("Take-off above ground m", min=-200, max=200).props("dense").classes("col")
            with ui.grid(columns=2).classes("w-full"):
                ui.button("1 Track QA", on_click=lambda: flight_step("track")).props("dense outline")
                ui.button("2 Sharpness", on_click=lambda: flight_step("score")).props("dense outline")
                ui.button("3 Select frames", on_click=lambda: flight_step("select")).props("dense outline")
                ui.button("4 Extract + geotag", on_click=lambda: flight_step("extract")).props("dense outline")
            ui.button("Run 1-4", icon="play_arrow", on_click=lambda: flight_step("all")).props("color=primary dense")
            ui.button("Coverage of all flights", icon="grid_on", on_click=lambda: do_coverage()) \
                .props("dense outline").classes("q-mt-xs")
            fl_table = ui.markdown().classes("w-full mdtab q-mt-sm")

        # ------------------------------------------------------------------ STITCH tab
        with ui.tab_panel(t_st):
            with ui.row().classes("w-full items-center no-wrap"):
                odm_url = ui.input("NodeODM URL", value=DEFAULT_URL).props("dense").classes("col")
                ui.button(icon="wifi_find", on_click=lambda: check_odm()).props("flat round dense").tooltip("Check")
            odm_status = ui.label("").classes("text-caption")
            ui.label("Options").classes("text-subtitle2")
            with ui.row().classes("w-full no-wrap"):
                o_res = ui.number("Ortho cm/px", value=DEFAULT_OPTIONS["orthophoto-resolution"], min=0.5, max=20,
                                  step=0.5).props("dense").classes("col")
                o_fq = ui.select(["ultra", "high", "medium", "low"], value=DEFAULT_OPTIONS["feature-quality"],
                                 label="Features").props("dense").classes("col")
                o_pc = ui.select(["ultra", "high", "medium", "low", "lowest"], value=DEFAULT_OPTIONS["pc-quality"],
                                 label="Point cloud").props("dense").classes("col")
            with ui.row():
                o_dsm = ui.switch("DSM", value=True)
                o_fast = ui.switch("Fast ortho (no 3D)", value=False)
            run_name = ui.input("Run name (optional)").props("dense").classes("w-full")
            ui.button("Start stitch", icon="play_arrow", on_click=lambda: do_stitch()).props("color=primary")
            runs_md = ui.markdown().classes("w-full mdtab q-mt-sm")
            runs_box = ui.column().classes("w-full")

        # ------------------------------------------------------------------ QA tab
        with ui.tab_panel(t_qa):
            ui.label("Map layers").classes("text-subtitle2")
            toggles_box = ui.column().classes("w-full gap-0")
            ui.label("Coverage").classes("text-subtitle2 q-mt-sm")
            with ui.row().classes("gap-1"):
                for _, _, col, lab in COV_CLASSES:
                    ui.label(f"{lab} views").style(f"background:{col};color:white;padding:0 6px;border-radius:3px;font-size:11px")
            qa_md = ui.markdown().classes("w-full mdtab")

    # ------------------------------------------------------------------ params <-> settings
    def load_params():
        prj: Project = S["prj"]
        p, t, f = prj.settings["plan"], prj.settings["track"], prj.settings["frames"]
        cam_sel.value = prj.settings["camera"]["name"] if prj.settings["camera"]["name"] in CAMERAS else list(CAMERAS)[0]
        mode.value, gsd.value, hgt.value = p["mode"], p["gsd_cm"], p["height_m"]
        fo.value, so.value = round(p["front_overlap"] * 100), round(p["side_overlap"] * 100)
        brg.value, spd.value, bat.value, cross.value = p["bearing_deg"], p["speed_max_ms"], p["battery_usable_min"], p["cross_grid"]
        marg.value, turn.value, blur.value = p["margin_m"], p["turn_s"], p["blur_px"]
        nadir.value, tilt.value, minh.value = t["nadir_tol_deg"], t["tilt_max_deg"], t["min_height_m"]
        ffo.value, msh.value, goff.value = round(f["front_overlap"] * 100), f["min_rel_sharpness"], f["ground_offset_m"]
        odm_url.value = prj.settings.get("odm_url", DEFAULT_URL)

    def save_params():
        prj: Project = S["prj"]
        if cam_sel.value and cam_sel.value != prj.settings["camera"]["name"]:
            prj.set_camera(cam_sel.value)
        p = prj.settings["plan"]
        p.update(mode=mode.value, gsd_cm=float(gsd.value), height_m=float(hgt.value),
                 front_overlap=float(fo.value) / 100, side_overlap=float(so.value) / 100,
                 bearing_deg=None if brg.value in (None, "") else float(brg.value),
                 speed_max_ms=float(spd.value), battery_usable_min=float(bat.value), cross_grid=bool(cross.value),
                 margin_m=float(marg.value), turn_s=float(turn.value), blur_px=float(blur.value))
        prj.settings["track"].update(nadir_tol_deg=float(nadir.value), tilt_max_deg=float(tilt.value),
                                     min_height_m=float(minh.value))
        prj.settings["frames"].update(front_overlap=float(ffo.value) / 100, min_rel_sharpness=float(msh.value),
                                      ground_offset_m=float(goff.value))
        prj.settings["odm_url"] = odm_url.value
        prj.save()

    def need_project() -> bool:
        if S["prj"] is None:
            ui.notify("Create or open a project first", type="warning")
            return False
        return True

    # ------------------------------------------------------------------ plan actions
    async def load_area(e):
        if not need_project():
            return
        tmp = S["prj"].root / "exports" / e.file.name
        tmp.parent.mkdir(exist_ok=True)
        await e.file.save(tmp)
        try:
            g = gpd.read_file(tmp).to_crs(4326)
            poly = unary_union([geom for geom in g.geometry if geom.geom_type in ("Polygon", "MultiPolygon")])
            if poly.is_empty:
                raise ValueError("no polygon in file")
            if poly.geom_type == "MultiPolygon":
                poly = max(poly.geoms, key=lambda p: p.area)
                ui.notify("Several polygons - using the largest", type="warning")
            pl.set_survey_area(S["prj"], Polygon(poly.exterior.coords))
            refresh_map(fit=True)
            refresh_plan_info()
        except Exception as ex:  # noqa: BLE001
            ui.notify(f"Could not read area: {ex}", type="negative")

    async def do_plan():
        if not need_project():
            return
        save_params()
        await run_job("Plan", pl.run_plan, S["prj"], done=lambda r: (refresh_map(), refresh_plan_info()))

    def refresh_plan_info():
        prj: Project = S["prj"]
        st = prj.settings.get("stats", {}).get("plan") if prj else None
        plan_warn.clear()
        if not st or "plan_lines" not in prj.layers():
            plan_md.content = "*No plan yet.*"
            return
        plan_md.content = fmt_table(st, [
            ("height_m", "Height above take-off", " m"), ("gsd_cm", "GSD", " cm/px"),
            ("footprint_across_m", "Footprint across", " m"), ("footprint_along_m", "Footprint along", " m"),
            ("line_spacing_m", "Line spacing", " m"), ("frame_spacing_m", "Frame spacing", " m"),
            ("bearing_deg", "Bearing", "°"), ("lines", "Lines", ""), ("line_length_m", "Line length", " m"),
            ("speed_ms", "Speed", " m/s"), ("frames_expected", "Frames", ""), ("area_ha", "Area", " ha"),
            ("flight_time_min", "Flight time", " min"), ("batteries", "Batteries", ""),
            ("max_distance_from_centre_m", "Max distance from centre", " m")])
        pc = st.get("plan_coverage")
        if pc:
            plan_md.content += f"\n\nPlanned coverage: **{pc['pct_ge5']} %** of the area with ≥ 5 views (median {pc['views_median']:.0f})."
        with plan_warn:
            for w in prj.settings["stats"].get("plan_warnings", []):
                ui.label("⚠ " + w).classes("text-warning text-caption")

    def do_export(kind):
        if not need_project():
            return
        try:
            if kind == "kml":
                ui.download.file(exports.export_kml(S["prj"]))
            elif kind == "pdf":
                ui.download.file(exports.export_pdf(S["prj"]))
            else:
                ui.download.file(S["prj"].gpkg)
        except Exception as ex:  # noqa: BLE001
            ui.notify(str(ex), type="negative")

    # ------------------------------------------------------------------ flight actions
    async def do_import():
        if not need_project():
            return
        files = []
        for ln in (imp_path.value or "").splitlines():
            p = Path(ln.strip().strip('"'))
            if not ln.strip():
                continue
            if p.is_dir():
                files += sorted(x for x in p.iterdir() if x.suffix.lower() == ".mp4")
            elif p.is_file():
                files.append(p)
            else:
                ui.notify(f"Not found: {p}", type="negative")
                return
        if not files:
            ui.notify("No MP4 files found", type="warning")
            return
        name = await run_job(f"Import {len(files)} clips", pl.import_flight, S["prj"], files,
                             (imp_name.value or "").strip() or None, bool(imp_copy.value))
        if name:
            refresh_flights()
            fl_sel.value = name

    def _flight_all(prj, fl, progress):
        steps = [("track", 0.02), ("score", 0.45), ("select", 0.02), ("extract", 0.51)]
        p0 = 0.0
        for s, w in steps:
            sub = (lambda a, b: (lambda p, m="": progress(a + b * p, f"{s}: {m}")))(p0, w)
            _flight_one(prj, fl, s, sub)
            p0 += w

    def _flight_one(prj, fl, step, progress):
        if step == "track":
            pl.process_track(prj, fl, progress)
        elif step == "score":
            pl.score_frames(prj, fl, progress)
        elif step == "select":
            pl.select_frames(prj, fl)
        elif step == "extract":
            pl.extract_frames(prj, fl, progress)

    async def flight_step(step):
        if not need_project():
            return
        fl = fl_sel.value
        if not fl:
            ui.notify("Select a flight", type="warning")
            return
        save_params()
        fn = (lambda: _flight_all(S["prj"], fl, JOB.cb)) if step == "all" else \
            (lambda: _flight_one(S["prj"], fl, step, JOB.cb))
        await run_job(f"{fl} {step}", fn, done=lambda r: (refresh_flights(), refresh_map(fit=step in ("track", "all"))))

    async def do_coverage():
        if not need_project():
            return
        save_params()
        await run_job("Coverage", pl.run_coverage, S["prj"], done=lambda r: (refresh_map(), refresh_qa()))

    def refresh_flights():
        prj: Project = S["prj"]
        fls = prj.settings.get("flights", {}) if prj else {}
        fl_sel.options = list(fls)
        if fl_sel.value not in fls:
            fl_sel.value = next(iter(fls), None)
        fl_sel.update()
        if not fls:
            fl_table.content = "*No flights imported.*"
            fl_md.content = ""
            return
        rows = ["| Flight | Clips | Status | Selected | Extracted |", "|---|---|---|---|---|"]
        for n, f in fls.items():
            rows.append(f"| {esc(n)} | {len(f['clips'])} | {f.get('status', '')} | {f.get('n_selected', '')} | {f.get('n_extracted', '')} |")
        fl_table.content = "\n".join(rows)
        qa = fls.get(fl_sel.value, {}).get("qa")
        if qa:
            qrows = ["| Clip | s | path m | h m | nadir % | usable % |", "|---|---|---|---|---|---|"]
            for q in qa:
                qrows.append(f"| {esc(q['clip'][-9:])} | {q['duration_s']:.0f} | {q['path_m']:.0f} | "
                             f"{q['height_min']:.0f}–{q['height_max']:.0f} | {q['nadir_pct']} | {q['usable_pct']} |")
            fl_md.content = "\n".join(qrows)
        else:
            fl_md.content = ""
    fl_sel.on_value_change(lambda e: refresh_flights())

    # ------------------------------------------------------------------ stitch actions
    async def check_odm():
        node = NodeODM(odm_url.value)
        try:
            inf = await run.io_bound(node.info)
            odm_status.text = (f"✔ NodeODM {inf['version']} · {inf['cpuCores']} cores · "
                               f"{inf['availableMemory'] / 2**30:.1f} GB free · queue {inf['taskQueueCount']}")
            odm_status.classes(replace="text-caption text-positive")
        except Exception as ex:  # noqa: BLE001
            odm_status.text = f"✖ not reachable ({type(ex).__name__}) - start Docker Desktop / container 'nodeodm'"
            odm_status.classes(replace="text-caption text-negative")

    async def do_stitch():
        if not need_project():
            return
        save_params()
        opts = {"orthophoto-resolution": float(o_res.value), "feature-quality": o_fq.value,
                "pc-quality": o_pc.value, "dsm": bool(o_dsm.value), "fast-orthophoto": bool(o_fast.value)}
        await run_job("Stitch", pl.run_odm, S["prj"], None, (run_name.value or "").strip() or None, opts,
                      odm_url.value, JOB.cb, done=lambda r: refresh_runs())
        refresh_runs()

    def refresh_runs():
        prj: Project = S["prj"]
        runs = prj.settings.get("runs", {}) if prj else {}
        runs_box.clear()
        if not runs:
            runs_md.content = "*No stitching runs yet.*"
            return
        rows = ["| Run | Images | Status | Time |", "|---|---|---|---|"]
        for n, r in runs.items():
            t = f"{r['processing_s'] / 60:.0f} min" if r.get("processing_s") else ""
            rows.append(f"| {esc(n)} | {r.get('n_images', '')} | {r.get('status', '')} | {t} |")
        runs_md.content = "\n".join(rows)
        with runs_box:
            for n, r in runs.items():
                d = prj.odm_dir / n
                if d.exists():
                    with ui.row().classes("items-center gap-1"):
                        ui.label(n).classes("text-caption")
                        ui.button(icon="folder_open", on_click=lambda d=d: os.startfile(d)).props("flat round dense size=sm")
                        ortho = d / "odm_orthophoto" / "odm_orthophoto.tif"
                        if ortho.exists():
                            ui.label(f"ortho {ortho.stat().st_size / 2**20:.0f} MB").classes("text-caption text-positive")
                if r.get("error"):
                    ui.label(r["error"][:300]).classes("text-caption text-negative")

    # ------------------------------------------------------------------ QA
    def refresh_layer_toggles(have):
        toggles_box.clear()
        with toggles_box:
            for name, (lab, _, _) in STYLES.items():
                if name in have:
                    ui.checkbox(lab, value=S["visible"][name],
                                on_change=lambda e, n=name: (S["visible"].__setitem__(n, e.value), refresh_map())) \
                        .props("dense")

    def refresh_qa():
        prj: Project = S["prj"]
        st = prj.settings.get("stats", {}).get("coverage") if prj else None
        if not st:
            qa_md.content = "*Run 'Coverage of all flights' after selecting frames.*"
            return
        qa_md.content = fmt_table(st, [
            ("frames", "Frames", ""), ("extent", "Extent", ""), ("area_ha", "Area", " ha"), ("cell_m", "Grid cell", " m"),
            ("views_median", "Median views", ""), ("pct_ge5", "≥ 5 views", " %"), ("pct_3_4", "3–4 views", " %"),
            ("pct_1_2", "1–2 views", " %"), ("pct_0", "Not seen", " %")])
        qa_md.content += "\n\nRule of thumb: ODM needs ≥ 5 views for a good orthophoto/DSM; fly extra lines where 0–2."

    setup_base()
    projects = Project.list(ROOT)
    if projects:
        proj_sel.value = projects[0]


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="MissionPlanner", host=os.environ.get("MISSIONPLANNER_HOST", "127.0.0.1"),
           port=int(os.environ.get("MISSIONPLANNER_PORT", 8080)), reload=False,
           show=False, favicon="🛩")
