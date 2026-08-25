"""prep_rings 纯 SQL 文本断言（不依赖数据库）。

管线：osm_roads 名称过滤收集线集（build_ring_sql）
→ 分级容差 buffer 成带 + 取最大内洞（HOLE_SQL，OSM 分段缝隙下 ST_Polygonize 返回 0 面，故不用）。
"""
from prep_rings import HOLE_SQL, build_ring_sql


def test_ring_sql_contains_core_ops():
    sql = build_ring_sql("三环")
    for frag in ("osm_roads", "三环", "ST_Collect"):
        assert frag in sql


def test_ring_sql_filter_excludes_noise():
    """前缀匹配 + 噪音词排除 + tertiary 放宽（三环主段 highway=tertiary）。"""
    sql = build_ring_sql("三环")
    assert "name ~" in sql
    assert "绿道" in sql
    assert "tertiary" in sql


def test_liuhuan_includes_g4501():
    """六环约 14.5km 段无 name、仅有 ref=G4501，需按 ref 补入。"""
    sql = build_ring_sql("六环")
    assert "G4501" in sql


def test_hole_sql_contains_core_ops():
    """buffer 容差成带 → 最大连通部分 → 最大内洞，geography 面积 km²。"""
    for frag in ("ST_UnaryUnion", "ST_Buffer", "ST_DumpRings", "ST_Area", "::geography", "/1e6"):
        assert frag in HOLE_SQL
