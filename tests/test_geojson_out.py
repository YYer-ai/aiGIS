# tests/test_geojson_out.py
import json
from aigis.geojson_out import rows_to_geojson

PT = '{"type":"Point","coordinates":[116.4,39.9]}'

def test_with_geometry_column():
    fc = rows_to_geojson(["name", "geometry"], [("故宫", PT)])
    assert fc["type"] == "FeatureCollection"
    f = fc["features"][0]
    assert f["geometry"]["type"] == "Point"
    assert f["properties"] == {"name": "故宫"}

def test_geometry_as_dict():
    fc = rows_to_geojson(["geometry"], [(json.loads(PT),)])
    assert fc["features"][0]["geometry"]["type"] == "Point"

def test_without_geometry():
    fc = rows_to_geojson(["count"], [(5,)])
    assert fc["features"][0]["geometry"] is None
    assert fc["features"][0]["properties"] == {"count": 5}
