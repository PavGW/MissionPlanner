"""Parser for DJI .SRT telemetry files (one subtitle block per video frame).

Tested on DJI Neo files, block layout:

    1
    00:00:00,000 --> 00:00:00,033
    <font size="28">FrameCnt: 1, DiffTime: 33ms
    2025-06-30 18:45:50.538
    [iso: 150] [shutter: 1/320.0] [fnum: 2.8] [ev: 0] [ct: 5482] [color_md: default]
    [ae_meter_md: 1] [latitude: ..] [longitude: ..] [rel_alt: .. abs_alt: ..] </font>

Other DJI models use slightly different keys (e.g. ``altitude`` or ``[rel_alt: x abs_alt: y]``,
``longtitude`` typo); the parser is key-based and tolerant of those variants.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->")
_FRAME = re.compile(r"FrameCnt\s*:\s*(\d+)")
_DT = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)?)")
_KV = re.compile(r"([A-Za-z_]+)\s*:\s*([-+]?[\d./]+|[A-Za-z_]+)")


@dataclass
class SrtRecord:
    frame: int            # 1-based frame counter from the SRT
    t: float              # seconds from start of the clip (subtitle start time)
    datetime: str         # local wall-clock time as written by the drone (no time zone)
    lat: float | None
    lon: float | None
    rel_alt: float | None  # m above take-off point (barometric)
    abs_alt: float | None  # m, GPS altitude (ellipsoid/MSL depends on model; Neo ~ MSL)
    iso: float | None
    shutter: float | None  # exposure time in seconds
    fnum: float | None
    ev: float | None
    ct: float | None       # colour temperature, K

    def as_dict(self) -> dict:
        return asdict(self)


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        if "/" in v:
            a, b = v.split("/", 1)
            return float(a) / float(b)
        return float(v)
    except ValueError:
        return None


def parse_srt(path: str | Path) -> list[SrtRecord]:
    """Parse a DJI SRT file into a list of per-frame records (in file order)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", " ", text)              # drop <font> tags
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    out: list[SrtRecord] = []
    for b in blocks:
        mt = _TIME.search(b)
        if not mt:
            continue
        h, m, s, ms = mt.groups()
        t = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 10 ** len(ms)
        body = b[mt.end():]
        mf = _FRAME.search(body)
        md = _DT.search(body)
        kv = {k.lower(): v for k, v in _KV.findall(body)}
        lat = _num(kv.get("latitude"))
        lon = _num(kv.get("longitude", kv.get("longtitude")))
        if lat == 0 and lon == 0:                      # no GPS fix
            lat = lon = None
        out.append(SrtRecord(
            frame=int(mf.group(1)) if mf else len(out) + 1,
            t=t,
            datetime=md.group(1).replace(",", ".") if md else "",
            lat=lat, lon=lon,
            rel_alt=_num(kv.get("rel_alt", kv.get("altitude"))),
            abs_alt=_num(kv.get("abs_alt")),
            iso=_num(kv.get("iso")),
            shutter=_num(kv.get("shutter")),
            fnum=_num(kv.get("fnum")),
            ev=_num(kv.get("ev")),
            ct=_num(kv.get("ct")),
        ))
    return out


def parse_datetime(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None
