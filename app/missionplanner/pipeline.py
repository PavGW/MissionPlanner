"""High-level processing steps on a Project (used by the web UI and the CLI)."""
from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Callable

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, Polygon, shape, mapping
from shapely.ops import unary_union

from . import coverage as cov
from . import frames as fr
from .geo import geom_to, utm_epsg
from .geotag import geotag
from .planner import plan
from .project import Project
from .track import derive, load_clip, summary

Progress = Callable[[float, str], None]


def _noop(p: float, msg: str = "") -> None:  # pragma: no cover
    pass


# ------------------------------------------------------------------------------------------------
# survey area + plan
# ------------------------------------------------------------------------------------------------
def set_survey_area(prj: Project, poly_wgs84: Polygon) -> None:
    if prj.epsg is None:
        c = poly_wgs84.centroid
        prj.settings["epsg"] = utm_epsg(c.x, c.y)
        prj.save()
    poly = geom_to(poly_wgs84, 4326, prj.epsg)
    prj.write_layer("survey_area", gpd.GeoDataFrame({"area_ha": [round(poly.area / 1e4, 3)]},
                                                     geometry=[poly], crs=prj.epsg))


def survey_area(prj: Project) -> Polygon | None:
    g = prj.read_layer("survey_area")
    if g is None or g.empty:
        return None
    return unary_union(list(g.geometry))


def run_plan(prj: Project) -> dict:
    poly = survey_area(prj)
    if poly is None:
        raise ValueError("Draw or import a survey area first")
    res = plan(poly, prj.camera, prj.plan_params)
    e = prj.epsg
    prj.write_layer("plan_lines", gpd.GeoDataFrame(res.line_info, geometry=res.lines, crs=e))
    pts, info = [], []
    for ln, d in zip(res.lines, res.line_info):
        for end, c in (("start", ln.coords[0]), ("end", ln.coords[-1])):
            pts.append(Point(c))
            info.append({"line_no": d["line_no"], "end": end, "label": f"{d['line_no']}{'S' if end == 'start' else 'E'}"})
    prj.write_layer("plan_points", gpd.GeoDataFrame(info, geometry=pts, crs=e))
    prj.write_layer("plan_footprints", gpd.GeoDataFrame(res.footprint_info, geometry=res.footprints, crs=e))
    # planned coverage check
    xs, ys, cnt, inside = cov.view_count_grid(res.footprints, poly, cell=max(1.0, res.stats["gsd_cm"] / 100 * 100))
    res.stats["plan_coverage"] = cov.coverage_stats(cnt, inside, max(1.0, res.stats["gsd_cm"]))
    prj.settings["stats"]["plan"] = res.stats
    prj.settings["stats"]["plan_warnings"] = res.warnings
    prj.save()
    return {"stats": res.stats, "warnings": res.warnings}


# ------------------------------------------------------------------------------------------------
# flights
# ------------------------------------------------------------------------------------------------
def import_flight(prj: Project, files: list[str | Path], name: str | None = None, copy: bool = True) -> str:
    """Copy (or reference) MP4 + SRT files of one flight into the project. Returns the flight name."""
    mp4s = sorted(Path(f) for f in files if str(f).lower().endswith(".mp4"))
    if not mp4s:
        raise ValueError("No MP4 files given")
    name = name or mp4s[0].stem.split("_")[1] if "_" in mp4s[0].stem else mp4s[0].stem
    d = prj.flight_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    clips = []
    for m in mp4s:
        srt = m.with_suffix(".SRT") if m.with_suffix(".SRT").exists() else m.with_suffix(".srt")
        if copy:
            for src in (m, srt):
                if src.exists() and not (d / src.name).exists():
                    shutil.copy2(src, d / src.name)
            clips.append(str((d / m.name).relative_to(prj.root)))
        else:
            clips.append(str(m.resolve()))
    prj.settings.setdefault("flights", {})[name] = {"clips": clips, "status": "imported"}
    prj.save()
    return name


def clip_paths(prj: Project, flight: str) -> list[Path]:
    out = []
    for c in prj.settings["flights"][flight]["clips"]:
        p = Path(c)
        out.append(p if p.is_absolute() else prj.root / p)
    return out


def telemetry_path(prj: Project, flight: str) -> Path:
    return prj.flight_dir(flight) / "telemetry.csv"


def process_track(prj: Project, flight: str, progress: Progress = _noop) -> list[dict]:
    clips = clip_paths(prj, flight)
    dfs = []
    for i, c in enumerate(clips):
        progress(i / len(clips), f"telemetry {c.name}")
        dfs.append(load_clip(c, prj.flight_dir(flight)))
    df, epsg = derive(pd.concat(dfs, ignore_index=True), prj.epsg, prj.track_params)
    if prj.epsg is None:
        prj.settings["epsg"] = epsg
    df.to_csv(telemetry_path(prj, flight), index=False)
    for c in clips:  # raw djmd dumps are not needed any more
        b = prj.flight_dir(flight) / (c.stem + "_djmd.bin")
        if b.exists():
            b.unlink()
    _write_track_layers(prj)
    s = summary(df)
    prj.settings["flights"][flight].update(status="track", qa=s)
    prj.save()
    progress(1.0, "track done")
    return s


def load_telemetry(prj: Project, flight: str) -> pd.DataFrame:
    return pd.read_csv(telemetry_path(prj, flight))


def _write_track_layers(prj: Project) -> None:
    pts, lines = [], []
    for f in prj.settings.get("flights", {}):
        tp = telemetry_path(prj, f)
        if not tp.exists():
            continue
        df = pd.read_csv(tp)
        df = df[df["x"].notna()]
        sub = df.iloc[::3].copy()      # every 3rd frame (~0.1 s) is plenty for display
        sub["flight"] = f
        cols = ["flight", "clip", "frame0", "t", "datetime", "rel_alt", "abs_alt", "speed", "dist",
                "drone_yaw", "gimbal_pitch", "yaw_rate", "flags", "flag_text", "iso", "shutter"]
        cols = [c for c in cols if c in sub]
        pts.append(gpd.GeoDataFrame(sub[cols], geometry=gpd.points_from_xy(sub["x"], sub["y"]), crs=prj.epsg))
        for clip, g in df.groupby("clip", sort=False):
            if len(g) > 1:
                lines.append({"flight": f, "clip": clip, "frames": len(g), "length_m": round(float(g["dist"].max()), 1),
                              "geometry": LineString(list(zip(g["x"].values[::3], g["y"].values[::3])) or [(0, 0), (0, 0)])})
    if pts:
        prj.write_layer("track_points", pd.concat(pts, ignore_index=True))
    if lines:
        prj.write_layer("track_lines", gpd.GeoDataFrame(lines, geometry="geometry", crs=prj.epsg))


# ------------------------------------------------------------------------------------------------
# frames
# ------------------------------------------------------------------------------------------------
def sharpness_path(prj: Project, flight: str) -> Path:
    return prj.flight_dir(flight) / "sharpness.csv"


def score_frames(prj: Project, flight: str, progress: Progress = _noop) -> int:
    df = load_telemetry(prj, flight)
    prm = prj.frame_params
    mask = fr.reject_mask(prm.reject)
    rows = []
    clips = clip_paths(prj, flight)
    for ci, c in enumerate(clips):
        g = df[df["clip"] == c.stem]
        segs = fr.candidate_segments(g, mask)
        base = ci / len(clips)
        sc = fr.sharpness_scores(c, segs, lambda p, b=base: progress(b + p / len(clips), f"sharpness {c.name}"))
        rows += [{"clip": c.stem, "frame0": k, "sharpness": v} for k, v in sc.items()]
    pd.DataFrame(rows, columns=["clip", "frame0", "sharpness"]).to_csv(sharpness_path(prj, flight), index=False)
    prj.settings["flights"][flight]["status"] = "scored"
    prj.save()
    return len(rows)


def select_frames(prj: Project, flight: str) -> pd.DataFrame:
    df = load_telemetry(prj, flight)
    sp = sharpness_path(prj, flight)
    sc = pd.read_csv(sp) if sp.exists() else pd.DataFrame(columns=["clip", "frame0", "sharpness"])
    scores = {c: dict(zip(g["frame0"], g["sharpness"])) for c, g in sc.groupby("clip")}
    sel = fr.select(df, prj.camera, prj.frame_params, scores)
    sel["flight"] = flight
    sel.to_csv(prj.flight_dir(flight) / "selected.csv", index=False)
    prj.settings["flights"][flight].update(status="selected", n_selected=int(len(sel)))
    prj.save()
    _write_frame_layers(prj)
    return sel


def extract_frames(prj: Project, flight: str, progress: Progress = _noop) -> int:
    sel = pd.read_csv(prj.flight_dir(flight) / "selected.csv")
    out = prj.frames_dir(flight)
    if out.exists():
        for p in out.glob("*.jpg"):
            p.unlink()
    n = 0
    clips = {c.stem: c for c in clip_paths(prj, flight)}
    groups = list(sel.groupby("clip", sort=False))
    cam = prj.camera
    for gi, (clip, g) in enumerate(groups):
        progress(gi / max(1, len(groups)), f"extract {clip}")
        paths = fr.extract(clips[clip], list(g["frame0"]), out, list(g["image"]), prj.frame_params.jpeg_quality)
        recs = g.set_index("image").to_dict("index")
        for p in paths:
            r = recs[p.name]
            r["clip"] = clip
            geotag(p, r, cam)
            n += 1
    prj.settings["flights"][flight].update(status="extracted", n_extracted=n)
    prj.save()
    progress(1.0, "frames extracted")
    return n


def _write_frame_layers(prj: Project) -> None:
    rows = []
    for f in prj.settings.get("flights", {}):
        p = prj.flight_dir(f) / "selected.csv"
        if p.exists():
            rows.append(pd.read_csv(p))
    if not rows:
        return
    sel = pd.concat(rows, ignore_index=True)
    cols = ["flight", "clip", "frame0", "image", "t", "datetime", "rel_alt", "abs_alt", "speed", "drone_yaw",
            "gimbal_pitch", "sharpness", "rel_sharpness", "spacing_m"]
    cols = [c for c in cols if c in sel]
    prj.write_layer("frames", gpd.GeoDataFrame(sel[cols], geometry=gpd.points_from_xy(sel["x"], sel["y"]), crs=prj.epsg))


# ------------------------------------------------------------------------------------------------
# coverage / QA
# ------------------------------------------------------------------------------------------------
def run_coverage(prj: Project, cell: float | None = None) -> dict:
    rows = []
    for f in prj.settings.get("flights", {}):
        p = prj.flight_dir(f) / "selected.csv"
        if p.exists():
            rows.append(pd.read_csv(p))
    if not rows:
        raise ValueError("No selected frames yet")
    sel = pd.concat(rows, ignore_index=True)
    cam = prj.camera
    off = prj.frame_params.ground_offset_m
    fps, keep = [], []
    for i, r in sel.iterrows():
        fp = cov.footprint(r["x"], r["y"], float(r["rel_alt"]) + off, float(r.get("drone_yaw", 0) or 0),
                           float(r.get("gimbal_pitch", -90) if pd.notna(r.get("gimbal_pitch")) else -90), cam)
        if fp is not None:
            fps.append(fp)
            keep.append(i)
    fgdf = gpd.GeoDataFrame(sel.loc[keep, ["flight", "clip", "frame0", "image", "rel_alt", "gimbal_pitch"]],
                            geometry=fps, crs=prj.epsg)
    prj.write_layer("footprints", fgdf)
    area = survey_area(prj)
    extent = area if area is not None else unary_union(fps).convex_hull
    if cell is None:
        gsd = cam.gsd_m(float(np.nanmedian(sel["rel_alt"])) + off)
        cell = max(1.0, round(gsd * 100, 1))      # ~100 px cells
    xs, ys, cnt, inside = cov.view_count_grid(fps, extent, cell)
    st = cov.coverage_stats(cnt, inside, cell)
    st["frames"] = int(len(fps))
    st["extent"] = "survey_area" if area is not None else "convex hull of footprints"
    geoms, vals = cov.grid_polygons(xs, ys, cnt, inside, cell)
    prj.write_layer("coverage", gpd.GeoDataFrame({"views": vals}, geometry=geoms, crs=prj.epsg))
    prj.settings["stats"]["coverage"] = st
    prj.save()
    return st
