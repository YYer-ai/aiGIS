# src/aigis/scenarios/camping.py
"""场景：露营选址。

两类结果合并输出：
1. 已开发营地（tourism=camp_site/caravan_site/picnic_site，命名者优先）；
2. 野营候选区（wood/scrub 林地面按面积取前若干，计算三因子：
   距水域 / 距主干道（噪声）/ 距步道（可达性），Python 端加权评分排序）。

提醒边界：数据只有 OSM 静态标签，无法判断合法性/开放状态/设施实时情况，
总结中明示"以实地与官方信息为准"。距离均为直线距离。
"""
import json
import re
from collections.abc import Callable

import psycopg

from aigis.config import Config
from aigis.scenarios import ScenarioOutcome
from aigis.scenarios.db import db_error, readonly_conn
from aigis.scenarios.extract import extract_json
from aigis.scenarios.trip import _CITY_NAMES, resolve_disc

# 触发正则（高置信）
KEYWORDS = [
    re.compile(r"露营|扎营|野营|营地|帐篷"),
    re.compile(r"camping|camp\s*site", re.IGNORECASE),
]

_WILD_LIMIT = 120          # 野营候选面数（按面积降序）
_WILD_MIN_AREA_M2 = 10000  # 太小的林地不便扎营
_FACTOR_RADIUS_M = 2000    # 因子检索半径（超出记 None）
# 因子粗筛半径（度）：米 / 85000（北京纬度经向最坏换算），保证度框覆盖米圆；
# 精确距离由 ST_DistanceSphere 计算。ST_DWithin(geography) 不利用 geometry GiST
# 索引（LATERAL 内全表扫实测 20s+），故统一"度粗筛走索引 + 球面精算"两段式
_FACTOR_DEG = _FACTOR_RADIUS_M / 85000


# ---------- 参数抽取 ----------

_EXTRACT_SYSTEM = """你是露营选址需求分析助手，从用户需求抽取参数。
只输出一个 JSON 对象（不要多余文本）：
{"region_name": null, "region_kind": "none", "near_water": false, "prefer": "auto"}
规则：
- region_kind："ring"（二环~六环范围）/"place"（具体地名如海淀区、温榆河附近）/
  "none"（未提范围）；region_name 为对应名称，"none" 时为 null。
  城市本身（如"北京"）不算范围，用 "none"——数据只覆盖北京城区
- near_water：用户明确想靠近水源（河边/湖边露营）时 true，否则 false
- prefer："camp"（明确要去设施完备的营地）/"wild"（明确想野营/野外扎营）/
  "auto"（未明说，两类都给）
- 结合对话上下文消解指代"""


def normalize_params(data: dict | None) -> dict:
    if not isinstance(data, dict):
        data = {}
    region_kind = data.get("region_kind") if data.get("region_kind") in ("ring", "place") else "none"
    region_name = str(data.get("region_name") or "").strip()
    if region_kind == "place" and any(c in region_name for c in _CITY_NAMES):
        region_kind = "none"  # 城市名不缩小范围（同 trip）
    if region_kind == "place" and not region_name:
        region_kind = "none"
    prefer = data.get("prefer") if data.get("prefer") in ("camp", "wild") else "auto"
    return {"region_kind": region_kind, "region_name": region_name,
            "near_water": bool(data.get("near_water")), "prefer": prefer}


# ---------- 因子查询 ----------

def _row_site(name, kind, geometry, water_m, road_m, trail_m, area_m2) -> dict | None:
    try:
        g = json.loads(geometry) if isinstance(geometry, str) else geometry
        lng, lat = g["coordinates"][:2]
    except (TypeError, KeyError, ValueError):
        return None
    return {"name": name or "（未命名）", "kind": kind, "lng": float(lng), "lat": float(lat),
            "water_m": round(water_m) if water_m is not None else None,
            "road_m": round(road_m) if road_m is not None else None,
            "trail_m": round(trail_m) if trail_m is not None else None,
            "area_m2": round(area_m2) if area_m2 else None}


def fetch_camps(cur, cx: float, cy: float, radius: int) -> list[dict]:
    """已开发营地 + 三因子（距水/主干道/步道）。

    距离两段式：ST_DWithin(geometry, 度) 粗筛走 GiST + ST_DistanceSphere 精算（米）。
    """
    deg = radius / 85000
    cur.execute(
        "SELECT p.name, '营地' AS kind, ST_AsGeoJSON(p.geom) AS geometry, w.d, r.d, t.d, "
        "ST_Area(NULL::geography) AS area_m2 "
        "FROM osm_pois p "
        "LEFT JOIN LATERAL (SELECT min(ST_DistanceSphere(p.geom, a.geom)) AS d "
        "  FROM osm_areas a WHERE a.\"natural\" = 'water' "
        "  AND ST_DWithin(a.geom, p.geom, %s)) w ON true "
        "LEFT JOIN LATERAL (SELECT min(ST_DistanceSphere(p.geom, g.geom)) AS d "
        "  FROM osm_roads g WHERE g.highway IN ('motorway','trunk','primary','secondary') "
        "  AND ST_DWithin(g.geom, p.geom, %s)) r ON true "
        "LEFT JOIN LATERAL (SELECT min(ST_DistanceSphere(p.geom, h.geom)) AS d "
        "  FROM osm_roads h WHERE h.highway IN ('path','footway','cycleway') "
        "  AND ST_DWithin(h.geom, p.geom, %s)) t ON true "
        "WHERE ST_DWithin(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
        "AND ST_DistanceSphere(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
        "AND p.tourism IN ('camp_site','caravan_site','picnic_site') "
        "ORDER BY ((p.name IS NULL) OR length(p.name) = 0), p.tourism, p.osm_id LIMIT 30",
        (_FACTOR_DEG, _FACTOR_DEG, _FACTOR_DEG,
         cx, cy, deg, cx, cy, radius))
    return [s for row in cur.fetchall()
            if (s := _row_site(*row))]


def fetch_wild_areas(cur, cx: float, cy: float, radius: int) -> list[dict]:
    """野营候选林地面（范围内面积 top）+ 三因子 + 面积（两段式距离，同上）。"""
    deg = radius / 85000
    cur.execute(
        "SELECT n.name, '野营点' AS kind, ST_AsGeoJSON(ST_Centroid(n.geom)) AS geometry, "
        "w.d, r.d, t.d, ST_Area(n.geom::geography) AS area_m2 "
        "FROM (SELECT * FROM osm_areas "
        "      WHERE \"natural\" IN ('wood','scrub') AND name IS NOT NULL "
        f"      AND ST_Area(geom::geography) >= {_WILD_MIN_AREA_M2} "
        "      AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
        "      AND ST_DistanceSphere(ST_Centroid(geom), "
        "          ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
        f"      ORDER BY ST_Area(geom::geography) DESC LIMIT {_WILD_LIMIT}) n "
        "LEFT JOIN LATERAL (SELECT min(ST_DistanceSphere(n.geom, a.geom)) AS d "
        "  FROM osm_areas a WHERE a.\"natural\" = 'water' "
        "  AND ST_DWithin(a.geom, n.geom, %s)) w ON true "
        "LEFT JOIN LATERAL (SELECT min(ST_DistanceSphere(n.geom, g.geom)) AS d "
        "  FROM osm_roads g WHERE g.highway IN ('motorway','trunk','primary','secondary') "
        "  AND ST_DWithin(g.geom, n.geom, %s)) r ON true "
        "LEFT JOIN LATERAL (SELECT min(ST_DistanceSphere(n.geom, h.geom)) AS d "
        "  FROM osm_roads h WHERE h.highway IN ('path','footway','cycleway') "
        "  AND ST_DWithin(h.geom, n.geom, %s)) t ON true "
        "ORDER BY n.name",
        (cx, cy, deg, cx, cy, radius,
         _FACTOR_DEG, _FACTOR_DEG, _FACTOR_DEG))
    return [s for row in cur.fetchall()
            if (s := _row_site(*row))]


# ---------- 评分 ----------

def _band(v: float | None, good: float, mid: float, ok: float,
          miss: float = 0.1) -> float:
    """分档得分：v<=good→1 / <=mid→0.7 / <=ok→0.4 / None 或更远→miss。"""
    if v is None:
        return miss
    if v <= good:
        return 1.0
    if v <= mid:
        return 0.7
    if v <= ok:
        return 0.4
    return miss


def _quiet(road_m: float | None) -> float:
    """距主干道越远越安静：None（2km 内无主干道）最优。"""
    if road_m is None:
        return 1.0
    if road_m <= 100:
        return 0.1
    if road_m <= 300:
        return 0.4
    if road_m <= 500:
        return 0.7
    return 1.0


def score_site(site: dict, near_water: bool) -> float:
    """野营点加权评分（0-100）：水源 40 + 静谧 30 + 步道可达 20 + 面积 10。"""
    water = _band(site["water_m"], 300, 800, 1500, 0.0)
    quiet = _quiet(site["road_m"])
    trail = _band(site["trail_m"], 300, 800, 1500)
    area = 0.5 if (site["area_m2"] or 0) >= 100000 else \
        (0.3 if (site["area_m2"] or 0) >= 30000 else 0.1)
    s = 40 * water + 30 * quiet + 20 * trail + 10 * area
    return round(s * (1.15 if near_water and water >= 0.7 else 1.0), 1)


# ---------- 卡片 / GeoJSON ----------

def build_output(sites: list[dict], params: dict) -> tuple[dict, list[dict], str]:
    """返回 (geojson, cards, 标题)。sites 已含 kind/score。"""
    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]},
              "properties": {"name": s["name"], "kind": s["kind"],
                             "score": s["score"], "water_m": s["water_m"],
                             "road_m": s["road_m"], "trail_m": s["trail_m"],
                             "area_m2": s["area_m2"]}} for s in sites]
    geojson = {"type": "FeatureCollection", "features": feats}
    cards = [{"type": "camping",
              "title": f"露营选址推荐 · {len(sites)} 处",
              "sites": [{k: s[k] for k in
                         ("name", "kind", "score", "water_m", "road_m", "trail_m", "area_m2")}
                        for s in sites]}]
    return geojson, cards, cards[0]["title"]


# ---------- 总结 ----------

_SUMMARY_SYSTEM = ("你是露营选址顾问。基于给定的候选点及因子（距水/距主干道/距步道/评分），"
                   "用中文写不超过 3 句话：推荐评分最高的 1-2 处并说明理由；"
                   "末句提醒：营地合法性、开放状态与设施请以实地和官方信息为准。"
                   "严格使用给定信息，不编造。")


def _fallback_answer(sites: list[dict]) -> str:
    if not sites:
        return "没有找到合适的露营候选点，请换个范围试试。"
    top = sites[0]
    return (f"共 {len(sites)} 处候选，评分最高的是「{top['name']}」"
            f"（{top['score']} 分）。合法性、开放状态与设施请以实地和官方信息为准。")


def _summarize(question: str, sites: list[dict], provider,
               on_delta: Callable[[str], None] | None) -> str:
    rows = "\n".join(
        f"{s['name']}（{s['kind']}，评分{s['score']}，距水{s['water_m']}m，"
        f"距主干道{s['road_m']}m，距步道{s['trail_m']}m）" for s in sites[:12])
    user = f"用户需求：{question}\n候选点：\n{rows}"
    try:
        if on_delta is None:
            return provider.generate(_SUMMARY_SYSTEM, user).strip()
        parts = []
        for d in provider.generate_stream(_SUMMARY_SYSTEM, user):
            parts.append(d)
            on_delta(d)
        return "".join(parts).strip()
    except Exception:
        return _fallback_answer(sites)


# ---------- 主流程 ----------

def run(question: str, cfg: Config, provider=None, history: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None) -> ScenarioOutcome:
    from aigis.llm import make_provider
    provider = provider or make_provider(cfg)

    if on_status:
        on_status("理解需求")
    ctx = f"对话上下文（供指代消解）：\n{history}\n\n" if history else ""
    params = normalize_params(extract_json(
        provider, _EXTRACT_SYSTEM, f"{ctx}用户需求：{question}", on_delta))

    if on_status:
        on_status("检索营地与林地")
    try:
        cx, cy, radius = resolve_disc(
            {"region_kind": params["region_kind"],
             "region_name": params["region_name"]}, cfg)
        with readonly_conn(cfg) as conn, conn.cursor() as cur:
            camps = fetch_camps(cur, cx, cy, radius) if params["prefer"] != "wild" else []
            wilds = fetch_wild_areas(cur, cx, cy, radius) if params["prefer"] != "camp" else []
    except psycopg.Error as e:
        return ScenarioOutcome(ok=False, error=str(db_error(e)))

    if on_status:
        on_status("选址评分")
    for s in camps:  # 营地：设施基准 60 + 水源/静谧/步道三因子；未命名折减 8 分
        s["score"] = round(60 + 15 * _band(s["water_m"], 500, 1000, 1500)
                           + 15 * _quiet(s["road_m"])
                           + 10 * _band(s["trail_m"], 300, 800, 1500)
                           - (8 if not s["name"] or s["name"] == "（未命名）" else 0), 1)
    for s in wilds:
        s["score"] = score_site(s, params["near_water"])
    # 营地/野营点各留配额再总分合并（避免一类满分霸榜）；并列时命名点优先
    named = lambda s: 0 if (s["name"] and s["name"] != "（未命名）") else 1  # noqa: E731
    camps_top = sorted(camps, key=lambda s: (named(s), -s["score"]))[:10]
    wilds_top = sorted(wilds, key=lambda s: (named(s), -s["score"]))[:8]
    sites = sorted(camps_top + wilds_top,
                   key=lambda s: (-s["score"], named(s)))[:15]

    if not sites:
        return ScenarioOutcome(
            ok=False, params=params,
            error="该范围内没有找到营地或适合扎营的林地，请扩大范围（如不限定环路）再试")

    if on_status:
        on_status("总结中")
    answer = _summarize(question, sites, provider, on_delta)
    geojson, cards, title = build_output(sites, params)
    return ScenarioOutcome(
        ok=True, scenario_type="camping", title=title, cards=cards,
        geojson=geojson, answer=answer, params=params,
        layer_style={"gradient": {"column": "score"}, "label": {"column": "name"}},
        row_count=len(sites))
