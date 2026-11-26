"""Flight track: merge djmd (MP4) + SRT telemetry per video frame, derive kinematics and QA flags."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

from .djmd import read_telemetry
from .geo import to_utm, utm_epsg
from .srt import parse_srt

FPS = 30000 / 1001  # exact NTSC rate; 29.97 drifts by a frame after ~1000 s


@dataclass
class TrackParams:
    nadir_tol_deg: float = 5.0        # |gimbal_pitch + 90| above this -> not nadir
    tilt_max_deg: float = 15.0        # drone pitch/roll above this -> accelerating/braking hard
    yaw_rate_max_dps: float = 8.0     # turning
    hover_speed_ms: float = 0.3
    min_height_m: float = 5.0         # below -> take-off / landing
    smooth_s: float = 1.0             # window for smoothing positions before speed/distance

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


FLAG_BITS = {"no_gps": 1, "not_nadir": 2, "tilt": 4, "turning": 8, "low": 16, "hover": 32}


def load_clip(mp4: str | Path, work_dir: str | Path | None = None) -> pd.DataFrame:
    """Per-frame telemetry of one clip (djmd preferred, SRT for exposure and as fallback)."""
    mp4 = Path(mp4)
    srt = mp4.with_suffix(".SRT")
    if not srt.exists():
        srt = mp4.with_suffix(".srt")
    s = pd.DataFrame([r.as_dict() for r in parse_srt(srt)]) if srt.exists() else pd.DataFrame()
    d = pd.DataFrame(read_telemetry(mp4, work_dir))
    if d.empty and s.empty:
        raise ValueError(f"No telemetry for {mp4.name} (no djmd track and no SRT)")
    if d.empty:
        df = s.copy()
        df["source"] = "srt"
        for k in ("drone_pitch", "drone_roll", "drone_yaw", "gimbal_pitch", "gimbal_roll", "gimbal_yaw"):
            df[k] = np.nan
    else:
        df = d.rename(columns={"idx": "frame0"})
        df["source"] = "djmd"
        if not s.empty and len(s) == len(df):
            for k in ("t", "datetime", "iso", "shutter", "fnum", "ev", "ct", "rel_alt"):
                df[k] = s[k].values
        else:
            df["t"] = df["frame0"] / FPS
            df["rel_alt"] = df["rel_alt_djmd"]
            df["datetime"] = ""
    if "frame0" not in df:
        df["frame0"] = np.arange(len(df))
    df["clip"] = mp4.stem
    return df


def _wrap180(a):
    return (a + 180) % 360 - 180


def derive(df: pd.DataFrame, epsg: int | None = None, prm: TrackParams | None = None) -> tuple[pd.DataFrame, int]:
    """Add x/y (UTM), smoothed speed, along-track distance, yaw rate and QA flags. Returns (df, epsg)."""
    prm = prm or TrackParams()
    df = df.copy()
    ok = df["lat"].notna() & df["lon"].notna()
    if epsg is None:
        if not ok.any():
            raise ValueError("No GPS positions in track")
        epsg = utm_epsg(float(df.loc[ok, "lon"].median()), float(df.loc[ok, "lat"].median()))
    tf = to_utm(epsg)
    x = np.full(len(df), np.nan)
    y = np.full(len(df), np.nan)
    if ok.any():
        x[ok.values], y[ok.values] = tf(df.loc[ok, "lon"].values, df.loc[ok, "lat"].values)
    df["x"], df["y"] = x, y

    out = []
    for _, g in df.groupby("clip", sort=False):
        g = g.copy()
        w = max(1, int(round(prm.smooth_s * FPS)))
        xs = g["x"].interpolate(limit_direction="both").rolling(w, center=True, min_periods=1).mean()
        ys = g["y"].interpolate(limit_direction="both").rolling(w, center=True, min_periods=1).mean()
        t = g["t"].values.astype(float)
        dt = np.gradient(t) if len(t) > 1 else np.ones(len(t))
        dt[dt <= 0] = 1 / FPS
        vx, vy = np.gradient(xs.values) / dt, np.gradient(ys.values) / dt
        g["speed"] = np.hypot(vx, vy)
        step = np.hypot(np.diff(xs.values, prepend=xs.values[0]), np.diff(ys.values, prepend=ys.values[0]))
        g["dist"] = np.cumsum(step)
        g["course"] = (np.degrees(np.arctan2(vx, vy)) % 360)
        yaw = g["drone_yaw"].astype(float)
        if yaw.notna().any():
            yu = np.degrees(np.unwrap(np.radians(yaw.ffill().bfill().values)))
            yu = pd.Series(yu).rolling(w, center=True, min_periods=1).mean().values
            g["yaw_rate"] = np.abs(np.gradient(yu) / dt)
        else:
            c = np.degrees(np.unwrap(np.radians(g["course"].values)))
            g["yaw_rate"] = np.where(g["speed"] > 1, np.abs(np.gradient(c) / dt), 0)
        out.append(g)
    df = pd.concat(out)

    flags = np.zeros(len(df), dtype=int)
    gp = df["gimbal_pitch"].astype(float)
    flags |= np.where(~ok.values, FLAG_BITS["no_gps"], 0)
    flags |= np.where(gp.notna() & ((gp + 90).abs() > prm.nadir_tol_deg), FLAG_BITS["not_nadir"], 0)
    tilt = np.maximum(df["drone_pitch"].abs().fillna(0), df["drone_roll"].abs().fillna(0))
    flags |= np.where(tilt > prm.tilt_max_deg, FLAG_BITS["tilt"], 0)
    flags |= np.where(df["yaw_rate"] > prm.yaw_rate_max_dps, FLAG_BITS["turning"], 0)
    flags |= np.where(df["rel_alt"].fillna(0) < prm.min_height_m, FLAG_BITS["low"], 0)
    flags |= np.where(df["speed"] < prm.hover_speed_ms, FLAG_BITS["hover"], 0)
    df["flags"] = flags
    df["flag_text"] = [",".join(k for k, b in FLAG_BITS.items() if f & b) for f in flags]
    return df.reset_index(drop=True), epsg


def summary(df: pd.DataFrame) -> dict:
    """Flight QA summary per clip."""
    rows = []
    for clip, g in df.groupby("clip", sort=False):
        n = len(g)
        r = {"clip": clip, "frames": n, "duration_s": round(float(g["t"].max()), 1),
             "path_m": round(float(g["dist"].max()), 1),
             "height_min": round(float(g["rel_alt"].min()), 1), "height_max": round(float(g["rel_alt"].max()), 1),
             "speed_median": round(float(g["speed"].median()), 2), "speed_max": round(float(g["speed"].max()), 2),
             "nadir_pct": round(100 * float((g["flags"] & FLAG_BITS["not_nadir"] == 0).mean()), 1),
             "usable_pct": round(100 * float((g["flags"] & ~FLAG_BITS["hover"] == 0).mean()), 1)}
        for k, b in FLAG_BITS.items():
            r[f"{k}_pct"] = round(100 * float((g["flags"] & b > 0).mean()), 1)
        rows.append(r)
    return rows
