# tests/test_scenarios.py
"""场景引擎单测：纯几何工具 + 行程规划（参数/池/聚类/卡片）+ 露营评分 + 路由与 SSE 接入。"""
import json
from unittest.mock import patch

from aigis.scenarios import REGISTRY, ScenarioOutcome, match_scenario
from aigis.scenarios.camping import _quiet, normalize_params as camp_params, score_site
from aigis.scenarios.geo import greedy_order, haversine_m, kmeans
from aigis.scenarios.trip import (Spot, build_cards, build_geojson, build_pool,
                                  match_ring, normalize_params, plan_days)


# ---------- geo ----------

def test_haversine_known_distance():
    # 天安门→北京站 约 2.6km 量级
    d = haversine_m((116.397, 39.909), (116.427, 39.902))
    assert 2000 < d < 3500


def test_kmeans_two_clusters():
    pts = [(116.30, 39.95), (116.31, 39.94), (116.30, 39.96),
           (116.45, 39.88), (116.46, 39.87), (116.45, 39.89)]
    labels = kmeans(pts, 2)
    assert set(labels) == {0, 1}
    assert len({labels[0], labels[3]}) == 2  # 前后分属两簇


def test_kmeans_k_gt_n_and_single():
    assert kmeans([(116.0, 39.9)], 3) == [0]
    assert kmeans([], 2) == []


def test_greedy_order_linear_points():
    # 一条东西向线上的点：从最西出发应自西向东依次访问
    pts = [(116.30, 39.9), (116.32, 39.9), (116.31, 39.9), (116.34, 39.9)]
    assert greedy_order(pts, 0) == [0, 2, 1, 3]


# ---------- trip：参数与池 ----------

def test_normalize_params_clamps_and_defaults():
    p = normalize_params({"days": 9, "per_day": 1, "themes": ["history", "hack"],
                          "region_kind": "ring", "region_name": "十八环"})
    assert p["days"] == 5 and p["per_day"] == 3
    assert p["themes"] == ["history"]
    assert p["region_kind"] == "none"  # 环名匹配失败降级
    assert normalize_params(None)["themes"] == ["sightseeing"]


def test_normalize_params_city_name_not_a_region():
    # 城市名不缩小范围（LLM 误抽"北京"为 place 的防御）
    p = normalize_params({"region_kind": "place", "region_name": "北京", "days": 1})
    assert p["region_kind"] == "none"
    p2 = normalize_params({"region_kind": "place", "region_name": "北京城区"})
    assert p2["region_kind"] == "none"
    # 具体地名仍是 place
    assert normalize_params({"region_kind": "place",
                             "region_name": "奥林匹克森林公园"})["region_kind"] == "place"


def test_match_ring_fuzzy():
    assert match_ring("北三环里") == "三环"
    assert match_ring("五环以内") == "五环"
    assert match_ring("二环") == "二环"
    assert match_ring("没有环") is None


def _spot(name, kind, lng, lat=39.9):
    return Spot(name=name, kind=kind, lng=lng, lat=lat)


def test_build_pool_dedup_and_interleave():
    cands = ([_spot(f"景点{i}", "景点", 116.3 + i * 0.01) for i in range(4)]
             + [_spot(f"博物馆{i}", "博物馆", 116.4 + i * 0.01) for i in range(4)]
             + [_spot("景点0", "景点", 116.5)])  # 与首位同名
    pool = build_pool(cands, 5)
    names = [s.name for s in pool]
    assert len(names) == len(set(names)) == 5
    assert names[:2] == ["景点0", "博物馆0"]  # 轮转：类型交替


def test_build_pool_need_exceeds_supply():
    pool = build_pool([_spot("a", "景点", 116.3), _spot("b", "公园", 116.4)], 10)
    assert {s.name for s in pool} == {"a", "b"}


# ---------- trip：分天与输出 ----------

def _two_cluster_pool(per=5):
    return ([_spot(f"西{i}", "景点", 116.20 + i * 0.01, 39.95) for i in range(per)]
            + [_spot(f"东{i}", "景点", 116.50 + i * 0.01, 39.90) for i in range(per)])


def test_plan_days_two_days_two_clusters():
    plan = plan_days(_two_cluster_pool(), days=2, per_day=5)
    assert len(plan) == 2
    assert all(len(day) == 5 for day in plan)
    for day in plan:  # 两个天然簇应完整分到每天（同名前缀互不混天）
        assert len({s.name[0] for s in day}) == 1


def test_plan_days_overflow_rebalance():
    # 8 点聚 2 天、每天 3 点：截断溢出回池，小簇补齐，总数保持 2×3
    pool = ([_spot(f"w{i}", "景点", 116.2 + i * 0.001, 39.95) for i in range(6)]
            + [_spot(f"e{i}", "景点", 116.6 + i * 0.001, 39.90) for i in range(2)])
    plan = plan_days(pool, days=2, per_day=3)
    assert sum(len(d) for d in plan) == 6
    assert all(len(d) <= 3 for d in plan)


def test_plan_days_empty():
    assert plan_days([], 2, 5) == []


def test_build_geojson_and_cards_structure():
    plan = [[_spot("甲", "博物馆", 116.3), _spot("乙", "公园", 116.4)],
            [_spot("丙", "景点", 116.5)]]
    gj = build_geojson(plan)
    kinds = [f["geometry"]["type"] for f in gj["features"]]
    assert kinds == ["Point", "Point", "LineString", "Point"]  # 单点日无连线
    assert gj["features"][2]["properties"]["day"] == 1
    assert gj["features"][0]["properties"]["seq"] == 1

    cards = build_cards(plan, {"themes": ["history"], "region_kind": "none",
                               "region_name": "", "days": 2, "per_day": 3})
    card = cards[0]
    assert card["type"] == "itinerary" and len(card["days"]) == 2
    d1 = card["days"][0]
    assert d1["spots"][0]["next_m"] > 0 and d1["spots"][1]["next_m"] is None
    assert d1["straight_km"] > 0
    assert "全城" in card["title"]


# ---------- camping ----------

def test_quiet_scoring():
    assert _quiet(None) == 1.0     # 2km 内无主干道最优
    assert _quiet(50) == 0.1
    assert _quiet(800) == 1.0


def test_camp_score_site_weights():
    good = {"water_m": 200, "road_m": 800, "trail_m": 200, "area_m2": 200000}
    bad = {"water_m": None, "road_m": 50, "trail_m": None, "area_m2": 5000}
    assert score_site(good, False) > score_site(bad, False)
    assert score_site(good, True) > score_site(good, False)  # 近水偏好加成


def test_camp_normalize_params():
    p = camp_params({"prefer": "wild", "near_water": True, "region_kind": "place",
                     "region_name": "温榆河"})
    assert p == {"region_kind": "place", "region_name": "温榆河",
                 "near_water": True, "prefer": "wild"}
    assert camp_params({})["prefer"] == "auto"


# ---------- runride ----------

def test_runride_normalize_params():
    from aigis.scenarios.runride import normalize_params as rr_params
    p = rr_params({"activity": "ride", "region_kind": "place", "region_name": "温榆河"})
    assert p == {"activity": "ride", "region_kind": "place", "region_name": "温榆河"}
    assert rr_params({}) == {"activity": "run", "region_kind": "none", "region_name": ""}
    assert rr_params({"region_kind": "place", "region_name": "北京"})["region_kind"] == "none"


def test_runride_build_output():
    from aigis.scenarios.runride import build_output
    trails = [{"name": "清河沿岸", "km": 12.3, "segs": 40,
               "geometry": '{"type":"MultiLineString","coordinates":[[[116.3,39.9],[116.31,39.91]]]}'}]
    parks = [{"name": "奥森", "ha": 680,
              "geometry": '{"type":"Point","coordinates":[116.39,40.01]}'}]
    tracks = [{"name": "工体跑道", "geometry": '{"type":"Point","coordinates":[116.44,39.93]}'}]
    geojson, cards, title = build_output(trails, parks, tracks,
                                         {"activity": "run"})
    kinds = [f["properties"]["kind"] for f in geojson["features"]]
    assert kinds == ["滨水绿道", "大公园", "田径场"]
    assert geojson["features"][0]["geometry"]["type"] == "MultiLineString"
    assert "跑步" in title and "骑行" not in title
    assert cards[0]["trails"][0]["km"] == 12.3
    _, cards2, title2 = build_output(trails, parks, tracks, {"activity": "ride"})
    assert "骑行" in title2


# ---------- 路由与 Web 接入 ----------

def test_match_scenario_routing():
    assert match_scenario("帮我规划北京两天的行程").id == "trip"
    assert match_scenario("推荐一个适合露营扎营的地方").id == "camping"
    assert match_scenario("三环内有多少个公园") is None
    assert match_scenario("想找个地方露营") is not None
    assert match_scenario("推荐几条适合跑步的绿道").id == "runride"


def test_registry_complete():
    assert set(REGISTRY) == {"trip", "camping", "runride"}
    for s in REGISTRY.values():
        assert callable(s.run) and s.keywords


def _collect_sse(client, q):
    """拉一次 SSE，返回 [(event, data_dict)]。"""
    out = []
    with client.stream("GET", "/api/query/stream", params={"q": q}) as r:
        assert r.status_code == 200
        cur = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                cur = line[7:]
            elif line.startswith("data: ") and cur:
                out.append((cur, json.loads(line[6:])))
    return out


def test_stream_scenario_worker_dispatch():
    """场景问题 → 场景 worker：首状态为场景 label，result 带 scenario/layer_style。"""
    out = ScenarioOutcome(ok=True, scenario_type="itinerary", title="北京2日行程 · 全城",
                          cards=[{"type": "itinerary", "title": "t", "days": []}],
                          geojson={"type": "FeatureCollection", "features": []},
                          answer="已生成", params={}, row_count=3,
                          layer_style={"classify": {"column": "day"}})

    def fake_run(question, cfg, history=None, on_delta=None, on_status=None, **kw):
        on_status("规划路线")
        return out

    with patch("aigis_web.app.match_scenario", return_value=REGISTRY["trip"]), \
         patch.object(REGISTRY["trip"], "run", side_effect=fake_run):
        from fastapi.testclient import TestClient
        from aigis_web.app import create_app
        events = _collect_sse(TestClient(create_app()), "帮我规划两天行程")
        assert events[0] == ("status", {"stage": "规划行程"})
        results = [d for e, d in events if e == "result"]
        assert results and results[0]["scenario"]["type"] == "itinerary"
        assert results[0]["layer_style"]["classify"]["column"] == "day"
        assert results[0]["row_count"] == 3


def test_stream_scenario_failure_hint():
    """场景执行失败：error 事件带"改用查询表述"提示。"""
    out = ScenarioOutcome(ok=False, error="没有找到足够的地点")
    with patch("aigis_web.app.match_scenario", return_value=REGISTRY["camping"]), \
         patch.object(REGISTRY["camping"], "run", return_value=out):
        from fastapi.testclient import TestClient
        from aigis_web.app import create_app
        events = _collect_sse(TestClient(create_app()), "想露营")
        errs = [d for e, d in events if e == "error"]
        assert errs and "查询表述" in errs[0]["error"]


def test_stream_scenario_not_matched_goes_query():
    """非场景问题仍走查询流（回归保护）。"""
    with patch("aigis_web.app.run_query_stream") as fake_run, \
         patch("aigis_web.app.summarize", side_effect=lambda *a: iter(["ok"])):
        m = fake_run.return_value
        m.ok = True; m.chat_mode = False; m.sql = "SELECT 1"; m.reasoning = ""
        m.attempts = 1; m.rows = []; m.columns = []; m.geojson = None; m.error = ""
        m.answer = ""
        from fastapi.testclient import TestClient
        from aigis_web.app import create_app
        events = _collect_sse(TestClient(create_app()), "三环内有多少个公园")
        assert events[0] == ("status", {"stage": "理解问题"})
        assert any(e == "result" for e, _ in events)
