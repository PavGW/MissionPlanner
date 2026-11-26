"""Field exports of a plan: KML (phone / Google Earth / DJI Fly map overlay) and a printable PDF cheat sheet."""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import simplekml
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .geo import geom_to
from .project import Project


def _layers(prj: Project):
    area = prj.read_layer("survey_area")
    lines = prj.read_layer("plan_lines")
    if area is None or lines is None:
        raise ValueError("Run the planner first")
    return area, lines.sort_values("line_no")


# ------------------------------------------------------------------------------------------------
# KML
# ------------------------------------------------------------------------------------------------
def export_kml(prj: Project, out: Path | None = None) -> Path:
    area, lines = _layers(prj)
    st = prj.settings["stats"]["plan"]
    e = prj.epsg
    out = out or prj.exports_dir / f"{prj.root.name}_plan.kml"
    out.parent.mkdir(parents=True, exist_ok=True)
    k = simplekml.Kml(name=f"{prj.settings['title']} - flight plan")
    k.document.description = (f"Height {st['height_m']} m above take-off, speed {st['speed_ms']} m/s, "
                              f"GSD {st['gsd_cm']} cm, {st['lines']} lines, {st['flight_time_min']} min")
    a = geom_to(area.geometry.iloc[0], e, 4326)
    pg = k.newpolygon(name="Survey area", outerboundaryis=list(a.exterior.coords))
    pg.style.polystyle.color = simplekml.Color.changealphaint(40, simplekml.Color.yellow)
    pg.style.linestyle.color = simplekml.Color.yellow
    pg.style.linestyle.width = 3
    fl = k.newfolder(name="Flight lines")
    for _, r in lines.iterrows():
        g = geom_to(r.geometry, e, 4326)
        ls = fl.newlinestring(name=f"Line {r.line_no}", coords=list(g.coords))
        ls.description = f"heading {r.heading_deg:.0f} deg, {r.length_m:.0f} m, height {r.height_m:.0f} m"
        ls.style.linestyle.color = simplekml.Color.red if r.get("set", "main") == "main" else simplekml.Color.cyan
        ls.style.linestyle.width = 4
        for lab, c in ((f"{r.line_no}S", g.coords[0]), (f"{r.line_no}E", g.coords[-1])):
            p = fl.newpoint(name=lab, coords=[c])
            p.style.iconstyle.scale = 0.6
            p.style.iconstyle.icon.href = ("http://maps.google.com/mapfiles/kml/paddle/grn-circle.png" if lab.endswith("S")
                                           else "http://maps.google.com/mapfiles/kml/paddle/red-circle.png")
    k.save(str(out))
    return out


# ------------------------------------------------------------------------------------------------
# PDF cheat sheet
# ------------------------------------------------------------------------------------------------
def export_pdf(prj: Project, out: Path | None = None) -> Path:
    """One landscape A4 page: plan drawing (north up, numbered lines, scale bar) + settings + line table."""
    area, lines = _layers(prj)
    st = prj.settings["stats"]["plan"]
    cam = prj.camera
    out = out or prj.exports_dir / f"{prj.root.name}_cheatsheet.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = landscape(A4)
    c = canvas.Canvas(str(out), pagesize=(W, H))
    c.setTitle(f"{prj.settings['title']} - flight cheat sheet")

    # --- header
    c.setFont("Helvetica-Bold", 15)
    c.drawString(12 * mm, H - 14 * mm, f"{prj.settings['title']} - flight cheat sheet")
    c.setFont("Helvetica", 8)
    c.drawRightString(W - 12 * mm, H - 14 * mm, f"MissionPlanner  {date.today().isoformat()}  EPSG:{prj.epsg}")

    # --- map frame (left)
    mx0, my0, mw, mh = 12 * mm, 12 * mm, 170 * mm, H - 32 * mm
    c.setStrokeColor(colors.grey)
    c.rect(mx0, my0, mw, mh)
    geoms = [area.geometry.iloc[0]] + list(lines.geometry)
    xs = [x for g in geoms for x, _ in (g.exterior.coords if g.geom_type == "Polygon" else g.coords)]
    ys = [y for g in geoms for _, y in (g.exterior.coords if g.geom_type == "Polygon" else g.coords)]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pad = 10 * mm
    s = min((mw - 2 * pad) / max(1, x1 - x0), (mh - 2 * pad) / max(1, y1 - y0))
    ox = mx0 + (mw - (x1 - x0) * s) / 2
    oy = my0 + (mh - (y1 - y0) * s) / 2

    def P(x, y):
        return ox + (x - x0) * s, oy + (y - y0) * s

    # survey area
    a = area.geometry.iloc[0]
    pth = c.beginPath()
    for i, (x, y) in enumerate(a.exterior.coords):
        (pth.moveTo if i == 0 else pth.lineTo)(*P(x, y))
    pth.close()
    c.setFillColor(colors.Color(1, 0.9, 0.3, alpha=0.25))
    c.setStrokeColor(colors.Color(0.8, 0.6, 0))
    c.setLineWidth(1.2)
    c.drawPath(pth, fill=1, stroke=1)

    # flight lines with direction arrows, start/end labels, transit dashed
    prev_end = None
    for _, r in lines.iterrows():
        pts = [P(x, y) for x, y in r.geometry.coords]
        if prev_end is not None:
            c.setDash(2, 2); c.setStrokeColor(colors.grey); c.setLineWidth(0.6)
            c.line(*prev_end, *pts[0]); c.setDash()
        main = r.get("set", "main") == "main"
        col = colors.Color(0.8, 0.1, 0.1) if main else colors.Color(0, 0.5, 0.7)
        c.setStrokeColor(col)
        c.setLineWidth(1.4)
        c.line(*pts[0], *pts[-1])
        # arrow at the middle
        (ax, ay), (bx, by) = pts[0], pts[-1]
        ang = math.atan2(by - ay, bx - ax)
        cx, cy = (ax + bx) / 2, (ay + by) / 2
        L = 3.2 * mm
        c.setFillColor(col)
        ar = c.beginPath()
        ar.moveTo(cx + L * math.cos(ang), cy + L * math.sin(ang))
        ar.lineTo(cx + L * math.cos(ang + 2.6), cy + L * math.sin(ang + 2.6))
        ar.lineTo(cx + L * math.cos(ang - 2.6), cy + L * math.sin(ang - 2.6))
        ar.close()
        c.drawPath(ar, fill=1, stroke=0)
        # line number at start
        c.setFillColor(colors.white); c.setStrokeColor(colors.black); c.setLineWidth(0.5)
        c.circle(ax, ay, 2.6 * mm, fill=1, stroke=1)
        c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(ax, ay - 2.4, str(r.line_no))
        prev_end = pts[-1]
    # take-off hint: start of line 1
    sx, sy = P(*lines.geometry.iloc[0].coords[0])
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(sx + 3.5 * mm, sy + 2 * mm, "start")

    # north arrow
    nx, ny = mx0 + mw - 10 * mm, my0 + mh - 18 * mm
    c.setFillColor(colors.black)
    na = c.beginPath(); na.moveTo(nx, ny + 10 * mm); na.lineTo(nx - 2.5 * mm, ny); na.lineTo(nx + 2.5 * mm, ny); na.close()
    c.drawPath(na, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 9); c.drawCentredString(nx, ny + 11.5 * mm, "N")

    # scale bar (nice round length ~ 1/4 of the frame)
    target = (mw / 4) / s
    nice = max(v for v in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000) if v <= max(5, target))
    bx0, by0 = mx0 + 6 * mm, my0 + 6 * mm
    c.setLineWidth(1.5); c.setStrokeColor(colors.black)
    c.line(bx0, by0, bx0 + nice * s, by0)
    c.line(bx0, by0 - 1 * mm, bx0, by0 + 1 * mm); c.line(bx0 + nice * s, by0 - 1 * mm, bx0 + nice * s, by0 + 1 * mm)
    c.setFont("Helvetica", 7); c.drawCentredString(bx0 + nice * s / 2, by0 + 1.5 * mm, f"{nice} m")

    # --- right column: settings
    rx = mx0 + mw + 8 * mm
    y = H - 26 * mm

    def head(t):
        nonlocal y
        c.setFont("Helvetica-Bold", 10); c.setFillColor(colors.black); c.drawString(rx, y, t); y -= 5 * mm

    def row(k, v, bold=False):
        nonlocal y
        c.setFont("Helvetica", 8.5); c.drawString(rx, y, k)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 8.5); c.drawString(rx + 42 * mm, y, str(v)); y -= 4.3 * mm

    head("Fly")
    row("Height above take-off", f"{st['height_m']:.0f} m", True)
    row("Ground speed", f"{st['speed_ms']:.1f} m/s  (max {st['speed_blur_limit_ms']:.1f})", True)
    row("Line spacing", f"{st['line_spacing_m']:.0f} m")
    row("Lines / heading", f"{st['lines']} / {st['bearing_deg']:.0f} deg")
    row("Line length total", f"{st['line_length_m']:.0f} m (+{st['transit_m']:.0f} m transit)")
    row("Flight time / batteries", f"{st['flight_time_min']:.1f} min / {st['batteries']}", True)
    row("Max distance from centre", f"{st['max_distance_from_centre_m']:.0f} m")
    y -= 2 * mm
    head("Camera (DJI Fly)")
    row("Mode", "Video 4K 30 fps")
    row("Gimbal", "-90 deg (straight down)", True)
    row("Shutter", f"1/{round(1 / cam.shutter_s)} s or faster (Pro mode)")
    row("Expected GSD / footprint", f"{st['gsd_cm']:.1f} cm / {st['footprint_across_m']:.0f} x {st['footprint_along_m']:.0f} m")
    row("Frames to keep", f"every {st['frame_spacing_m']:.1f} m  (~{st['frames_expected']})")
    y -= 2 * mm
    head("Checklist")
    for t in ("Site legal & clear, take-off near line 1 start",
              "Calibrate compass if asked; wait for GPS lock",
              "Climb to height before line 1, set gimbal -90",
              "Start recording, fly each line straight & level",
              "Turn slowly outside the area (lead-in)",
              "Keep the drone in sight; land at 25 % battery"):
        c.setFont("Helvetica", 8); c.rect(rx, y - 0.5 * mm, 2.6 * mm, 2.6 * mm); c.drawString(rx + 4 * mm, y, t); y -= 4.3 * mm

    # line table
    y -= 2 * mm
    head("Lines")
    c.setFont("Helvetica-Bold", 7.5)
    for xo, t in ((0, "#"), (8, "heading"), (24, "length"), (40, "done")):
        c.drawString(rx + xo * mm, y, t)
    y -= 3.8 * mm
    c.setFont("Helvetica", 7.5)
    for _, r in lines.iterrows():
        if y < 14 * mm:
            c.drawString(rx, y, "..."); break
        c.drawString(rx, y, str(r.line_no))
        c.drawString(rx + 8 * mm, y, f"{r.heading_deg:.0f} deg")
        c.drawString(rx + 24 * mm, y, f"{r.length_m:.0f} m")
        c.rect(rx + 41 * mm, y - 0.5 * mm, 2.6 * mm, 2.6 * mm)
        y -= 3.6 * mm
    c.showPage()
    c.save()
    return out
