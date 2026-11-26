"""Minimal reader for the DJI 'djmd' timed-metadata track in DJI Neo MP4 files.

The track is a sequence of protobuf messages (schema ``dvtm_dji_neo.proto``, not published). This module
contains a schema-less protobuf wire-format decoder; the field paths for the quantities we need were
identified empirically (see _doc/03_dev_log.md, "djmd metadata stream").
"""
from __future__ import annotations

import struct
import subprocess
from pathlib import Path

import imageio_ffmpeg


def extract_stream(mp4: str | Path, out_bin: str | Path) -> Path:
    """Copy the djmd data track (stream 0:1) of an MP4 to a raw binary file."""
    out_bin = Path(out_bin)
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(mp4), "-map", "0:d:0", "-c", "copy", "-f", "data", str(out_bin)], check=True)
    return out_bin


def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        if c < 0x80:
            return r, i
        s += 7


def decode(b: bytes, depth: int = 0, max_depth: int = 8):
    """Decode protobuf wire format into a list of (field, wiretype, value).

    Length-delimited values are decoded recursively when they parse cleanly as a message, otherwise kept
    as bytes. Fixed32 values are returned as float; fixed64 as double."""
    out = []
    i = 0
    n = len(b)
    while i < n:
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if f == 0:
            raise ValueError("field 0")
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 1:
            v = struct.unpack_from("<d", b, i)[0]
            i += 8
        elif wt == 5:
            v = struct.unpack_from("<f", b, i)[0]
            i += 4
        elif wt == 2:
            ln, i = _varint(b, i)
            if i + ln > n:
                raise ValueError("overrun")
            raw = b[i:i + ln]
            i += ln
            v = raw
            if depth < max_depth and ln:
                try:
                    v = decode(raw, depth + 1, max_depth)
                except (ValueError, IndexError, struct.error):
                    v = raw
        else:
            raise ValueError(f"wiretype {wt}")
        out.append((f, wt, v))
    if i != n:
        raise ValueError("trailing")
    return out


def flatten(msg, prefix: str = "") -> dict[str, list]:
    """Flatten a decoded message into {'1.3.2': [values...]} (repeated paths keep all values)."""
    d: dict[str, list] = {}
    for f, wt, v in msg:
        p = f"{prefix}.{f}" if prefix else str(f)
        if isinstance(v, list):
            for k, vv in flatten(v, p).items():
                d.setdefault(k, []).extend(vv)
        else:
            d.setdefault(p, []).append(v)
    return d


def frames(stream: bytes, frame_field: int = 3):
    """Yield the per-frame sub-messages (top-level field ``frame_field``) of a djmd stream."""
    for f, wt, v in decode(stream, max_depth=0):
        if f == frame_field and wt == 2:
            yield decode(v)


# Empirical field map for the DJI Neo (firmware 10.00.04.91), per-frame message = top-level field 3.
# Angles are signed varints in 0.1 deg; proto3 omits zero values, so a missing field means 0.
NEO_FIELDS = {
    "time_us": "1.2",          # microseconds, monotonic
    "lat_rad": "4.4.1.2",      # double, radians
    "lon_rad": "4.4.1.3",      # double, radians
    "abs_alt_mm": "4.4.2",
    "rel_alt_mm": "4.5.1",     # above take-off
    "drone_pitch": "4.3.1",
    "drone_roll": "4.3.2",
    "drone_yaw": "4.3.3",
    "gimbal_pitch": "5.2.1",   # 0 = horizon, -90 = nadir
    "gimbal_roll": "5.2.2",
    "gimbal_yaw": "5.2.3",
    "iso": "2.3.1",
    "ct": "2.6.1",
}
_ANGLES = {"drone_pitch", "drone_roll", "drone_yaw", "gimbal_pitch", "gimbal_roll", "gimbal_yaw"}


def _signed(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


def _num(v):
    """int varint (signed) or float fixed32/64 -> number; anything else -> None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return _signed(v)
    if isinstance(v, float):
        return v
    return None


def read_telemetry(mp4: str | Path, work_dir: str | Path | None = None) -> list[dict]:
    """Per-frame telemetry from the djmd track: lat/lon (deg), abs/rel alt (m), drone + gimbal attitude (deg).

    Returns [] if the file has no djmd track or it cannot be decoded."""
    import math
    import tempfile

    mp4 = Path(mp4)
    tmp = None
    if work_dir is None:
        tmp = tempfile.TemporaryDirectory()
        work_dir = tmp.name
    try:
        binf = extract_stream(mp4, Path(work_dir) / (mp4.stem + "_djmd.bin"))
        data = binf.read_bytes()
    except subprocess.CalledProcessError:
        return []
    finally:
        pass
    out = []
    try:
        for i, m in enumerate(frames(data)):
            f = flatten(m)
            g = lambda k, d=None: f[NEO_FIELDS[k]][0] if NEO_FIELDS[k] in f else d  # noqa: E731
            rec = {"idx": i}
            lat, lon = g("lat_rad"), g("lon_rad")
            rec["lat"] = math.degrees(lat) if isinstance(lat, float) else None
            rec["lon"] = math.degrees(lon) if isinstance(lon, float) else None
            if rec["lat"] == 0 and rec["lon"] == 0:
                rec["lat"] = rec["lon"] = None
            rec["time_us"] = g("time_us")
            a, r = g("abs_alt_mm"), g("rel_alt_mm", 0)
            rec["abs_alt"] = _num(a) / 1000 if _num(a) is not None else None
            rec["rel_alt_djmd"] = _num(r) / 1000 if _num(r) is not None else None
            for k in _ANGLES:
                v = g(k, 0)
                rec[k] = _signed(v) / 10 if isinstance(v, int) else None
            out.append(rec)
    except (ValueError, IndexError, struct.error):
        return []
    finally:
        if tmp:
            tmp.cleanup()
    return out
