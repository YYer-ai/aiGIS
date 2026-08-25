"""环路多边形预处理：osm_roads 环路线 → ring_areas 环内面。

方案（依据 osm_roads 实际数据调试，139,265 行）：
1. 线集收集：名称前缀正则 ^[东西南北]?N环（匹配「三环/三环路/六环路/北三环中路/东六环路」），
   排除噪音词（六环路绿道/东六环隧道/六环路门头沟出口/南二环匝道/四环胡同等），
   highway 放宽至 tertiary（三环主分路段「北三环中路」等 501 条为 tertiary），
   六环补入 ref=G4501（约 14.5km 无 name 段）。
2. 成环：OSM 分段间存在 55m~3km 缝隙，ST_LineMerge/ST_Polygonize 返回 0 面（分叉致 IsClosed=false），
   改为分级容差 buffer 成带 → 取最大连通部分 → 取最大内洞（即环内区域）；
   从最小容差逐级尝试，取首个内洞面积 >= MIN_AREA_KM2 的容差，使边界收缩偏差最小。
   面积相对公开值的偏差：三环 -4%、六环 -6%（容差+半车道宽的内偏），对 POI 级查询可接受。
3. 未成环的环跳过并提示（LLM 层走 ST_DWithin 近似）。
"""
import psycopg

RING_DEFS = {
    "二环": {"level": 2, "refs": ()},
    "三环": {"level": 3, "refs": ()},
    "四环": {"level": 4, "refs": ()},
    "五环": {"level": 5, "refs": ()},
    "六环": {"level": 6, "refs": ("G4501",)},
}
# 噪音词来自实际取样（六环路绿道/东六环隧道/六环路门头沟出口/东六环西侧路/南二环匝道等）
NAME_NOISE = "(绿道|出口|隧道|联络线|匝道|西侧路|东侧路|训练场|胡同|公园)"
# 容差梯度（度）：33m/66m/130m/330m/880m，实测二环 66m、三环/四环 130m、五环 330m、六环 880m 成环
TOLERANCES = (0.0003, 0.0006, 0.0012, 0.003, 0.008)
MIN_AREA_KM2 = 20.0  # 低于此面积的洞视为立交小圈/碎片，不算成环


def build_ring_sql(ring_keyword: str) -> str:
    """收集某环全部线段的 SQL（单行 ST_Collect，供 TEMP 表 t_ring 使用）。"""
    refs = RING_DEFS[ring_keyword]["refs"]
    ref_clause = f" OR ref IN ({','.join(repr(r) for r in refs)})" if refs else ""
    return (
        "SELECT ST_Collect(geom) AS g FROM osm_roads "
        f"WHERE (name ~ '^[东西南北]?{ring_keyword}' "
        f"AND name !~ '{NAME_NOISE}' "
        f"AND highway IN ('motorway','trunk','primary','secondary','tertiary'){ref_clause})"
    )


# 从 t_ring 提取最大内洞：参数为 buffer 容差（度）。
# 注：本机 PostGIS 的 ST_DumpRings 返回 POLYGON（非文档的 LINESTRING），洞 geom 可直接使用。
HOLE_SQL = """
WITH buf AS (
  SELECT ST_Buffer(ST_UnaryUnion(g), %s, 'quad_segs=2') AS b FROM t_ring
), parts AS (
  SELECT (ST_Dump(b)).geom AS pg FROM buf
), big AS (
  SELECT pg FROM parts ORDER BY ST_Area(pg::geography) DESC LIMIT 1
), rings AS (
  SELECT path[1] AS idx, geom FROM (SELECT (ST_DumpRings(pg)).* FROM big) d
)
SELECT geom AS hole, ST_Area(geom::geography)/1e6 AS area_km2
FROM rings WHERE idx > 0 ORDER BY area_km2 DESC LIMIT 1
"""

DROP_SQL = "DROP TABLE IF EXISTS ring_areas;"
CREATE_SQL = """CREATE TABLE ring_areas(
  ring_name text, ring_level int,
  geom geometry(MultiPolygon,4326) NOT NULL);
COMMENT ON TABLE ring_areas IS '环路环内面：查询"N环内"用 ST_Contains(ring_areas.geom, x.geom)，ring_level 2-6';
CREATE INDEX ON ring_areas USING GIST(geom);"""

INSERT_SQL = """INSERT INTO ring_areas(ring_name, ring_level, geom)
VALUES (%s, %s, ST_Multi(ST_GeomFromEWKB(decode(%s, 'hex'))))"""


def main(conn_info: str):
    with psycopg.connect(conn_info, autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute(DROP_SQL)
        cur.execute(CREATE_SQL)
        for ring, cfg in RING_DEFS.items():
            cur.execute("DROP TABLE IF EXISTS t_ring;")
            cur.execute(f"CREATE TEMP TABLE t_ring AS {build_ring_sql(ring)}")
            done = False
            for tol in TOLERANCES:
                cur.execute(HOLE_SQL, (tol,))
                row = cur.fetchone()
                if row and row[1] and row[1] >= MIN_AREA_KM2:
                    hole_hex, area_km2 = row
                    cur.execute(INSERT_SQL, (ring, cfg["level"], hole_hex))
                    print(f"{ring}: OK, 容差={tol}, 面积(km²)={area_km2:.1f}")
                    done = True
                    break
            if not done:
                print(f"{ring}: 未闭合或未找到，跳过（LLM 将走 ST_DWithin 近似）")
            cur.execute("DROP TABLE IF EXISTS t_ring;")
        cur.execute("GRANT SELECT ON ring_areas TO aigis_readonly;")


if __name__ == "__main__":
    main("host=localhost port=5432 dbname=aigis user=aigis password=aigis_dev_2026")
