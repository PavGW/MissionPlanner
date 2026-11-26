"""Survey project: folder layout, settings (project.json) and GeoPackage layers."""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import geopandas as gpd
import pyogrio

from .camera import CAMERAS, Camera, NEO_VIDEO_4K
from .frames import FrameParams
from .planner import PlanParams
from .track import TrackParams

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "projects"


def slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    return s or "survey"


class Project:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.name = self.root.name
        self.settings_path = self.root / "project.json"
        self.gpkg = self.root / f"{self.name}.gpkg"
        self.settings: dict = {}
        if self.settings_path.exists():
            self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))

    # --- lifecycle --------------------------------------------------------------------------------
    @classmethod
    def create(cls, name: str, root: str | Path = DEFAULT_ROOT) -> "Project":
        p = cls(Path(root) / slug(name))
        if p.settings_path.exists():
            raise FileExistsError(f"Project {p.name} already exists")
        for d in ("flights", "frames", "exports", "odm"):
            (p.root / d).mkdir(parents=True, exist_ok=True)
        p.settings = {"title": name, "epsg": None, "camera": NEO_VIDEO_4K.to_dict(),
                      "plan": PlanParams().to_dict(), "track": TrackParams().to_dict(),
                      "frames": FrameParams().to_dict(), "flights": {}, "runs": {}, "stats": {}}
        p.save()
        return p

    @staticmethod
    def list(root: str | Path = DEFAULT_ROOT) -> list[str]:
        root = Path(root)
        return sorted(d.name for d in root.iterdir() if (d / "project.json").exists()) if root.exists() else []

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings, indent=2, default=str), encoding="utf-8")

    # --- settings accessors -----------------------------------------------------------------------
    @property
    def camera(self) -> Camera:
        c = self.settings.get("camera")
        return Camera.from_dict(c) if c else NEO_VIDEO_4K

    def set_camera(self, name: str) -> None:
        self.settings["camera"] = CAMERAS[name].to_dict()

    @property
    def plan_params(self) -> PlanParams:
        return PlanParams.from_dict(self.settings.get("plan", {}))

    @property
    def track_params(self) -> TrackParams:
        return TrackParams.from_dict(self.settings.get("track", {}))

    @property
    def frame_params(self) -> FrameParams:
        return FrameParams.from_dict(self.settings.get("frames", {}))

    @property
    def epsg(self) -> int | None:
        return self.settings.get("epsg")

    # --- paths ------------------------------------------------------------------------------------
    def flight_dir(self, flight: str) -> Path:
        return self.root / "flights" / flight

    def frames_dir(self, flight: str) -> Path:
        return self.root / "frames" / flight

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def odm_dir(self) -> Path:
        return self.root / "odm"

    # --- GeoPackage -------------------------------------------------------------------------------
    def layers(self) -> list[str]:
        if not self.gpkg.exists():
            return []
        return [l[0] for l in pyogrio.list_layers(self.gpkg)]

    def write_layer(self, layer: str, gdf: gpd.GeoDataFrame) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if self.gpkg.exists() and layer in self.layers():
                pyogrio.write_dataframe(gdf, self.gpkg, layer=layer, driver="GPKG",
                                        layer_options={"OVERWRITE": "YES"})
            else:
                pyogrio.write_dataframe(gdf, self.gpkg, layer=layer, driver="GPKG", append=self.gpkg.exists())

    def read_layer(self, layer: str) -> gpd.GeoDataFrame | None:
        if layer not in self.layers():
            return None
        return pyogrio.read_dataframe(self.gpkg, layer=layer)

    def delete_layer(self, layer: str) -> None:
        """Remove a layer (table + GeoPackage metadata) using plain sqlite."""
        if layer not in self.layers():
            return
        import sqlite3
        con = sqlite3.connect(self.gpkg)
        try:
            con.execute(f'DROP TABLE IF EXISTS "{layer}"')
            con.execute(f'DROP TABLE IF EXISTS "rtree_{layer}_geom"')
            for t in ("gpkg_contents", "gpkg_geometry_columns", "gpkg_ogr_contents"):
                try:
                    con.execute(f"DELETE FROM {t} WHERE table_name = ?", (layer,))
                except sqlite3.OperationalError:
                    pass
            for t in ("gpkg_extensions",):
                try:
                    con.execute(f"DELETE FROM {t} WHERE table_name = ? OR table_name = ?", (layer, f"rtree_{layer}_geom"))
                except sqlite3.OperationalError:
                    pass
            con.commit()
        finally:
            con.close()
