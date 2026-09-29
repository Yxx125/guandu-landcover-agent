"""Idempotently publish the 1990-2025 GeoTIFF layers to GeoServer."""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = os.getenv("GEOSERVER_INTERNAL_URL", "http://geoserver:8080/geoserver").rstrip("/")
USER = os.getenv("GEOSERVER_ADMIN_USER", "admin")
PASSWORD = os.getenv("GEOSERVER_ADMIN_PASSWORD", "geoserver")
RASTER_DIR = Path(os.getenv("GEOSERVER_RASTER_DIR", "/data/web_data"))
WORKSPACE = os.getenv("GEOSERVER_WORKSPACE", "guandu_clcd")
STORE = os.getenv("GEOSERVER_STORE", "guandu_web_data")

def request(path: str, method="GET", body=None):
    token = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    return urlopen(Request(BASE + path, data=body, headers=headers, method=method), timeout=30)

def wait_ready():
    for _ in range(60):
        try:
            with request("/rest/about/status"):
                return
        except (HTTPError, URLError, OSError):
            time.sleep(5)
    raise RuntimeError("GeoServer did not become ready within five minutes")

def post_if_missing(path, payload):
    try:
        with request(path, "POST", json.dumps(payload).encode()):
            return
    except HTTPError as exc:
        if exc.code != 409:
            raise

def main():
    wait_ready()
    # Rebuild the application workspace so stale failed stores cannot mask
    # missing layers on subsequent deployments.
    try:
        request(f"/rest/workspaces/{WORKSPACE}?recurse=true", "DELETE")
    except HTTPError as exc:
        if exc.code != 404:
            raise
    post_if_missing("/rest/workspaces", {"workspace": {"name": WORKSPACE}})
    # A workspace created through REST does not always get a namespace URI in
    # older GeoServer catalogs.  Register it explicitly so WMS can resolve
    # prefixed layer names (workspace:layer).
    try:
        request(f"/rest/namespaces/{WORKSPACE}", "PUT",
                json.dumps({"namespace": {"prefix": WORKSPACE,
                                             "uri": f"http://{WORKSPACE}"}}).encode())
    except HTTPError as exc:
        if exc.code not in (404, 405):
            raise
    rasters = sorted(
        p for p in RASTER_DIR.glob("guandu_*_3857.tif")
        if re.fullmatch(r"guandu_(19\d{2}|20(?:0\d|1\d|2[0-5]))_3857", p.stem)
    )
    if len(rasters) != 36:
        raise RuntimeError(f"expected 36 WebGIS rasters, found {len(rasters)}")
    for raster in rasters:
        name = raster.stem
        store = name
        post_if_missing(f"/rest/workspaces/{WORKSPACE}/coveragestores", {
            "coverageStore": {"name": store, "workspace": WORKSPACE,
                               "type": "GeoTIFF", "url": f"file:{raster}"}
        })
        post_if_missing(f"/rest/workspaces/{WORKSPACE}/coveragestores/{store}/coverages", {
            "coverage": {"name": name, "nativeName": name, "title": name}
        })
        # Make publication explicit.  This also repairs catalogs created by
        # older versions of the initializer where WMS omitted the layers.
        request(f"/rest/layers/{name}", "PUT", json.dumps({
            "layer": {"name": name, "enabled": True, "advertised": True}
        }).encode())
    # Refresh the in-memory WMS catalog after REST mutations.
    request("/rest/reload", "POST")
    print(f"GeoServer ready: {WORKSPACE}, {len(rasters)} layers")

if __name__ == "__main__":
    main()
