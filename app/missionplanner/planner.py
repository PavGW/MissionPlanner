"""Survey flight planner: polygon + camera + GSD/height + overlaps -> parallel flight lines (snake order).

Conventions
-----------
* Geometry in a projected CRS (metres, UTM).
* ``bearing_deg`` = direction of the flight lines, degrees clockwise from grid north (0..180).
* Hand flying with the Neo: the drone flies nose-first along a line with the camera pointing straight down, so
  the image's long side lies *across* the track (``orientation="across"``, default). ``"along"`` = drone flown
  sideways / camera rotated (long side along track).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from .camera import Camera


@dataclass
class PlanParams:
    mode: str = "gsd"                  # "gsd" or "height"
    gsd_cm: float = 2.0
    height_m: float = 30.0
    front_overlap: float = 0.80
    side_overlap: float = 0.70
    bearing_deg: float | None = None   # None = auto (along the polygon's long axis)
    orientation: str = "across"        # image long side across (default) or along the track
    margin_m: float = 5.0              # extend coverage beyond the polygon (all sides)
    lead_in_m: float | None = None     # extra length at each line end; None = half along-track footprint
    speed_max_ms: float = 5.0          # pilot/drone comfortable cruise speed
    blur_px: float = 0.5               # max motion blur during exposure
    turn_s: float = 15.0               # time per line change (stop, turn, settle)
    battery_usable_min: float = 12.0   # usable flight time per battery (Neo ~18 min max)
    cross_grid: bool = False           # add a perpendicular set of lines (better for 3D / DSM)
    min_height_m: float = 10.0
    max_height_m: float = 120.0        # EU open category limit above ground

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PlanParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PlanResult:
    lines: list[LineString]                          # in flight order, each oriented in flight direction
    line_info: list[dict]
    footprints: list[Polygon]
    footprint_info: list[dict]
    stats: dict
    warnings: list[str] = field(default_factory=list)


def auto_bearing(poly: BaseGeometry) -> float:
    """Bearing (deg from north, 0..180) of the long side of the polygon's minimum rotated rectangle."""
    mrr = poly.minimum_rotated_rectangle
    c = list(mrr.exterior.coords)
    best = max(((c[i], c[i + 1]) for i in range(4)), key=lambda ab: math.dist(*ab))
    (x0, y0), (x1, y1) = best
    return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 180


def _segments(g) -> list[LineString]:
    if g.is_empty:
        return []
    if isinstance(g, LineString):
        return [g]
    if isinstance(g, MultiLineString):
        return list(g.geoms)
    return [p for p in getattr(g, "geoms", []) if isinstance(p, LineString)]


def _line_set(poly: Polygon, bearing: float, spacing: float, lead_in: float, origin) -> list[LineString]:
    """Parallel lines (bearing) covering ``poly`` at ``spacing``; returned unordered, in world coords."""
    # rotate so that the flight direction becomes +x (east): world bearing b -> math angle 90-b
    rot = bearing - 90.0  # rotating world by +rot (ccw) maps bearing b onto east
    p = affinity.rotate(poly, rot, origin=origin)
    minx, miny, maxx, maxy = p.bounds
    h = maxy - miny
    n = max(1, math.ceil(h / spacing) + 1)
    y0 = miny + (h - (n - 1) * spacing) / 2
    out = []
    for i in range(n):
        y = y0 + i * spacing
        cut = LineString([(minx - 1, y), (maxx + 1, y)]).intersection(p)
        segs = _segments(cut)
        if not segs:  # first/last line may only touch the edge: use the nearest chord
            yy = min(max(y, miny + 1e-6), maxy - 1e-6)
            segs = _segments(LineString([(minx - 1, yy), (maxx + 1, yy)]).intersection(p))
            segs = [LineString([(s.coords[0][0], y), (s.coords[-1][0], y)]) for s in segs]
        for s in segs:
            xa = min(s.coords[0][0], s.coords[-1][0]) - lead_in
            xb = max(s.coords[0][0], s.coords[-1][0]) + lead_in
            out.append(affinity.rotate(LineString([(xa, y), (xb, y)]), -rot, origin=origin))
    return out, rot


def _snake(lines: list[LineString], rot: float, origin) -> list[LineString]:
    """Order lines across the block and alternate direction (boustrophedon)."""
    def key(ln):
        q = affinity.rotate(ln, rot, origin=origin)
        return (round(q.coords[0][1], 3), q.coords[0][0])
    lines = sorted(lines, key=key)
    out = []
    for i, ln in enumerate(lines):
        out.append(ln if i % 2 == 0 else LineString(list(ln.coords)[::-1]))
    return out


def plan(poly: Polygon, cam: Camera, prm: PlanParams) -> PlanResult:
    warnings = []
    if prm.mode == "gsd":
        height = cam.height_for_gsd(prm.gsd_cm / 100)
    else:
        height = prm.height_m
    if height < prm.min_height_m:
        warnings.append(f"Height {height:.1f} m below minimum {prm.min_height_m} m - clamped.")
        height = prm.min_height_m
    if height > prm.max_height_m:
        warnings.append(f"Height {height:.1f} m above {prm.max_height_m} m (EU open category limit) - clamped.")
        height = prm.max_height_m
    gsd = cam.gsd_m(height)
    fw, fh = cam.footprint_m(height)              # long side, short side
    across, along = (fw, fh) if prm.orientation == "across" else (fh, fw)
    spacing = across * (1 - prm.side_overlap)
    trigger = along * (1 - prm.front_overlap)
    lead_in = along / 2 if prm.lead_in_m is None else prm.lead_in_m

    bearing = auto_bearing(poly) if prm.bearing_deg is None else prm.bearing_deg % 180
    area_poly = poly.buffer(prm.margin_m, join_style=2) if prm.margin_m else poly
    origin = poly.centroid

    sets = [(bearing, "main")]
    if prm.cross_grid:
        sets.append(((bearing + 90) % 180, "cross"))
    lines, info = [], []
    for b, kind in sets:
        ls, rot = _line_set(area_poly, b, spacing, lead_in, origin)
        ls = _snake(ls, rot, origin)
        if lines and ls:  # start the cross set at the end nearest to where the main set finished
            end = Point(lines[-1].coords[-1])
            if end.distance(Point(ls[-1].coords[-1])) < end.distance(Point(ls[0].coords[0])):
                ls = [LineString(list(l.coords)[::-1]) for l in ls[::-1]]
        for ln in ls:
            (x0, y0), (x1, y1) = ln.coords[0], ln.coords[-1]
            info.append({"line_no": len(lines) + 1, "set": kind, "length_m": round(ln.length, 1),
                         "heading_deg": round(math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360, 1),
                         "height_m": round(height, 1)})
            lines.append(ln)

    # speed: motion blur limit, comfortable max
    v_blur = prm.blur_px * gsd / cam.shutter_s
    speed = min(prm.speed_max_ms, v_blur)
    for d in info:
        d["speed_ms"] = round(speed, 1)

    # expected footprints along the lines at the trigger spacing
    fps, fpi = [], []
    for ln, d in zip(lines, info):
        (x0, y0), (x1, y1) = ln.coords[0], ln.coords[-1]
        ang = math.atan2(y1 - y0, x1 - x0)
        n = max(1, int(ln.length // trigger) + 1)
        for k in range(n):
            pt = ln.interpolate(min(k * trigger, ln.length))
            r = box(-along / 2, -across / 2, along / 2, across / 2)   # x = along track
            r = affinity.rotate(r, ang, origin=(0, 0), use_radians=True)
            fps.append(affinity.translate(r, pt.x, pt.y))
            fpi.append({"line_no": d["line_no"], "seq": k + 1})

    total_len = sum(l.length for l in lines)
    transit = sum(Point(lines[i].coords[-1]).distance(Point(lines[i + 1].coords[0])) for i in range(len(lines) - 1))
    t_s = (total_len + transit) / speed + prm.turn_s * max(0, len(lines) - 1)
    home = poly.centroid
    max_dist = max((home.distance(Point(c)) for l in lines for c in l.coords), default=0)
    stats = {
        "height_m": round(height, 1), "gsd_cm": round(gsd * 100, 2),
        "footprint_across_m": round(across, 1), "footprint_along_m": round(along, 1),
        "line_spacing_m": round(spacing, 1), "frame_spacing_m": round(trigger, 1),
        "bearing_deg": round(bearing, 1), "lines": len(lines), "line_length_m": round(total_len),
        "transit_m": round(transit), "speed_ms": round(speed, 1), "speed_blur_limit_ms": round(v_blur, 1),
        "frames_expected": len(fps), "area_ha": round(poly.area / 1e4, 2),
        "flight_time_min": round(t_s / 60, 1),
        "batteries": math.ceil(t_s / 60 / prm.battery_usable_min) if t_s else 0,
        "max_distance_from_centre_m": round(max_dist),
        "video_frames_per_spacing": round(trigger / speed * 29.97, 1),
    }
    if stats["batteries"] > 1:
        warnings.append(f"Needs {stats['batteries']} batteries - plan breaks between line groups.")
    if max_dist > 300:
        warnings.append(f"Lines reach {max_dist:.0f} m from the area centre - check visual line of sight.")
    return PlanResult(lines, info, fps, fpi, stats, warnings)
