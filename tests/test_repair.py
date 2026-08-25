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
