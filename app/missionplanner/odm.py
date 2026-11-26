"""Minimal NodeODM REST client (https://github.com/OpenDroneMap/NodeODM/blob/master/docs/index.adoc).

Flow: POST /task/new/init -> POST /task/new/upload/{uuid} (files) -> POST /task/new/commit/{uuid}
      -> GET /task/{uuid}/info (poll) -> GET /task/{uuid}/download/all.zip
NodeODM runs in Docker:  docker run -d --name nodeodm -p 3000:3000 opendronemap/nodeodm"""
from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Callable

import requests

DEFAULT_URL = "http://localhost:3000"
STATUS = {10: "queued", 20: "running", 30: "failed", 40: "completed", 50: "canceled"}

# Sensible defaults for hand-flown Neo video frames (wide lens, GPS-only, small sets).
DEFAULT_OPTIONS = {
    "camera-lens": "brown",       # wide 106 deg lens: fit full Brown distortion
    "feature-quality": "high",
    "min-num-features": 10000,
    "orthophoto-resolution": 2.0,  # cm/px
    "dsm": True,
    "pc-quality": "medium",
    "auto-boundary": True,
    "fast-orthophoto": False,
}


class NodeODM:
    def __init__(self, url: str = DEFAULT_URL, token: str = "", timeout: float = 30):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _q(self) -> dict:
        return {"token": self.token} if self.token else {}

    def _get(self, path: str, **kw):
        r = requests.get(self.url + path, params=self._q(), timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    def _post(self, path: str, **kw):
        r = requests.post(self.url + path, params=self._q(), timeout=kw.pop("timeout", self.timeout), **kw)
        r.raise_for_status()
        js = r.json()
        if isinstance(js, dict) and js.get("error"):
            raise RuntimeError(f"NodeODM {path}: {js['error']}")
        return js

    # --- server -------------------------------------------------------------------------------
    def info(self) -> dict:
        return self._get("/info").json()

    def options(self) -> list[dict]:
        return self._get("/options").json()

    def alive(self) -> bool:
        try:
            self.info()
            return True
        except requests.RequestException:
            return False

    # --- tasks --------------------------------------------------------------------------------
    def create_task(self, images: list[Path], name: str, options: dict | None = None,
                    progress: Callable[[float, str], None] | None = None) -> str:
        opts = [{"name": k, "value": v} for k, v in (options or {}).items()]
        uuid = self._post("/task/new/init", data={"name": name, "options": json.dumps(opts)})["uuid"]
        for i, p in enumerate(images):
            with open(p, "rb") as fh:
                self._post(f"/task/new/upload/{uuid}", files={"images": (p.name, fh, "image/jpeg")}, timeout=300)
            if progress:
                progress((i + 1) / len(images), f"uploaded {i + 1}/{len(images)}")
        self._post(f"/task/new/commit/{uuid}")
        return uuid

    def task_info(self, uuid: str) -> dict:
        return self._get(f"/task/{uuid}/info").json()

    def task_output(self, uuid: str, line: int = 0) -> list[str]:
        r = requests.get(f"{self.url}/task/{uuid}/output", params={**self._q(), "line": line}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def cancel(self, uuid: str) -> None:
        self._post("/task/cancel", data={"uuid": uuid})

    def remove(self, uuid: str) -> None:
        self._post("/task/remove", data={"uuid": uuid})

    def wait(self, uuid: str, progress: Callable[[float, str], None] | None = None, poll_s: float = 10) -> dict:
        while True:
            inf = self.task_info(uuid)
            code = inf.get("status", {}).get("code")
            if progress:
                progress(inf.get("progress", 0) / 100, STATUS.get(code, str(code)))
            if code in (30, 40, 50):
                return inf
            time.sleep(poll_s)

    def download(self, uuid: str, out_dir: Path, extract: bool = True) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        zp = out_dir / "all.zip"
        with requests.get(f"{self.url}/task/{uuid}/download/all.zip", params=self._q(), stream=True,
                          timeout=600) as r:
            r.raise_for_status()
            with open(zp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        if extract:
            with zipfile.ZipFile(zp) as z:
                z.extractall(out_dir)
        return zp
