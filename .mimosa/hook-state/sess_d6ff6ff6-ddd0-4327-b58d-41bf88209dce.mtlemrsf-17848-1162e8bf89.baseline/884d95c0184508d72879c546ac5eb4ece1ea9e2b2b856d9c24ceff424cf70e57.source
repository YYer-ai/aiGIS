"""prep_rings 纯 SQL 文本断言（不依赖数据库）。

管线：osm_roads 名称过滤收集线集（build_ring_sql，环名/噪音词/refs 参数化绑定）
→ 分级容差 buffer 成带 + 取最大内洞（HOLE_SQL，OSM 分段缝隙下 ST_Polygonize 返回 0 面，故不用）。
"""
import pytest
from prep_rings import HOLE_SQL, build_ring_sql


def test_ring_sql_contains_core_ops():
    sql, params = build_ring_sql("三环")
    for frag in ("osm_roads", "ST_Collect", "name ~"):
        assert frag in sql
    # 环名经参数绑定进入查询（防注入），而非内嵌 SQL 文本
    assert params[0] == "^[东西南北]?三环"
    assert "三环" not in sql


def test_ring_sql_filter_excludes_noise():
    """前缀匹配 + 噪音词排除 + tertiary 放宽（三环主段 highway=tertiary）。"""
    sql, params = build_ring_sql("三环")
    assert "绿道" in params[1]
    assert "tertiary" in sql


def test_ring_sql_binds_all_inputs():
    """SQL 文本只含 %s 占位符，环名/噪音词/refs 全部走参数绑定。"""
    sql, params = build_ring_sql("二环")
    assert sql.count("%s") == 3
    assert params[0] == "^[东西南北]?二环"
    assert "绿道" in params[1]
    assert params[2] == []


def test_ring_sql_rejects_unknown_ring():
    """ring_keyword 白名单守卫：非 RING_DEFS 键直接拒绝。"""
    with pytest.raises(ValueError):
        build_ring_sql("七环")


def test_liuhuan_includes_g4501():
    """六环约 14.5km 段无 name、仅有 ref=G4501，需按 ref 补入（参数化数组）。"""
    sql, params = build_ring_sql("六环")
    assert params[2] == ["G4501"]
    assert "ref = ANY(%s)" in sql


def test_hole_sql_contains_core_ops():
    """buffer 容差成带 → 最大连通部分 → 最大内洞，geography 面积 km²。"""
    for frag in ("ST_UnaryUnion", "ST_Buffer", "ST_DumpRings", "ST_Area", "::geography", "/1e6"):
        assert frag in HOLE_SQL
