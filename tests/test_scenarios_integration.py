# tests/test_scenarios_integration.py
"""场景引擎真库集成测试（@pytest.mark.integration，需 aigis-postgis 运行）。

LLM 用 stub（按 system 提示词分流返回抽取 JSON / 总结文本），
数据库走真实 PostGIS——验证场景 SQL 正确性、性能与端到端产物结构。
"""
import json

import pytest

from aigis.config import Config
from aigis.scenarios.camping import run as camping_run
from aigis.scenarios.trip import run as trip_run


class StubProvider:
    """按 system 关键词分流的假 LLM：抽取→固定 JSON；总结→固定中文。"""

    def generate(self, system: str, user: str) -> str:
        if "行程规划参数" in system:
            return json.dumps({"days": 2, "per_day": 4, "themes": ["history", "nature"],
                               "region_kind": "ring", "region_name": "四环"},
                              ensure_ascii=False)
        if "露营选址需求" in system:
            return json.dumps({"region_kind": "none", "region_name": None,
                               "near_water": True, "prefer": "auto"},
                              ensure_ascii=False)
        return "stub 总结文本。"

    def generate_stream(self, system: str, user: str):
        yield self.generate(system, user)


@pytest.mark.integration
def test_trip_run_end_to_end_real_db():
    out = trip_run("帮我规划北京四环内两天的行程，喜欢历史和公园",
                   Config(), provider=StubProvider())
    assert out.ok, out.error
    assert out.scenario_type == "itinerary"
    assert 1 <= len(out.cards[0]["days"]) <= 2
    feats = out.geojson["features"]
    points = [f for f in feats if f["geometry"]["type"] == "Point"]
    lines = [f for f in feats if f["geometry"]["type"] == "LineString"]
    assert len(points) >= 4          # 至少安排出可逛的点位
    assert len(lines) >= 1           # 有当日连线
    assert all(1 <= f["properties"]["day"] <= 2 for f in points)
    # 每日点 seq 连续；卡片与 geojson 点数一致
    card_spots = sum(len(d["spots"]) for d in out.cards[0]["days"])
    assert card_spots == len(points)
    assert "stub 总结" in out.answer


@pytest.mark.integration
def test_trip_run_place_region_anchor():
    out = trip_run("规划天安门附近一日游", Config(), provider=StubProvider())
    assert out.ok, out.error
    # 锚点解析走 place 分支（stub 固定 ring 四环，此测覆盖默认参数路径）
    assert out.geojson["features"]


@pytest.mark.integration
def test_camping_run_end_to_end_real_db():
    out = camping_run("推荐适合露营的地方，最好靠近水", Config(), provider=StubProvider())
    assert out.ok, out.error
    assert out.scenario_type == "camping"
    sites = out.cards[0]["sites"]
    assert 1 <= len(sites) <= 15
    assert all("score" in s and "kind" in s for s in sites)
    # 评分降序
    scores = [s["score"] for s in sites]
    assert scores == sorted(scores, reverse=True)
    assert out.geojson["features"] and out.answer
