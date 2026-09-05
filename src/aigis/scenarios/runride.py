# src/aigis/scenarios/runride.py
"""场景：跑步/骑行绿道推荐。

三类要素：
1. 滨水绿道（线）：footway/path/cycleway 距有名水域 ≤130m 的线段按水域名聚合，
   输出 MultiLineString + 总里程（骑行偏好只取 cycleway）；
2. 大公园（点，跑步绕圈）：leisure=park 面积 top；
3. 田径场（点，塑胶跑道）：leisure=track。

距离口径：滨水邻近度粗筛（度）、里程 ST_Length(geography)（一次性汇总聚合，
实测 <1s）。绿道里程含少量非滨水段的误差，总结中明示为约数。
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
    re.compile(r"跑步|晨跑|夜跑|跑个?步|骑行|骑车|自行车道|绿道"),
    re.compile(r"runc?ing|cycling", re.IGNORECASE),
]

_TRAIL_MIN_KM = 3.0   # 聚合后低于此里程的水域不出榜
_PARK_MIN_AREA_M2 = 100000  # 跑步公园门槛（10 公顷）


# ---------- 参数抽取 ----------

_EXTRACT_SYSTEM = """你是运动出行需求分析助手，从用户需求抽取参数。
只输出一个 JSON 对象（不要多余文本）：
{"activity": "run", "region_name": null, "region_kind": "none"}
规则：
- activity："run"（跑步/散步/走跑道）/"ride"（骑行/骑车/自行车）；未明说默认 "run"
- region_kind："ring"（二环~六环范围）/"place"（具体地名如海淀区、温榆河）/
  "none"（未提范围）；region_name 为对应名称，"none" 时为 null。
  城市本身（如"北京"）不算范围，用 "none"
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
    activity = data.get("activity") if data.get("activity") in ("run", "ride") else "run"
    return {"activity": activity, "region_kind": region_kind,
            "region_name": region_name}


# ---------- 检索 ----------

def _trail_geometry(cur, ride: bool, cx: float, cy: float, radius: int) -> list[dict]:
    """滨水绿道聚合：按水域名 GROUP BY，返回 [{name, km, segs, geometry}]。
    ride 只取骑行道（highway 集合为代码常量双分支字面量，无用户输入）。"""
    if ride:
        cur.execute(
            "SELECT w.name, count(r.osm_id), "
            "sum(ST_Length(r.geom::geography)) / 1000 AS km, "
            "ST_AsGeoJSON(ST_Collect(ST_Simplify(r.geom, 0.00005))) AS geometry "
            "FROM osm_roads r "
            "JOIN LATERAL (SELECT a.name FROM osm_areas a "
            "  WHERE a.\"natural\" = 'water' AND a.name IS NOT NULL "
            "  AND length(a.name) > 1 AND ST_DWithin(a.geom, r.geom, 0.0015) "
            "  ORDER BY ST_Area(a.geom::geography) DESC LIMIT 1) w ON true "
            "WHERE r.highway IN ('cycleway') "
            "AND ST_DWithin(r.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
            "AND ST_DistanceSphere(r.geom, "
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
            "GROUP BY w.name "
            "HAVING sum(ST_Length(r.geom::geography)) / 1000 >= %s "
            "ORDER BY km DESC LIMIT 10",
            (cx, cy, radius / 85000, cx, cy, radius, _TRAIL_MIN_KM))
    else:
        cur.execute(
            "SELECT w.name, count(r.osm_id), "
            "sum(ST_Length(r.geom::geography)) / 1000 AS km, "
            "ST_AsGeoJSON(ST_Collect(ST_Simplify(r.geom, 0.00005))) AS geometry "
            "FROM osm_roads r "
            "JOIN LATERAL (SELECT a.name FROM osm_areas a "
            "  WHERE a.\"natural\" = 'water' AND a.name IS NOT NULL "
            "  AND length(a.name) > 1 AND ST_DWithin(a.geom, r.geom, 0.0015) "
            "  ORDER BY ST_Area(a.geom::geography) DESC LIMIT 1) w ON true "
            "WHERE r.highway IN ('footway', 'path', 'cycleway') "
            "AND ST_DWithin(r.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
            "AND ST_DistanceSphere(r.geom, "
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
            "GROUP BY w.name "
            "HAVING sum(ST_Length(r.geom::geography)) / 1000 >= %s "
            "ORDER BY km DESC LIMIT 10",
            (cx, cy, radius / 85000, cx, cy, radius, _TRAIL_MIN_KM))
    return [{"name": f"{row[0]}沿岸", "km": round(float(row[2]), 1),
             "segs": row[1], "geometry": row[3]} for row in cur.fetchall()]


def _park_points(cur, cx: float, cy: float, radius: int, limit: int = 8) -> list[dict]:
    """大公园（跑步绕圈去处）：面积 top，质心点 + 面积（公顷）。"""
    cur.execute(
        "SELECT p.name, ST_Area(p.geom::geography) / 10000 AS ha, "
        "ST_AsGeoJSON(ST_Centroid(p.geom)) AS geometry "
        "FROM osm_areas p "
        "WHERE p.leisure = 'park' AND p.name IS NOT NULL AND length(p.name) > 1 "
        "AND ST_Area(p.geom::geography) >= %s "
        "AND ST_DWithin(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
        "AND ST_DistanceSphere(ST_Centroid(p.geom), "
        "ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
        "ORDER BY ST_Area(p.geom::geography) DESC LIMIT %s",
        (_PARK_MIN_AREA_M2, cx, cy, radius / 85000, cx, cy, radius, limit))
    return [{"name": row[0], "ha": round(float(row[1])), "geometry": row[2]}
            for row in cur.fetchall()]


def _track_points(cur, cx: float, cy: float, radius: int, limit: int = 6) -> list[dict]:
    """田径场（塑胶跑道）：命名优先、面积大优先。"""
    cur.execute(
        "SELECT p.name, ST_AsGeoJSON(ST_Centroid(p.geom)) AS geometry "
        "FROM osm_areas p "
        "WHERE p.leisure = 'track' "
        "AND ST_DWithin(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
        "AND ST_DistanceSphere(ST_Centroid(p.geom), "
        "ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
        "ORDER BY ((p.name IS NULL) OR length(p.name) = 0), "
        "ST_Area(p.geom::geography) DESC LIMIT %s",
        (cx, cy, radius / 85000, cx, cy, radius, limit))
    return [{"name": row[0] or "（未命名田径场）", "geometry": row[1]}
            for row in cur.fetchall()]


def _point_feature(item: dict, kind: str, extra: dict | None = None) -> dict:
    g = json.loads(item["geometry"]) if isinstance(item["geometry"], str) else item["geometry"]
    lng, lat = g["coordinates"][:2]
    props = {"name": item["name"], "kind": kind, **(extra or {})}
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lng), float(lat)]},
            "properties": props}


def build_output(trails: list[dict], parks: list[dict], tracks: list[dict],
                 params: dict) -> tuple[dict, list[dict], str]:
    """返回 (geojson, cards, title)：绿道为 MultiLineString 线要素，公园/操场为点。"""
    act = "骑行" if params["activity"] == "ride" else "跑步"
    feats = []
    for t in trails:
        g = json.loads(t["geometry"]) if isinstance(t["geometry"], str) else t["geometry"]
        feats.append({"type": "Feature", "geometry": g,
                      "properties": {"name": t["name"], "kind": "滨水绿道",
                                     "km": t["km"], "segs": t["segs"]}})
    feats += [_point_feature(p, "大公园", {"ha": p["ha"]}) for p in parks]
    feats += [_point_feature(t, "田径场") for t in tracks]
    geojson = {"type": "FeatureCollection", "features": feats}
    cards = [{"type": "runride",
              "title": f"{act}路线推荐 · 滨水绿道 {len(trails)} 条",
              "trails": [{"name": t["name"], "km": t["km"], "segs": t["segs"]}
                         for t in trails],
              "parks": [{"name": p["name"], "ha": p["ha"]} for p in parks],
              "tracks": [{"name": t["name"]} for t in tracks]}]
    return geojson, cards, cards[0]["title"]


# ---------- 总结 ----------

_SUMMARY_SYSTEM = ("你是跑步/骑行路线顾问。基于给定的滨水绿道（名称/约里程）、"
                   "大公园与田径场清单，用中文写不超过 3 句话：推荐里程最长的 1-2 条"
                   "并说明适合的运动类型与节奏；里程为沿水步道约数，实际以路面为准。"
                   "严格使用给定信息，不编造。")


def _fallback_answer(trails: list[dict], activity: str) -> str:
    act = "骑行" if activity == "ride" else "跑步"
    if not trails:
        return "该范围内没有聚合出足够长的滨水绿道，试试扩大范围。"
    t = trails[0]
    return (f"推荐从「{t['name']}」开始（约 {t['km']} km），适合{act}；"
            "地图上绿道为线要素，公园/田径场为点要素，可切换显隐。")


def _summarize(question: str, trails: list[dict], parks: list[dict],
               params: dict, provider, on_delta) -> str:
    act = "骑行" if params["activity"] == "ride" else "跑步"
    lines = [f"{t['name']}：约 {t['km']} km（{t['segs']} 段）" for t in trails[:8]]
    user = (f"用户需求：{question}\n运动类型：{act}\n滨水绿道：\n" + "\n".join(lines)
            + f"\n大公园：{'、'.join(p['name'] for p in parks[:6])}")
    try:
        if on_delta is None:
            return provider.generate(_SUMMARY_SYSTEM, user).strip()
        parts = []
        for d in provider.generate_stream(_SUMMARY_SYSTEM, user):
            parts.append(d)
            on_delta(d)
        return "".join(parts).strip()
    except Exception:
        return _fallback_answer(trails, params["activity"])


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
        on_status("检索绿道与场地")
    try:
        cx, cy, radius = resolve_disc(
            {"region_kind": params["region_kind"],
             "region_name": params["region_name"]}, cfg)
        with readonly_conn(cfg) as conn, conn.cursor() as cur:
            trails = _trail_geometry(cur, params["activity"] == "ride", cx, cy, radius)
            parks = _park_points(cur, cx, cy, radius)
            tracks = _track_points(cur, cx, cy, radius)
    except psycopg.Error as e:
        return ScenarioOutcome(ok=False, error=str(db_error(e)))

    if not trails and not parks and not tracks:
        return ScenarioOutcome(
            ok=False, params=params,
            error="该范围内没有找到滨水绿道、大公园或田径场，请扩大范围再试")

    if on_status:
        on_status("总结中")
    answer = _summarize(question, trails, parks, params, provider, on_delta)
    geojson, cards, title = build_output(trails, parks, tracks, params)
    return ScenarioOutcome(
        ok=True, scenario_type="runride", title=title, cards=cards,
        geojson=geojson, answer=answer, params=params,
        layer_style={"classify": {"column": "kind"}, "label": {"column": "name"}},
        row_count=len(geojson["features"]))
