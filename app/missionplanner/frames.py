"""Frame selection (ground spacing + sharpness) and extraction from DJI video."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

import cv2
import imageio_ffmpeg
import numpy as np
import pandas as pd

from .camera import Camera
from .track import FLAG_BITS, FPS

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SCORE_W = 960  # width of the down-scaled frames used for the sharpness score


@dataclass
class FrameParams:
    front_overlap: float = 0.80
    reject: tuple = ("no_gps", "not_nadir", "tilt", "turning", "low")
    min_rel_sharpness: float = 0.6     # drop frames below this fraction of the clip's median sharpness
    ground_offset_m: float = 0.0       # take-off point height above the mapped ground (+ if launched from a hill)
    jpeg_quality: int = 2              # ffmpeg -q:v (2 = best practical)
    min_gap_frames: int = 3            # never pick frames closer than this

    def to_dict(self):
        d = asdict(self)
        d["reject"] = list(self.reject)
        return d

    @classmethod
    def from_dict(cls, d):
        d = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        if "reject" in d:
            d["reject"] = tuple(d["reject"])
        return cls(**d)


def reject_mask(names) -> int:
    m = 0
    for n in names:
        m |= FLAG_BITS[n]
    return m


def candidate_segments(g: pd.DataFrame, mask: int, pad: int = 0) -> list[tuple[int, int]]:
    """Runs [a, b] (frame0, inclusive) of consecutive frames passing the telemetry checks."""
    ok = (g["flags"].values & mask) == 0
    segs, a = [], None
    f0 = g["frame0"].values
    for i, v in enumerate(ok):
        if v and a is None:
            a = i
        if (not v or i == len(ok) - 1) and a is not None:
            b = i if v else i - 1
            segs.append((int(f0[max(0, a - pad)]), int(f0[min(len(ok) - 1, b + pad)])))
            a = None
    return segs


def sharpness_scores(mp4: str | Path, segments: list[tuple[int, int]],
                     progress: Callable[[float], None] | None = None) -> dict[int, float]:
    """Variance of the Laplacian of each frame in the segments (down-scaled grey). Returns {frame0: score}.

    The clip is decoded continuously from frame 0 up to the last wanted frame and frames are counted.
    Timestamp seeking (-ss) was tested and is NOT frame-exact on Neo files (off by one late in a clip),
    see _doc/03_dev_log.md."""
    out: dict[int, float] = {}
    if not segments:
        return out
    wanted = np.zeros(max(b for _, b in segments) + 1, dtype=bool)
    for a, b in segments:
        wanted[a:b + 1] = True
    last = len(wanted) - 1
    w, h = SCORE_W, int(round(SCORE_W * 2160 / 3840 / 2)) * 2
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(mp4), "-frames:v", str(last + 1),
           "-vf", f"scale={w}:{h}", "-fps_mode", "passthrough", "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = w * h
    k = 0
    try:
        while k <= last:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            if wanted[k]:
                img = np.frombuffer(buf, np.uint8).reshape(h, w)
                out[k] = float(cv2.Laplacian(img, cv2.CV_32F).var())
            k += 1
            if progress and k % 60 == 0:
                progress(k / (last + 1))
    finally:
        p.stdout.close()
        p.wait()
    if progress:
        progress(1.0)
    return out


def select(df: pd.DataFrame, cam: Camera, prm: FrameParams, scores: dict[str, dict[int, float]]) -> pd.DataFrame:
    """Choose one sharp frame per required ground spacing. ``scores`` = {clip: {frame0: sharpness}}.

    The along-track distance is normalised by the spacing required at each frame's own height,
    u = integral(ds / spacing(h)); one frame is taken per unit of u (the sharpest in that bin)."""
    mask = reject_mask(prm.reject)
    picks = []
    for clip, g in df.groupby("clip", sort=False):
        sc = scores.get(clip, {})
        g = g.copy()
        g["sharpness"] = g["frame0"].map(sc)
        h = (g["rel_alt"].astype(float) + prm.ground_offset_m).clip(lower=1.0)
        along = cam.footprint_m(1.0)[1] * h          # nadir, image long side across the track
        spacing = along * (1 - prm.front_overlap)
        ds = np.diff(g["dist"].values, prepend=g["dist"].values[0])
        g["u"] = np.cumsum(ds / spacing.values)
        g["spacing_m"] = spacing.values
        ok = ((g["flags"].values & mask) == 0) & g["sharpness"].notna().values
        if not ok.any():
            continue
        med = float(np.nanmedian(g.loc[ok, "sharpness"]))
        ok &= (g["sharpness"].fillna(0).values >= prm.min_rel_sharpness * med)
        g = g[ok]
        g["bin"] = np.floor(g["u"]).astype(int)
        best = g.loc[g.groupby("bin")["sharpness"].idxmax()].sort_values("frame0")
        # enforce a minimal gap between consecutive picks
        keep, last = [], -10 ** 9
        for idx, f in zip(best.index, best["frame0"]):
            if f - last >= prm.min_gap_frames:
                keep.append(idx)
                last = f
        best = best.loc[keep]
        best["rel_sharpness"] = best["sharpness"] / med
        picks.append(best)
    if not picks:
        return pd.DataFrame()
    sel = pd.concat(picks).reset_index(drop=True)
    sel["image"] = [f"{c}_f{int(f):05d}.jpg" for c, f in zip(sel["clip"], sel["frame0"])]
    return sel


def extract(mp4: str | Path, frame_list: list[int], out_dir: str | Path, names: list[str] | None = None,
            quality: int = 2, progress: Callable[[float], None] | None = None) -> list[Path]:
    """Decode the given frames (frame0 indices) at full resolution and save as JPG (single decoding pass)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_list = sorted(int(f) for f in frame_list)
    if not frame_list:
        return []
    names = names or [f"{Path(mp4).stem}_f{f:05d}.jpg" for f in frame_list]
    order = {f: n for f, n in zip(sorted(frame_list), [n for _, n in sorted(zip(frame_list, names))])}
    tmp = out_dir / "_tmp_extract"
    tmp.mkdir(exist_ok=True)
    for old in tmp.glob("*.jpg"):
        old.unlink()
    script = tmp / "select.txt"
    # decode from frame 0 (no -ss: seeking is not frame-exact on these files); n = absolute frame index
    expr = "+".join(f"eq(n\\,{f})" for f in frame_list)
    script.write_text(f"select={expr}", encoding="ascii")
    # ffmpeg >= 7: '-/vf file' reads the filter graph from a file (long select expressions)
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
           "-/vf", str(script), "-fps_mode", "passthrough", "-q:v", str(quality),
           "-frames:v", str(len(frame_list)), str(tmp / "%06d.jpg")]
    subprocess.run(cmd, check=True)
    outs = sorted(tmp.glob("*.jpg"))
    res = []
    for f, p in zip(frame_list, outs):
        dst = out_dir / order[f]
        if dst.exists():
            dst.unlink()
        p.rename(dst)
        res.append(dst)
    for p in tmp.glob("*"):
        p.unlink()
    tmp.rmdir()
    if len(res) != len(frame_list):
        raise RuntimeError(f"extracted {len(res)} of {len(frame_list)} frames from {Path(mp4).name}")
    if progress:
        progress(1.0)
    return res
