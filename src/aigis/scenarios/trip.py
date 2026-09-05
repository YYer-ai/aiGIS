# src/aigis/scenarios/trip.py
"""场景：旅游行程规划。

流程：LLM 抽参（天数/每日点位/主题/范围）→ SQL 拉候选（主题→OSM 标签映射）
→ 同名去重 + 按类型轮转凑池 → k-means 分天聚类 + 每日点位均衡 → 簇内最近邻贪心定序
→ 点（day/seq/name/kind）+ 每日连线 GeoJSON + 结构化行程卡 → LLM 流式总结。

距离均为直线距离（无路网数据），对用户明示；范围过滤统一为"圆心+半径"
（环路用质心+外接圆半径近似，规划场景可接受；SQL 全部内联模板 + %s 绑定）。
"""
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from aigis.config import Config
from aigis.scenarios import ScenarioOutcome
from aigis.scenarios.db import db_error, readonly_conn
from aigis.scenarios.extract import extract_json
from aigis.scenarios.geo import centroid, greedy_order, haversine_m, kmeans

# 触发正则（高置信）：规划/安排 + 行程/路线/游玩；X日游；旅游攻略
KEYWORDS = [
    re.compile(r"(规划|安排|设计|制定|帮我).{0,12}(行程|路线|旅游|游玩)"),
    re.compile(r"(行程|路线|旅游)(规划|安排|设计)"),
    re.compile(r"[一二三四五1-9]\s*(日|天)(游|行程|路线|玩法)"),
    re.compile(r"旅游攻略|游玩攻略|旅行计划"),
]

_THEMES = ("sightseeing", "history", "nature", "family", "food")
_THEME_ZH = {"sightseeing": "经典观光", "history": "文化历史", "nature": "自然公园",
             "family": "亲子", "food": "美食"}

# 主题 → 候选源：(源id, 中文类型, 单源配额, 最小面积m²[仅面源])
# 源 id 对应 _fetch_source 的查询分支；配额以 1 日 5 点为基准（_scale 缩放）
_SOURCES: dict[str, list[tuple]] = {
    "sightseeing": [
        ("pois_tourism:attraction", "景点", 8, 0),
        ("pois_tourism:museum", "博物馆", 6, 0),
        ("pois_tourism:viewpoint", "观景台", 4, 0),
        ("areas_park", "公园", 6, 30000),
        ("pois_tourism:theme_park", "主题乐园", 2, 0),
        ("pois_tourism:zoo", "动物园", 2, 0),
        ("pois_tourism:aquarium", "海洋馆", 2, 0),
    ],
    "history": [
        ("pois_tourism:museum", "博物馆", 8, 0),
        ("pois_tourism:attraction", "景点", 6, 0),
        ("pois_tourism:artwork", "人文景观", 5, 0),
    ],
    "nature": [
        ("areas_park", "公园", 8, 30000),
        ("pois_tourism:viewpoint", "观景台", 5, 0),
        ("areas_garden", "园林", 4, 5000),
    ],
    "family": [
        ("pois_tourism:theme_park", "主题乐园", 3, 0),
        ("pois_tourism:zoo", "动物园", 2, 0),
        ("pois_tourism:aquarium", "海洋馆", 2, 0),
        ("areas_park", "公园", 6, 30000),
        ("areas_playground", "游乐场", 4, 2000),
    ],
    "food": [
        ("pois_restaurant", "餐厅", 8, 0),
        ("pois_cafe", "咖啡馆", 5, 0),
    ],
}

_RINGS = ("二环", "三环", "四环", "五环", "六环")
_ANCHOR_RADIUS_M = 5000      # place 范围：锚点周边半径
_CITY_CENTER = (116.407, 39.904)  # 未提范围兜底：北京市中心
_CITY_RADIUS_M = 30000       # 覆盖六环


@dataclass
class Spot:
    name: str
    kind: str
    lng: float
    lat: float


# ---------- 参数抽取与归一化 ----------

_EXTRACT_SYSTEM = """你是旅游需求分析助手，从用户需求抽取行程规划参数。
只输出一个 JSON 对象（不要多余文本）：
{"days": 1, "per_day": 5, "themes": ["sightseeing"], "region_name": null, "region_kind": "none"}
规则：
- days：游玩天数，整数 1-5（"一日/一天"=1，"周末两天"=2；未提默认 1）
- per_day：每天想安排的地点数，整数 3-6（未提默认 5）
- themes：主题数组，元素限 sightseeing/history/nature/family/food——
  历史文化/博物馆/古迹→history；自然/公园/风景→nature；亲子/孩子/家庭→family；
  美食/吃/餐厅→food；观光打卡或未明说→sightseeing；可多选
- region_kind："ring"（二环~六环范围）/"place"（具体地名如海淀区、天安门附近、
  奥林匹克森林公园）/"none"（未提范围）；region_name 为对应名称字符串，"none" 时为 null。
  注意：城市本身（如"北京"）不算范围，用 "none"——数据只覆盖北京城区，全城即默认范围
- 结合对话上下文消解指代（如"就那边"沿用上文提到的范围）"""


_CITY_NAMES = ("北京", "北京市", "北京城", "京城")  # 城市名非子范围（数据即北京城区）


def normalize_params(data: dict | None) -> dict:
    """抽取结果防御性归一化：clamp 数值、剔非法主题、环路名匹配失败降级 none。"""
    if not isinstance(data, dict):
        data = {}
    try:
        days = min(5, max(1, int(data.get("days") or 1)))
    except (TypeError, ValueError):
        days = 1
    try:
        per_day = min(6, max(3, int(data.get("per_day") or 5)))
    except (TypeError, ValueError):
        per_day = 5
    raw_themes = data.get("themes")
    themes = [t for t in (raw_themes if isinstance(raw_themes, list) else [])
              if t in _THEMES]
    region_kind = data.get("region_kind") if data.get("region_kind") in ("ring", "place") else "none"
    region_name = str(data.get("region_name") or "").strip()
    if region_kind == "place" and any(c in region_name for c in _CITY_NAMES):
        region_kind = "none"  # "北京/北京城区"类城市名不缩小范围
    if region_kind == "ring" and not match_ring(region_name):
        region_kind = "none"
    if region_kind == "place" and not region_name:
        region_kind = "none"
    return {"days": days, "per_day": per_day,
            "themes": themes or ["sightseeing"],
            "region_kind": region_kind, "region_name": region_name}


def match_ring(name: str) -> str | None:
    """文本中识别环路名（"北三环里"→"三环"）；无匹配返回 None。"""
    for ring in _RINGS:
        if ring in name:
            return ring
    return None


def region_label(params: dict) -> str:
    """区域中文描述（标题用）。"""
    if params["region_kind"] == "ring":
        return f"{match_ring(params['region_name'])}内"
    if params["region_kind"] == "place":
        return f"{params['region_name']}周边"
    return "全城"


# ---------- 区域解析与候选查询 ----------

_RING_DISC_CACHE: dict[str, tuple[float, float, int]] = {}


def ring_disc(ring: str, cfg: Config) -> tuple[float, float, int] | None:
    """环路 → (圆心lng, 圆心lat, 半径m)：质心 + 到 bbox 角距离（外接圆近似）。"""
    if ring in _RING_DISC_CACHE:
        return _RING_DISC_CACHE[ring]
    with readonly_conn(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ST_X(ST_Centroid(geom)), ST_Y(ST_Centroid(geom)), "
            "ST_Distance(ST_Centroid(geom)::geography, "
            "ST_SetSRID(ST_MakePoint(ST_XMin(geom), ST_YMin(geom)), 4326)::geography) "
            "FROM ring_areas WHERE ring_name = %s", (ring,))
        row = cur.fetchone()
    if row is None:
        return None
    disc = (float(row[0]), float(row[1]), int(float(row[2])))
    _RING_DISC_CACHE[ring] = disc
    return disc


def resolve_anchor(name: str, cfg: Config) -> tuple[float, float] | None:
    """地名 → 锚点坐标：区县边界质心优先，其次 POI 命中（旅游类优先）。"""
    with readonly_conn(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ST_X(c), ST_Y(c) FROM ("
            "  SELECT ST_Centroid(geom) AS c, 0 AS pri FROM osm_boundaries "
            "  WHERE name LIKE %s AND admin_level IN (6, 8)"
            "  UNION ALL"
            "  SELECT geom, CASE WHEN tourism IS NOT NULL THEN 1 ELSE 2 END "
            "  FROM osm_pois WHERE name LIKE %s"
            ") t ORDER BY pri LIMIT 1", (f"%{name}%", f"%{name}%"))
        row = cur.fetchone()
    return (float(row[0]), float(row[1])) if row else None


def resolve_disc(params: dict, cfg: Config) -> tuple[float, float, int]:
    """参数 → 圆形检索范围（cx, cy, 半径m）。解析失败回落全城。"""
    if params["region_kind"] == "ring":
        disc = ring_disc(match_ring(params["region_name"]), cfg)
        if disc:
            return disc
    elif params["region_kind"] == "place":
        anchor = resolve_anchor(params["region_name"], cfg)
        if anchor:
            return (*anchor, _ANCHOR_RADIUS_M)
    return (*_CITY_CENTER, _CITY_RADIUS_M)


def _scale(limit: int, need: int) -> int:
    """单源配额按需求量缩放（need 以 5 点/日为基准）。"""
    return max(2, round(limit * need / 5))


def _fetch_source(cur, source: str, kind_zh: str, limit: int, min_area: int,
                  cx: float, cy: float, radius: int) -> None:
    """按源 id 执行候选查询（两段式圆过滤：度粗筛走 GiST + ST_DistanceSphere 精筛，
    geography 直算不利用索引；%s 绑定值：圆心/半径两段、类型、标签值、配额）。"""
    deg = radius / 85000
    if source.startswith("pois_tourism:"):
        cur.execute(
            "SELECT p.name AS name, %s AS kind, ST_AsGeoJSON(p.geom) AS geometry "
            "FROM osm_pois p "
            "WHERE ST_DWithin(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
            "AND ST_DistanceSphere(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
            "AND p.tourism = %s AND p.name IS NOT NULL AND length(p.name) > 1 "
            "ORDER BY p.osm_id LIMIT %s",
            (kind_zh, cx, cy, deg, cx, cy, radius, source.split(":", 1)[1], limit))
    elif source == "pois_restaurant":
        cur.execute(
            "SELECT p.name AS name, %s AS kind, ST_AsGeoJSON(p.geom) AS geometry "
            "FROM osm_pois p "
            "WHERE ST_DWithin(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
            "AND ST_DistanceSphere(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
            "AND p.amenity = 'restaurant' AND p.name IS NOT NULL "
            "AND length(p.name) > 1 AND p.cuisine IS NOT NULL "
            "ORDER BY p.osm_id LIMIT %s",
            (kind_zh, cx, cy, deg, cx, cy, radius, limit))
    elif source == "pois_cafe":
        cur.execute(
            "SELECT p.name AS name, %s AS kind, ST_AsGeoJSON(p.geom) AS geometry "
            "FROM osm_pois p "
            "WHERE ST_DWithin(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
            "AND ST_DistanceSphere(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
            "AND p.amenity = 'cafe' AND p.name IS NOT NULL AND length(p.name) > 1 "
            "ORDER BY p.osm_id LIMIT %s",
            (kind_zh, cx, cy, deg, cx, cy, radius, limit))
    elif source.startswith("areas_"):
        cur.execute(
            "SELECT p.name AS name, %s AS kind, "
            "ST_AsGeoJSON(ST_Centroid(p.geom)) AS geometry "
            "FROM osm_areas p "
            "WHERE ST_DWithin(p.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) "
            "AND ST_DistanceSphere(ST_Centroid(p.geom), "
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326)) <= %s "
            "AND p.leisure = %s AND ST_Area(p.geom::geography) >= %s "
            "AND p.name IS NOT NULL AND length(p.name) > 1 "
            "ORDER BY ST_Area(p.geom::geography) DESC LIMIT %s",
            (kind_zh, cx, cy, deg, cx, cy, radius,
             source[6:], int(min_area), limit))


def fetch_candidates(params: dict, cfg: Config) -> list[Spot]:
    """按主题源逐源拉候选（每源一条小查询）；同名冗余由池阶段去重。"""
    need = params["days"] * params["per_day"]
    cx, cy, radius = resolve_disc(params, cfg)
    spots = []
    with readonly_conn(cfg) as conn, conn.cursor() as cur:
        for theme in params["themes"]:
            for source, kind_zh, limit, min_area in _SOURCES[theme]:
                _fetch_source(cur, source, kind_zh, _scale(limit, need),
                              min_area, cx, cy, radius)
                for name, kind, geometry in cur.fetchall():
                    try:
                        g = json.loads(geometry) if isinstance(geometry, str) else geometry
                        lng, lat = g["coordinates"][:2]
                    except (TypeError, KeyError, ValueError):
                        continue
                    spots.append(Spot(name=name, kind=kind,
                                      lng=float(lng), lat=float(lat)))
    return spots


def build_pool(cands: list[Spot], need: int) -> list[Spot]:
    """同名去重（首现优先）+ 按类型轮转取样到 need 个（多样性优先）。"""
    seen, dedup = set(), []
    for s in cands:
        if s.name not in seen:
            seen.add(s.name)
            dedup.append(s)
    buckets: dict[str, list[Spot]] = {}
    for s in dedup:  # 保序分桶
        buckets.setdefault(s.kind, []).append(s)
    pool, picked = [], set()
    while len(pool) < need:
        progressed = False
        for spots in buckets.values():
            s = next((x for x in spots if id(x) not in picked), None)
            if s is None:
                continue
            picked.add(id(s))
            pool.append(s)
            progressed = True
            if len(pool) >= need:
                break
        if not progressed:
            break
    return pool


# ---------- 分天聚类与路线 ----------

def plan_days(pool: list[Spot], days: int, per_day: int) -> list[list[Spot]]:
    """k-means 分天 → 每簇截断/补齐 per_day → 簇内最近邻贪心定序（最西点起）。"""
    if not pool:
        return []
    coords = [(s.lng, s.lat) for s in pool]
    k = min(days, len(pool))
    labels = kmeans(coords, k)
    clusters: list[list[Spot]] = [[] for _ in range(k)]
    for s, lb in zip(pool, labels):
        clusters[lb].append(s)

    # 每簇截断到 per_day：离簇质心最远的点溢出
    overflow: list[Spot] = []
    for i, cl in enumerate(clusters):
        if len(cl) > per_day:
            c = centroid([(s.lng, s.lat) for s in cl])
            cl.sort(key=lambda s: haversine_m((s.lng, s.lat), c))  # 近质心在前
            overflow += cl[per_day:]
            clusters[i] = cl[:per_day]
    # 不足 per_day 的簇从溢出池按离质心最近补齐
    for cl in clusters:
        while len(cl) < per_day and overflow:
            c = centroid([(s.lng, s.lat) for s in cl]) if cl else _CITY_CENTER
            best = min(overflow, key=lambda s: (haversine_m((s.lng, s.lat), c), s.name))
            overflow.remove(best)
            cl.append(best)

    plan = []
    for cl in clusters:
        if not cl:
            continue
        start = min(range(len(cl)), key=lambda i: cl[i].lng)  # 最西点起（确定性）
        order = greedy_order([(s.lng, s.lat) for s in cl], start)
        plan.append([cl[i] for i in order])
    return plan


def build_geojson(plan: list[list[Spot]]) -> dict:
    """点要素（day/seq/name/kind）+ 每日连线（day/kind=当日路线）的 FeatureCollection。"""
    feats = []
    for day, spots in enumerate(plan, 1):
        for seq, s in enumerate(spots, 1):
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [s.lng, s.lat]},
                          "properties": {"day": day, "seq": seq, "name": s.name, "kind": s.kind}})
        if len(spots) >= 2:
            feats.append({"type": "Feature", "geometry": {"type": "LineString",
                          "coordinates": [[s.lng, s.lat] for s in spots]},
                          "properties": {"day": day, "kind": "当日路线", "name": ""}})
    return {"type": "FeatureCollection", "features": feats}


def build_cards(plan: list[list[Spot]], params: dict) -> list[dict]:
    """行程卡：每天点位序列 + 到下一站直线距离 + 当日全程直线距离。"""
    days = []
    for day, spots in enumerate(plan, 1):
        items = []
        for seq, s in enumerate(spots, 1):
            nxt = spots[seq] if seq < len(spots) else None
            items.append({"seq": seq, "name": s.name, "kind": s.kind,
                          "next_m": round(haversine_m((s.lng, s.lat),
                                                      (nxt.lng, nxt.lat))) if nxt else None})
        total_km = round(sum(haversine_m((a.lng, a.lat), (b.lng, b.lat))
                             for a, b in zip(spots, spots[1:])) / 1000, 1)
        days.append({"day": day, "spots": items, "straight_km": total_km})
    themes_zh = "、".join(_THEME_ZH[t] for t in params["themes"])
    return [{"type": "itinerary",
             "title": f"北京{len(plan)}日行程 · {region_label(params)}",
             "themes": themes_zh, "days": days}]


# ---------- 总结 ----------

_SUMMARY_SYSTEM = ("你是行程讲解员。基于给定的每日行程（地名/类型/顺序/直线距离），"
                   "用中文写不超过 3 句话：先概括整体思路（每天大致区域与主题特色），"
                   "再给一条务实建议（如交通衔接或顺序取舍）。"
                   "严格使用给定地点信息，不编造未列出的地点。")


def _fallback_answer(plan: list[list[Spot]], params: dict) -> str:
    head = "、".join(s.name for s in plan[0][:3])
    return (f"已生成 {len(plan)} 天行程（{region_label(params)}）："
            f"第 1 天从 {head} 等地开始，地图上按天着色并连线，详细顺序见行程卡。")


def _summarize(question: str, plan: list[list[Spot]], params: dict,
               provider, on_delta: Callable[[str], None] | None) -> str:
    days_text = "\n".join(
        f"D{d}：" + " → ".join(s.name for s in spots) for d, spots in enumerate(plan, 1))
    user = (f"用户需求：{question}\n主题：{('、'.join(_THEME_ZH[t] for t in params['themes']))}\n"
            f"每日行程（→ 为游览顺序，距离为直线距离）：\n{days_text}")
    try:
        if on_delta is None:
            return provider.generate(_SUMMARY_SYSTEM, user).strip()
        parts = []
        for d in provider.generate_stream(_SUMMARY_SYSTEM, user):
            parts.append(d)
            on_delta(d)
        return "".join(parts).strip()
    except Exception:
        return _fallback_answer(plan, params)


# ---------- 主流程 ----------

def run(question: str, cfg: Config, provider=None, history: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None) -> ScenarioOutcome:
    from aigis.llm import make_provider
    provider = provider or make_provider(cfg)

    if on_status:
        on_status("理解需求")
    ctx = f"对话上下文（供指代消解）：\n{history}\n\n" if history else ""
    data = extract_json(provider, _EXTRACT_SYSTEM,
                        f"{ctx}用户需求：{question}", on_delta)
    params = normalize_params(data)

    if on_status:
        on_status("检索候选地点")
    try:
        cands = fetch_candidates(params, cfg)
    except psycopg.Error as e:
        return ScenarioOutcome(ok=False, error=str(db_error(e)))

    need = params["days"] * params["per_day"]
    pool = build_pool(cands, max(need * 2, 10))
    if len(pool) < 2:
        themes_zh = "、".join(_THEME_ZH[t] for t in params["themes"])
        return ScenarioOutcome(
            ok=False, params=params,
            error=f"在{region_label(params)}没有找到足够的{themes_zh}类地点，"
                  "请换个范围或主题试试")

    if on_status:
        on_status("规划路线")
    plan = plan_days(pool, params["days"], params["per_day"])
    if not plan:
        return ScenarioOutcome(ok=False, params=params, error="路线规划失败，候选点不足")

    if on_status:
        on_status("总结中")
    answer = _summarize(question, plan, params, provider, on_delta)
    cards = build_cards(plan, params)
    geojson = build_geojson(plan)
    return ScenarioOutcome(
        ok=True, scenario_type="itinerary", title=cards[0]["title"],
        cards=cards, geojson=geojson, answer=answer, params=params,
        layer_style={"classify": {"column": "day"}, "label": {"column": "name"}},
        row_count=len(geojson["features"]))
