"""Coordinate helpers: automatic UTM zone, WGS84 <-> UTM transforms."""
from __future__ import annotations

from functools import lru_cache

from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as _shp_transform


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the WGS84 / UTM zone containing (lon, lat). Handles the Norway/Svalbard exceptions."""
    zone = int((lon + 180) // 6) + 1
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    if 72 <= lat < 84:
        for z, (a, b) in {31: (0, 9), 33: (9, 21), 35: (21, 33), 37: (33, 42)}.items():
            if a <= lon < b:
                zone = z
    zone = max(1, min(60, zone))
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=16)
def _tf(src: int, dst: int) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def to_utm(epsg: int):
    """Transformer function (lon, lat) -> (x, y) for use with shapely or arrays."""
    return _tf(4326, epsg).transform


def to_wgs84(epsg: int):
    return _tf(epsg, 4326).transform


def geom_to(geom: BaseGeometry, src: int, dst: int) -> BaseGeometry:
    return _shp_transform(_tf(src, dst).transform, geom)
