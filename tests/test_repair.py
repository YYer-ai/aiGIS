# tests/test_repair.py
from unittest.mock import MagicMock
import pytest
from aigis.config import Config
from aigis.repair import run_query, run_query_stream

# GOOD 用白名单表 osm_pois（真库 28074 行）：spatial_ref_sys 不在 DEFAULT_TABLES
# 白名单内，会被 validator 拒绝，无法走通"修复后成功"路径
BAD = '{"sql":"SELECT * FROM nope","reasoning":"x"}'
GOOD = '{"sql":"SELECT count(*) AS n FROM osm_pois","reasoning":"ok"}'


@pytest.mark.integration
def test_repair_loop_recovers():
    provider = MagicMock()
    provider.generate.side_effect = [BAD, GOOD, GOOD]  # 第1次坏表名→修复→成功
    cfg = Config()
    out = run_query("有多少兴趣点", cfg, provider=provider)
    assert out.ok and out.attempts == 2 and out.rows[0][0] > 8000
    assert out.geojson["features"][0]["properties"]["n"] > 8000


@pytest.mark.integration
def test_gives_up_after_max():
    provider = MagicMock()
    provider.generate.return_value = BAD
    out = run_query("x", Config(), max_retries=2, provider=provider)
    assert not out.ok and out.attempts == 2 and ("nope" in out.error or out.error)


@pytest.mark.integration
def test_execution_error_refeed_recovers():
    """执行失败回喂分支：SQL 过 validator 但真库执行报错（除零）→ 回喂错误 → 修复成功。"""
    provider = MagicMock()
    DIV0 = '{"sql":"SELECT 1/0 AS x FROM osm_pois","reasoning":"x"}'
    provider.generate.side_effect = [DIV0, GOOD]
    out = run_query("x", Config(), provider=provider)
    assert out.attempts == 2 and out.ok and out.rows[0][0] > 8000


def test_run_query_stream_recovers_and_streams_every_round(monkeypatch):
    """流式版自修复：BAD→GOOD 仍修复成功；全部轮次的 delta 与 status 都回调。"""
    monkeypatch.setattr("aigis.repair._cached_schema", lambda cfg: "SCHEMA")
    ok_result = MagicMock(ok=True, columns=["n"], rows=[(42,)])
    monkeypatch.setattr("aigis.repair.execute_readonly",
                        lambda sql, cfg: ok_result)
    provider = MagicMock()
    provider.generate_stream.side_effect = [
        iter(['{"sql":"SELECT * FROM nope",', '"reasoning":"x"}']),
        iter(['{"sql":"SELECT count(*) AS n FROM osm_pois",', '"reasoning":"ok"}']),
    ]
    deltas: list[str] = []
    statuses: list[str] = []
    out = run_query_stream("有多少兴趣点", Config(), on_delta=deltas.append,
                           on_status=statuses.append, provider=provider)
    assert out.ok and out.attempts == 2 and out.rows == [(42,)]
    assert out.geojson["features"][0]["properties"]["n"] == 42
    assert "".join(deltas) == BAD + GOOD  # 全部轮次都推送（前端按轮次重置）
    assert len(deltas) == 4
    assert statuses == ["生成SQL（第1次）", "生成SQL（第2次）", "执行查询"]


def test_run_query_stream_gives_up_without_callbacks(monkeypatch):
    """on_delta/on_status 均缺省时不崩，重试耗尽返回失败 Outcome。"""
    monkeypatch.setattr("aigis.repair._cached_schema", lambda cfg: "SCHEMA")
    provider = MagicMock()
    provider.generate.side_effect = lambda *a: BAD  # 无回调 → 走非流式分支
    provider.generate_stream.side_effect = lambda *a: iter([BAD])
    out = run_query_stream("x", Config(), max_retries=2, provider=provider)
    assert not out.ok and out.attempts == 2 and "nope" in out.error
