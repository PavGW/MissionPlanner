"""Write EXIF (GPS, camera, time) and DJI-style XMP (gimbal/flight attitude) into extracted JPG frames.

OpenDroneMap reads: EXIF GPS lat/lon/alt, Make/Model, FocalLength, FocalLengthIn35mmFilm, image size,
DateTimeOriginal (+SubSecTimeOriginal), GPSHPositioningError / XMP accuracy tags (GPS standard deviation),
and the DJI XMP block (drone-dji:GimbalPitchDegree etc.)."""
from __future__ import annotations

import math
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import piexif

from .camera import Camera


def _rat(v: float, den: int = 10000) -> tuple[int, int]:
    f = Fraction(v).limit_denominator(den)
    return (f.numerator, f.denominator)


def _dms(deg: float):
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = (deg - d - m / 60) * 3600
    return ((d, 1), (m, 1), (int(round(s * 10000)), 10000))


def _xmp(rec: dict, xy_std: float, z_std: float) -> bytes:
    def f(k, fmt="{:+.2f}"):
        v = rec.get(k)
        return fmt.format(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else "+0.00"
    xml = f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="DJI Meta Data"
    xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/"
    xmlns:Camera="http://pix4d.com/camera/1.0/"
   drone-dji:AbsoluteAltitude="{f('abs_alt')}"
   drone-dji:RelativeAltitude="{f('rel_alt')}"
   drone-dji:GimbalRollDegree="{f('gimbal_roll')}"
   drone-dji:GimbalYawDegree="{f('gimbal_yaw')}"
   drone-dji:GimbalPitchDegree="{f('gimbal_pitch')}"
   drone-dji:FlightRollDegree="{f('drone_roll')}"
   drone-dji:FlightYawDegree="{f('drone_yaw')}"
   drone-dji:FlightPitchDegree="{f('drone_pitch')}"
   drone-dji:ProductName="NEO"
   drone-dji:SourceVideo="{rec.get('clip', '')}"
   drone-dji:SourceFrame="{rec.get('frame0', '')}"
   drone-dji:GpsXYAccuracy="{xy_std:.2f}"
   drone-dji:GpsZAccuracy="{z_std:.2f}"
   Camera:GPSXYAccuracy="{xy_std:.2f}"
   Camera:GPSZAccuracy="{z_std:.2f}"/>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    return xml.encode("utf-8")


def _insert_xmp(jpg: bytes, xmp: bytes) -> bytes:
    """Insert (or replace) the XMP APP1 segment right after SOI/APP0/EXIF."""
    hdr = b"http://ns.adobe.com/xap/1.0/\x00"
    seg = b"\xff\xe1" + (len(hdr) + len(xmp) + 2).to_bytes(2, "big") + hdr + xmp
    assert jpg[:2] == b"\xff\xd8"
    i = 2
    out = [jpg[:2]]
    insert_at = None
    while i < len(jpg) and jpg[i] == 0xFF and jpg[i + 1] in (0xE0, 0xE1, 0xE2, 0xED, 0xFE):
        ln = int.from_bytes(jpg[i + 2:i + 4], "big")
        s = jpg[i:i + 2 + ln]
        if not (jpg[i + 1] == 0xE1 and s[4:4 + len(hdr)] == hdr):  # drop old XMP
            out.append(s)
        i += 2 + ln
    insert_at = len(out)
    out.insert(insert_at, seg)
    out.append(jpg[i:])
    return b"".join(out)


def geotag(path: str | Path, rec: dict, cam: Camera, xy_std: float = 3.0, z_std: float = 5.0,
           alt_mode: str = "abs") -> None:
    """Write EXIF + XMP into ``path``. ``rec`` needs lat, lon, abs_alt/rel_alt, datetime (str), angles optional.

    alt_mode: 'abs' = drone absolute altitude (default), 'rel' = height above take-off."""
    path = Path(path)
    lat, lon = float(rec["lat"]), float(rec["lon"])
    alt = rec.get("abs_alt") if alt_mode == "abs" else rec.get("rel_alt")
    alt = float(alt) if alt is not None and not math.isnan(alt) else 0.0
    dt = None
    if rec.get("datetime"):
        try:
            dt = datetime.fromisoformat(str(rec["datetime"]))
        except ValueError:
            dt = None
    zeroth = {piexif.ImageIFD.Make: cam.make.encode(), piexif.ImageIFD.Model: cam.model.encode(),
              piexif.ImageIFD.Software: b"MissionPlanner video frame extraction",
              piexif.ImageIFD.ImageWidth: cam.width_px, piexif.ImageIFD.ImageLength: cam.height_px}
    exif = {piexif.ExifIFD.FocalLength: _rat(cam.focal_mm, 1000),
            piexif.ExifIFD.FocalLengthIn35mmFilm: int(round(cam.focal35_mm)),
            piexif.ExifIFD.PixelXDimension: cam.width_px, piexif.ExifIFD.PixelYDimension: cam.height_px}
    if rec.get("shutter"):
        exif[piexif.ExifIFD.ExposureTime] = _rat(float(rec["shutter"]), 100000)
    if rec.get("fnum"):
        exif[piexif.ExifIFD.FNumber] = _rat(float(rec["fnum"]), 100)
    if rec.get("iso"):
        exif[piexif.ExifIFD.ISOSpeedRatings] = int(rec["iso"])
    if dt:
        exif[piexif.ExifIFD.DateTimeOriginal] = dt.strftime("%Y:%m:%d %H:%M:%S").encode()
        exif[piexif.ExifIFD.DateTimeDigitized] = dt.strftime("%Y:%m:%d %H:%M:%S").encode()
        exif[piexif.ExifIFD.SubSecTimeOriginal] = f"{dt.microsecond // 1000:03d}".encode()
        zeroth[piexif.ImageIFD.DateTime] = dt.strftime("%Y:%m:%d %H:%M:%S").encode()
    gps = {piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
           piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S", piexif.GPSIFD.GPSLatitude: _dms(lat),
           piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W", piexif.GPSIFD.GPSLongitude: _dms(lon),
           piexif.GPSIFD.GPSAltitudeRef: 0 if alt >= 0 else 1, piexif.GPSIFD.GPSAltitude: _rat(abs(alt), 1000),
           piexif.GPSIFD.GPSMapDatum: b"WGS-84",
           piexif.GPSIFD.GPSHPositioningError: _rat(xy_std, 100)}
    data = piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gps, "1st": {}, "thumbnail": None})
    raw = path.read_bytes()
    tmp = path.with_suffix(".tmp")
    piexif.insert(data, raw, str(tmp))
    tagged = _insert_xmp(tmp.read_bytes(), _xmp(rec, xy_std, z_std))
    tmp.unlink()
    path.write_bytes(tagged)
