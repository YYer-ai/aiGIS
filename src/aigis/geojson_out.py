# src/aigis/geojson_out.py
import json
from typing import Any

def _parse_geom(v: Any):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return None
    return v if isinstance(v, dict) else None

def rows_to_geojson(columns: list[str], rows: list[tuple]) -> dict:
    feats = []
    geom_idx = columns.index("geometry") if "geometry" in columns else None
    prop_cols = [c for c in columns if c != "geometry"]
    for row in rows:
        geom = _parse_geom(row[geom_idx]) if geom_idx is not None else None
        props = dict(zip(prop_cols,
                         (v for i, v in enumerate(row) if i != geom_idx)))
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": feats}
