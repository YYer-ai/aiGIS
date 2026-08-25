# tests/test_prompt.py
import pytest

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
