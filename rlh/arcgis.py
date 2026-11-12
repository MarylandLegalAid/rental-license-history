"""Fetch every feature of an ArcGIS MapServer layer as flat, normalized rows."""

import datetime as dt
import json
import time
import urllib.parse
import urllib.request

PAGE = 2000
USER_AGENT = "rental-license-history/1.0"
# Fields that are internal to the GIS service and may churn on republish.
DROP_FIELDS = {"OBJECTID", "SHAPE", "Shape", "SHAPE.STArea()", "SHAPE.STLength()"}


def _get(url, params, retries=4):
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.load(r)
            if "error" in data:
                raise RuntimeError(f"ArcGIS error: {data['error']}")
            return data
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * 2**attempt)


def layer_fields(base_url, layer_id):
    """Return [(name, esri_type)] for a layer, in service order."""
    info = _get(f"{base_url}/{layer_id}", {"f": "json"})
    return [(f["name"], f["type"]) for f in info["fields"]]


def _normalize(value, esri_type):
    if value is None:
        return ""
    if esri_type == "esriFieldTypeDate":
        # Epoch milliseconds; the county stores dates at midnight UTC.
        return dt.datetime.fromtimestamp(value / 1000, dt.UTC).date().isoformat()
    if isinstance(value, float):
        return repr(round(value, 6))
    return str(value).strip()


def fetch_layer(base_url, layer_id):
    """Return (field_names, rows). Rows are dicts of strings; LON/LAT come from geometry."""
    fields = [(n, t) for n, t in layer_fields(base_url, layer_id) if n not in DROP_FIELDS]
    types = dict(fields)
    expected = _get(f"{base_url}/{layer_id}/query",
                    {"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
    rows, offset = [], 0
    while True:
        page = _get(f"{base_url}/{layer_id}/query", {
            "where": "1=1", "outFields": "*", "returnGeometry": "true", "outSR": 4326,
            "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": PAGE,
            "f": "json",
        })
        feats = page.get("features", [])
        for f in feats:
            a = f["attributes"]
            row = {n: _normalize(a.get(n), types[n]) for n, _ in fields}
            g = f.get("geometry") or {}
            row["LON"] = _normalize(g.get("x"), "double")
            row["LAT"] = _normalize(g.get("y"), "double")
            rows.append(row)
        offset += len(feats)
        if not feats or (len(feats) < PAGE and not page.get("exceededTransferLimit")):
            break
    if len(rows) != expected:
        raise RuntimeError(f"layer {layer_id}: fetched {len(rows)} rows but service reports {expected}")
    return [n for n, _ in fields] + ["LON", "LAT"], rows
