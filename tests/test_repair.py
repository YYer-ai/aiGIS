# tests/test_repair.py
from unittest.mock import MagicMock
import pytest
from aigis.config import Config
from aigis.repair import run_query

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
