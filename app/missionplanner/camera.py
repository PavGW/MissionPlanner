"""Camera profiles and photogrammetry geometry (GSD, footprint, spacing)."""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field


@dataclass
class Camera:
    name: str
    make: str
    model: str                 # EXIF model string (ODM uses make+model+size to group the camera)
    width_px: int
    height_px: int
    focal_mm: float            # real focal length
    focal35_mm: float          # 35 mm-equivalent (diagonal)
    shutter_s: float = 1 / 500  # conservative exposure time for blur limits
    notes: str = ""
    _derived: dict = field(default_factory=dict, repr=False)

    # --- derived geometry -------------------------------------------------------------------------
    @property
    def diag_px(self) -> float:
        return math.hypot(self.width_px, self.height_px)

    @property
    def crop_factor(self) -> float:
        return self.focal35_mm / self.focal_mm

    @property
    def sensor_w_mm(self) -> float:
        diag_mm = 43.2666 / self.crop_factor
        return diag_mm * self.width_px / self.diag_px

    @property
    def sensor_h_mm(self) -> float:
        return self.sensor_w_mm * self.height_px / self.width_px

    @property
    def pixel_mm(self) -> float:
        return self.sensor_w_mm / self.width_px

    @property
    def hfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.sensor_w_mm / (2 * self.focal_mm)))

    @property
    def vfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.sensor_h_mm / (2 * self.focal_mm)))

    def gsd_m(self, height_m: float) -> float:
        """Ground sample distance at nadir (m/px) for a flat ground at ``height_m`` below the camera."""
        return height_m * self.pixel_mm / self.focal_mm

    def height_for_gsd(self, gsd_m: float) -> float:
        return gsd_m * self.focal_mm / self.pixel_mm

    def footprint_m(self, height_m: float) -> tuple[float, float]:
        """(width, height) of the nadir image footprint on flat ground, metres (image long side = width)."""
        return (height_m * self.sensor_w_mm / self.focal_mm, height_m * self.sensor_h_mm / self.focal_mm)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_derived", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Camera":
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and not k.startswith("_")}
        return cls(**d)

    def summary(self) -> dict:
        return {"sensor_mm": f"{self.sensor_w_mm:.2f} x {self.sensor_h_mm:.2f}", "pixel_um": round(self.pixel_mm * 1000, 2),
                "hfov_deg": round(self.hfov_deg, 1), "vfov_deg": round(self.vfov_deg, 1),
                "gsd_cm_at_30m": round(self.gsd_m(30) * 100, 2)}


# Measured from test data (EXIF of JPG, ffmpeg probe of MP4). Video assumed to have the photo FOV.
NEO_VIDEO_4K = Camera(name="DJI Neo - 4K video", make="DJI", model="FC8671", width_px=3840, height_px=2160,
                      focal_mm=2.598, focal35_mm=14.0,
                      notes="Frames extracted from 4K/29.97 fps video. FOV assumed equal to the 16:9 photo.")
NEO_PHOTO_16x9 = Camera(name="DJI Neo - 12 MP photo 16:9", make="DJI", model="FC8671", width_px=4000, height_px=2256,
                        focal_mm=2.598, focal35_mm=14.0)

CAMERAS = {c.name: c for c in (NEO_VIDEO_4K, NEO_PHOTO_16x9)}
