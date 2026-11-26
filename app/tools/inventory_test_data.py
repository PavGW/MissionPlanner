"""One-off inventory of the raw test data (clips + photos) -> _doc/data/test_data_inventory.csv.

Prints per-clip telemetry statistics. Positions are summarised (centroid rounded to 0.01 deg is NOT
printed - only distances/heights/speeds) so the output can be shared in the docs.
"""
import csv
import math
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import imageio_ffmpeg  # noqa: E402
from PIL import Image, ExifTags  # noqa: E402
from missionplanner.srt import parse_srt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "test_data" / "raw"
OUT = ROOT / "_doc" / "data" / "test_data_inventory.csv"


def haversine(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(d))


def probe(mp4):
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(mp4)],
                       capture_output=True, text=True)
    line = next((l for l in r.stderr.splitlines() if "Video:" in l), "")
    res = next((p.strip().split()[0] for p in line.split(",") if "x" in p and p.strip()[0].isdigit()), "")
    fps = next((p.strip().split()[0] for p in line.split(",") if "fps" in p), "")
    return res, fps


rows = []
for srt in sorted(RAW.glob("*.SRT")):
    recs = parse_srt(srt)
    pts = [(r.lat, r.lon) for r in recs if r.lat is not None]
    dist = sum(haversine(pts[i - 1], pts[i]) for i in range(1, len(pts)))
    dur = recs[-1].t if recs else 0
    speeds = []
    for i in range(30, len(recs), 30):  # ~1 s steps
        a, b = recs[i - 30], recs[i]
        if a.lat is not None and b.lat is not None:
            speeds.append(haversine((a.lat, a.lon), (b.lat, b.lon)) / max(b.t - a.t, 1e-6))
    rel = [r.rel_alt for r in recs if r.rel_alt is not None]
    sh = [r.shutter for r in recs if r.shutter]
    res, fps = probe(srt.with_suffix(".MP4"))
    rows.append(dict(
        clip=srt.stem, start=recs[0].datetime if recs else "", frames=len(recs), duration_s=round(dur, 1),
        resolution=res, fps=fps, gps_frames=len(pts), path_m=round(dist), max_speed_ms=round(max(speeds, default=0), 1),
        median_speed_ms=round(sorted(speeds)[len(speeds) // 2], 1) if speeds else 0,
        rel_alt_min=round(min(rel), 1) if rel else "", rel_alt_max=round(max(rel), 1) if rel else "",
        shutter_slowest=f"1/{round(1 / max(sh))}" if sh else "", shutter_fastest=f"1/{round(1 / min(sh))}" if sh else "",
        iso_max=max((r.iso or 0) for r in recs),
    ))

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
for r in rows:
    print(r)

# photos: camera parameters relevant to photogrammetry (no GPS printed)
TAGS = {v: k for k, v in ExifTags.TAGS.items()}
print("\nphotos:")
for jpg in sorted(RAW.glob("*.JPG"))[:3]:
    im = Image.open(jpg)
    ex = im.getexif()
    sub = ex.get_ifd(0x8769)
    print(jpg.name, im.size, "Model", ex.get(TAGS["Model"]), "FocalLength", sub.get(TAGS["FocalLength"]),
          "FocalLength35", sub.get(TAGS["FocalLengthIn35mmFilm"]), "Exposure", sub.get(TAGS["ExposureTime"]))
