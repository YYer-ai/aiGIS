# tests/test_validator.py
import pytest

from aigis.validator import validate

TABLES = {"osm_pois", "osm_roads", "osm_areas", "osm_boundaries", "ring_areas"}

GOOD = [
    "SELECT name FROM osm_pois WHERE amenity='restaurant' LIMIT 5",
    "WITH t AS (SELECT * FROM osm_areas) SELECT count(*) FROM t",
    "SELECT name FROM osm_pois UNION SELECT name FROM osm_areas",
    "SELECT name, ST_AsGeoJSON(geom) AS geometry FROM osm_roads WHERE name LIKE '%长安街%'",
    # F4: PG unquoted 标识符折叠小写，大写裸表名语义等价，应放行
    "SELECT * FROM OSM_POIS LIMIT 3",
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
    # F1 回归：引号包裹的危险函数名（sqlglot 解析为 Identifier）不得绕过黑名单
    ("SELECT \"pg_read_file\"('/etc/passwd')", "函数"),
    ("SELECT \"pg_sleep\"(1)", "函数"),
    # F2 回归：会话层 GUC/锁/序列类危险函数
    ("SELECT set_config('statement_timeout','0',false)", "函数"),
    ("SELECT pg_advisory_lock(1)", "函数"),
    ("SELECT setval('ring_areas_id_seq',1)", "函数"),
    # 形态保持：大小写与 schema 前缀的函数名归一后仍命中黑名单
    ("SELECT PG_SLEEP(10)", "函数"),
    ("SELECT pg_catalog.pg_read_file('x')", "函数"),
    # 最终审查修复波：xml 导出函数族（字符串参数里的查询绕过表白名单）
    ("SELECT query_to_xml('SELECT * FROM osm_pois', true, true, '')", "函数"),
    ("SELECT table_to_xml_and_xmlschema('osm_pois', true, true, '')", "函数"),
    # 复审补充：database_to_xml 同族遗漏（整库导出，无需表名即可绕过表白名单）
    ("SELECT database_to_xml_and_xmlschema(true, true, '')", "函数"),
    # 最终审查修复波：数据修改 CTE（顶层 SELECT + CTE 内写操作）不得穿透
    ("WITH t AS (INSERT INTO osm_pois VALUES (1) RETURNING *) SELECT * FROM t", "数据修改"),
    ("WITH t AS (UPDATE osm_pois SET name='x' RETURNING *) SELECT * FROM t", "数据修改"),
    ("WITH t AS (DELETE FROM osm_pois RETURNING *) SELECT * FROM t", "数据修改"),
    # 最终审查修复波：advisory 锁同族
    ("SELECT pg_advisory_xact_lock(42)", "函数"),
]


@pytest.mark.parametrize("sql", GOOD)
def test_good_sql_passes(sql):
    ok, reason = validate(sql, TABLES)
    assert ok, reason


@pytest.mark.parametrize("sql,expect", BAD)
def test_bad_sql_rejected(sql, expect):
    ok, reason = validate(sql, TABLES)
    assert not ok and expect in reason
