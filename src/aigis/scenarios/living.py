# src/aigis/scenarios/living.py
"""场景：居住选址（街区生活便利度评估）。

候选 = 乡镇/街道面（boundaries admin_level 7/8），对每个街道质心统计
1.5km 半径内四类设施数量（购物/医疗/教育/公园），Python 端分档加权评分，
用户偏好维度加权放大。输出 top 街区点 + 因子明细 + 总分渐变图层。

注意：OSM POI 密度受采集完备度影响，评分是相对排序参考而非绝对结论，
总结中明示。
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

KEYWORDS = [
    re.compile(r"搬家|租房|买房|定居|安家|住哪|居住.{0,6}(选|找|推荐)|宜居|生活便利"),
]

_FACTORS = ("shopping", "medical", "education", "leisure")
_FACTOR_ZH = {"shopping": "购物", "medical": "医疗", "education": "教育",
              "leisure": "休闲"}
_RADIUS_M = 1500          # 因子统计半径（质心）
_TOP_N = 12               # 输出街区数


# ---------- 参数抽取 ----------

_EXTRACT_SYSTEM = """你是居住选址需求分析助手，从用户需求抽取参数。
只输出一个 JSON 对象（不要多余文本）：
{"region_name": null, "region_kind": "none", "priorities": ["shopping"]}
规则：
- region_kind："ring"（二环~六环范围）/"place"（具体地名如海淀区、国贸）/
  "none"（未提范围）；region_name 为对应名称，"none" 时为 null。
  城市本身（如"北京"）不算范围，用 "none"
- priorities：重视的维度数组，元素限 shopping（买菜购物/超市）/
  medical（医院药店）/education（学校幼儿园）/leisure（公园休闲）；
  可多选；未明说输出全部四个
- 结合对话上下文消解指代"""


def normalize_params(data: dict | None) -> dict:
    if not isinstance(data, dict):
        data = {}
    region_kind = data.get("region_kind") if data.get("region_kind") in ("ring", "place") else "none"
    region_name = str(data.get("region_name") or "").strip()
    if region_kind == "place" and any(c in region_name for c in _CITY_NAMES):
        region_kind = "none"
    if region_kind == "place" and not region_name:
        region_kind = "none"
    raw = data.get("priorities")
    priorities = [p for p in (raw if isinstance(raw, list) else []) if p in _FACTORS]
    return {"region_kind": region_kind, "region_name": region_name,
            "priorities": priorities or list(_FACTORS)}


# ---------- 检索与评分 ----------

def fetch_hoods(cur, cx: float, cy: float, radius: int) -> list[dict]:
    """街道面 + 四因子计数（质心 1.5km 半径；两段式距离走索引）。"""
    deg = _RADIUS_M / 85000
    cur.execute(
        "WITH c AS (SELECT name, ST_Centroid(geom) AS ctr FROM osm_boundaries "
        "  WHERE admin_level IN (7, 8) AND name IS NOT NULL), "
        "pk AS (SELECT ST_Centroid(geom) AS g FROM osm_areas WHERE leisure = 'park') "
        "SELECT c.name, ST_AsGeoJSON(c.ctr), "
        "(SELECT count(*) FROM osm_pois p "
        " WHERE p.shop IN ('supermarket','marketplace','convenience','mall') "
        " AND ST_DWithin(p.geom, c.ctr, %s) "
        " AND ST_DistanceSphere(p.geom, c.ctr) <= %s) AS shopping, "
        "(SELECT count(*) FROM osm_pois p "
        " WHERE p.amenity IN ('hospital','clinic','pharmacy') "
        " AND ST_DWithin(p.geom, c.ctr, %s) "
        " AND ST_DistanceSphere(p.geom, c.ctr) <= %s) AS medical, "
        "(SELECT count(*) FROM osm_pois p "
        " WHERE p.amenity IN ('school','kindergarten') "
        " AND ST_DWithin(p.geom, c.ctr, %s) "
        " AND ST_DistanceSphere(p.geom, c.ctr) <= %s) AS education, "
        "(SELECT count(*) FROM pk "
        " WHERE ST_DWithin(g, c.ctr, %s) "
        " AND ST_DistanceSphere(g, c.ctr) <= %s) AS leisure "
        "FROM c "
        "WHERE ST_DWithin(c.ctr, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
        "AND ST_DistanceSphere(c.ctr, "
        "ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s",
        (deg, _RADIUS_M, deg, _RADIUS_M, deg, _RADIUS_M, deg, _RADIUS_M,
         cx, cy, radius / 85000, cx, cy, radius))
    out = []
    for name, geometry, shop, med, edu, park in cur.fetchall():
        try:
            g = json.loads(geometry) if isinstance(geometry, str) else geometry
            lng, lat = g["coordinates"][:2]
        except (TypeError, KeyError, ValueError):
            continue
        out.append({"name": name, "lng": float(lng), "lat": float(lat),
                    "shopping": shop, "medical": med, "education": edu,
                    "leisure": park})
    return out


def _band(v: int, low: int, mid: int) -> float:
    """计数分档：0→0.1 / <low→0.4 / <mid→0.7 / ≥mid→1.0。"""
    if v <= 0:
        return 0.1
    if v < low:
        return 0.4
    if v < mid:
        return 0.7
    return 1.0


def score_hood(h: dict, priorities: list[str]) -> float:
    """四因子分档加权 100 分制：重视维度权重 1.0，其余 0.3。

    档位阈值按北京城区街道实际密度校准（低档也能满分的区分度问题）。
    """
    bands = {"shopping": _band(h["shopping"], 10, 40),
             "medical": _band(h["medical"], 5, 15),
             "education": _band(h["education"], 10, 40),
             "leisure": _band(h["leisure"], 3, 8)}
    w = {f: (1.0 if f in priorities else 0.3) for f in _FACTORS}
    total_w = sum(w.values())
    return round(100 * sum(bands[f] * w[f] for f in _FACTORS) / total_w, 1)


# ---------- 输出 ----------

def build_output(hoods: list[dict], params: dict) -> tuple[dict, list[dict], str]:
    pri_zh = "、".join(_FACTOR_ZH[f] for f in params["priorities"])
    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [h["lng"], h["lat"]]},
              "properties": {"name": h["name"], "score": h["score"],
                             **{f: h[f] for f in _FACTORS}}}
             for h in hoods]
    geojson = {"type": "FeatureCollection", "features": feats}
    cards = [{"type": "living",
              "title": f"宜居街区 TOP{len(hoods)} · 侧重{pri_zh}",
              "hoods": [{"name": h["name"], "score": h["score"],
                         "shopping": h["shopping"], "medical": h["medical"],
                         "education": h["education"], "leisure": h["leisure"]}
                        for h in hoods]}]
    return geojson, cards, cards[0]["title"]


_SUMMARY_SYSTEM = ("你是居住选址顾问。基于给定的街区便利度排名（购物/医疗/教育/休闲 "
                   "1.5km 半径计数与总分），用中文写不超过 3 句话：点评总分最高的 1-2 "
                   "个街区的优势维度；末句说明数据来自 OpenStreetMap 设施数量，"
                   "仅作相对参考，实地考察为准。严格使用给定信息，不编造。")


def _fallback_answer(hoods: list[dict]) -> str:
    if not hoods:
        return "该范围内没有可评估的街区，请扩大范围再试。"
    h = hoods[0]
    return (f"总分最高的是「{h['name']}」（{h['score']} 分）。"
            "设施数量来自 OpenStreetMap，仅作相对参考，实地考察为准。")


def _summarize(question: str, hoods: list[dict], params: dict,
               provider, on_delta) -> str:
    rows = "\n".join(
        f"{h['name']}（总分{h['score']}：购物{h['shopping']} 医疗{h['medical']} "
        f"教育{h['education']} 公园{h['leisure']}）" for h in hoods[:10])
    user = (f"用户需求：{question}\n"
            f"侧重维度：{('、'.join(_FACTOR_ZH[f] for f in params['priorities']))}\n"
            f"街区排名（半径1.5km内设施数）：\n{rows}")
    try:
        if on_delta is None:
            return provider.generate(_SUMMARY_SYSTEM, user).strip()
        parts = []
        for d in provider.generate_stream(_SUMMARY_SYSTEM, user):
            parts.append(d)
            on_delta(d)
        return "".join(parts).strip()
    except Exception:
        return _fallback_answer(hoods)


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
        on_status("评估街区便利度")
    try:
        cx, cy, radius = resolve_disc(
            {"region_kind": params["region_kind"],
             "region_name": params["region_name"]}, cfg)
        with readonly_conn(cfg) as conn, conn.cursor() as cur:
            hoods = fetch_hoods(cur, cx, cy, radius)
    except psycopg.Error as e:
        return ScenarioOutcome(ok=False, error=str(db_error(e)))

    if on_status:
        on_status("评分排序")
    if not hoods:
        return ScenarioOutcome(
            ok=False, params=params,
            error="该范围内没有可评估的街区，请扩大范围（如不限定环路）再试")
    for h in hoods:
        h["score"] = score_hood(h, params["priorities"])
    top = sorted(hoods, key=lambda h: (-h["score"], h["name"]))[:_TOP_N]

    if on_status:
        on_status("总结中")
    answer = _summarize(question, top, params, provider, on_delta)
    geojson, cards, title = build_output(top, params)
    return ScenarioOutcome(
        ok=True, scenario_type="living", title=title, cards=cards,
        geojson=geojson, answer=answer, params=params,
        layer_style={"gradient": {"column": "score"}, "label": {"column": "name"}},
        row_count=len(top))
