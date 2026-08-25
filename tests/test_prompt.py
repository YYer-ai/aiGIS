# tests/test_prompt.py
import pytest

from aigis.config import Config
from aigis.executor import execute_readonly
from aigis.prompt import build_messages, load_fewshot
from aigis.schema_export import DEFAULT_TABLES
from aigis.validator import validate


def test_load_fewshot_default():
    shots = load_fewshot()
    assert len(shots) >= 15
    assert all("question" in s and "sql" in s for s in shots)


def test_build_messages():
    msgs = build_messages("三环内有多少公园", "SCHEMA_TEXT_HERE")
    assert msgs[0]["role"] == "system" and "GeoJSON" in msgs[0]["content"]
    assert msgs[-1]["role"] == "user" and "三环内" in msgs[-1]["content"]
    assert "SCHEMA_TEXT_HERE" in msgs[-1]["content"]


# few-shot 自身合规性回归：防止将来手改样例引入过不了 validator 的坏 SQL
@pytest.mark.parametrize("shot", load_fewshot(), ids=lambda s: s["question"][:20])
def test_fewshot_sql_passes_validator(shot):
    ok, reason = validate(shot["sql"], set(DEFAULT_TABLES))
    assert ok, f"few-shot [{shot['question']}] 不合规: {reason}"


# few-shot 真库回归（审查 F1–F3 教训）：validator 只查表白名单/单条语句/危险函数，
# 对列歧义（F1）、函数类型不存在（F2）完全失明；从 EXPLAIN 升级为直接执行——
# EXPLAIN 系 utility 语句无法参数绑定主体，任何拼接形态都会被安全扫描静态规则命中，
# 而真执行比 EXPLAIN 验证更彻底（顺带抓 F3 类运行期错误）；
# 安全性由 validator 白名单 + 只读账号 + statement_timeout 三层兜底
@pytest.mark.integration
@pytest.mark.parametrize("shot", load_fewshot(), ids=lambda s: s["question"][:20])
def test_fewshot_sql_executes_on_real_db(shot):
    r = execute_readonly(shot["sql"], Config())
    assert r.ok, f"few-shot [{shot['question']}] 执行失败: {r.error}"
