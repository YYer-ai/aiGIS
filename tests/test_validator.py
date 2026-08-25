# tests/test_validator.py
import pytest

from aigis.validator import validate

TABLES = {"osm_pois", "osm_roads", "osm_areas", "osm_boundaries", "ring_areas"}

GOOD = [
    "SELECT name FROM osm_pois WHERE amenity='restaurant' LIMIT 5",
    "WITH t AS (SELECT * FROM osm_areas) SELECT count(*) FROM t",
    "SELECT name FROM osm_pois UNION SELECT name FROM osm_areas",
    "SELECT name, ST_AsGeoJSON(geom) AS geometry FROM osm_roads WHERE name LIKE '%长安街%'",
]
BAD = [
    ("DELETE FROM osm_pois", "非查询"),
    ("DROP TABLE osm_pois", "非查询"),
    ("INSERT INTO osm_pois VALUES (1)", "非查询"),
    ("UPDATE osm_areas SET name='x'", "非查询"),
    ("SELECT pg_sleep(10)", "函数"),
    ("SELECT pg_read_file('x')", "函数"),
    ("SELECT 1; SELECT 2", "单条"),
    ("SELECT * FROM pg_tables", "表白"),
    ("SELECT * FROM information_schema.columns", "表白"),
    ("COPY osm_pois TO 'x'", "非查询"),
    ("SELECT lo_import('x')", "函数"),
    ("SELECT * FROM nonexistent_table", "表白"),
    ("不是SQL", "解析"),
]


@pytest.mark.parametrize("sql", GOOD)
def test_good_sql_passes(sql):
    ok, reason = validate(sql, TABLES)
    assert ok, reason


@pytest.mark.parametrize("sql,expect", BAD)
def test_bad_sql_rejected(sql, expect):
    ok, reason = validate(sql, TABLES)
    assert not ok and expect in reason
