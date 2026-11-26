"""Ground footprints of frames and overlap (view count) grid."""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import Polygon, box

from .camera import Camera


def footprint(x: float, y: float, h: float, yaw_deg: float, pitch_deg: float, cam: Camera,
              max_range_factor: float = 6.0, edge_pts: int = 4) -> Polygon | None:
    """Footprint on flat ground ``h`` metres below the camera.

    yaw_deg: heading of the camera's view (deg clockwise from grid north); pitch_deg: gimbal pitch
    (0 = horizon, -90 = nadir). Rays above the horizon are clipped at ``max_range_factor * h``."""
    if h is None or not np.isfinite(h) or h <= 0.5:
        return None
    psi = math.radians(yaw_deg if np.isfinite(yaw_deg) else 0.0)
    th = math.radians(pitch_deg if np.isfinite(pitch_deg) else -90.0)
    f = np.array([math.sin(psi) * math.cos(th), math.cos(psi) * math.cos(th), math.sin(th)])
    r = np.array([math.cos(psi), -math.sin(psi), 0.0])
    u = np.cross(r, f)
    tx = math.tan(math.radians(cam.hfov_deg) / 2)
    ty = math.tan(math.radians(cam.vfov_deg) / 2)
    s = np.linspace(-1, 1, edge_pts + 1)
    border = ([(a, 1.0) for a in s] + [(1.0, b) for b in s[::-1][1:]] +
              [(a, -1.0) for a in s[::-1][1:]] + [(-1.0, b) for b in s[1:-1]])
    pts = []
    rmax = max_range_factor * h
    for a, b in border:
        d = f + a * tx * r + b * ty * u
        if d[2] < -1e-6:
            t = h / -d[2]
            px, py = d[0] * t, d[1] * t
            rr = math.hypot(px, py)
            if rr > rmax:
                px, py = px * rmax / rr, py * rmax / rr
        else:
            hz = math.hypot(d[0], d[1]) or 1.0
            px, py = d[0] / hz * rmax, d[1] / hz * rmax
        pts.append((x + px, y + py))
    p = Polygon(pts)
    if not p.is_valid:
        p = p.buffer(0)
    return p if not p.is_empty else None


def view_count_grid(footprints: list[Polygon], extent: Polygon, cell: float = 2.0):
    """Count footprints covering each grid cell centre over ``extent``.

    Returns (xs, ys, counts[ny, nx], cell polygons list, counts flat for cells inside extent)."""
    minx, miny, maxx, maxy = extent.bounds
    xs = np.arange(minx + cell / 2, maxx, cell)
    ys = np.arange(miny + cell / 2, maxy, cell)
    X, Y = np.meshgrid(xs, ys)
    cnt = np.zeros(X.shape, dtype=np.int32)
    for fp in footprints:
        if fp is None:
            continue
        a, b, c, d = fp.bounds
        ix = (xs >= a) & (xs <= c)
        iy = (ys >= b) & (ys <= d)
        if not ix.any() or not iy.any():
            continue
        sub = np.ix_(iy, ix)
        cnt[sub] += shapely.contains_xy(fp, X[sub], Y[sub])
    inside = shapely.contains_xy(extent, X, Y)
    return xs, ys, cnt, inside


def coverage_stats(cnt: np.ndarray, inside: np.ndarray, cell: float) -> dict:
    v = cnt[inside]
    n = max(1, v.size)
    return {"cells": int(v.size), "cell_m": cell, "area_ha": round(v.size * cell * cell / 1e4, 3),
            "views_median": float(np.median(v)) if v.size else 0.0,
            "pct_ge5": round(100 * float((v >= 5).sum()) / n, 1),
            "pct_3_4": round(100 * float(((v >= 3) & (v <= 4)).sum()) / n, 1),
            "pct_1_2": round(100 * float(((v >= 1) & (v <= 2)).sum()) / n, 1),
            "pct_0": round(100 * float((v == 0).sum()) / n, 1)}


def grid_polygons(xs, ys, cnt, inside, cell: float, min_count: int = 0):
    """Cells as polygons with their count (inside extent, count >= min_count)."""
    geoms, vals = [], []
    for j, yv in enumerate(ys):
        for i, xv in enumerate(xs):
            if inside[j, i] and cnt[j, i] >= min_count:
                geoms.append(box(xv - cell / 2, yv - cell / 2, xv + cell / 2, yv + cell / 2))
                vals.append(int(cnt[j, i]))
    return geoms, vals
