# tests/test_prompt.py
import psycopg
from psycopg import sql
import pytest

from aigis.prompt import build_messages, load_fewshot
from aigis.schema_export import DEFAULT_TABLES
from aigis.validator import validate

READONLY = "host=localhost port=5432 dbname=aigis user=aigis_readonly password=aigis_readonly"


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


# few-shot 真库 EXPLAIN 回归（审查 F1–F3 教训）：validator 只查表白名单/单条语句/危险函数，
# 对列歧义（F1）、函数类型不存在（F2）完全失明；EXPLAIN 只做解析/规划不执行，
# 零成本拦截这两类编译期错误（F3 子查询多行系运行期错误，由锚点子查询 LIMIT 1 模板约定防复发）
@pytest.mark.integration
@pytest.mark.parametrize("shot", load_fewshot(), ids=lambda s: s["question"][:20])
def test_fewshot_sql_explains_on_real_db(shot):
    with psycopg.connect(READONLY) as conn, conn.cursor() as cur:
        # fewshot 来自仓库内静态 YAML 常量（与 schema_export.py 同性质），走 psycopg 官方
        # sql.SQL 组装 API 声明可信字面量，杜绝 f-string/加号拼接 SQL 的静态告警
        cur.execute(sql.SQL("EXPLAIN {}").format(sql.SQL(shot["sql"])))
        assert cur.fetchone() is not None
