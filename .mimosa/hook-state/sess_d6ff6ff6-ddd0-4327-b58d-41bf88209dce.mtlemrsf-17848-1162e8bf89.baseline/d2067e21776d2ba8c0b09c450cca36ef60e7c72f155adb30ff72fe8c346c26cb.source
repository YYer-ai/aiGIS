# tests/test_prompt.py
import pytest

from aigis.config import Config
from aigis.executor import execute_readonly
from aigis.prompt import build_messages, load_fewshot, render_fewshot
from aigis.schema_export import DEFAULT_TABLES
from aigis.validator import validate


def test_load_fewshot_default():
    shots = load_fewshot()
    assert len(shots) >= 15
    # SQL 条目含 question+sql；chat 条目含 question+mode=chat+reply
    for s in shots:
        if s.get("mode") == "chat":
            assert "question" in s and "reply" in s
        else:
            assert "question" in s and "sql" in s
    assert sum(1 for s in shots if s.get("mode") == "chat") >= 3


def test_build_messages():
    msgs = build_messages("三环内有多少公园", "SCHEMA_TEXT_HERE")
    assert msgs[0]["role"] == "system" and "GeoJSON" in msgs[0]["content"]
    assert msgs[-1]["role"] == "user" and "三环内" in msgs[-1]["content"]
    assert "SCHEMA_TEXT_HERE" in msgs[-1]["content"]


def test_build_messages_with_history():
    """history 插在 user 消息的问题之前（指代消解用），schema 仍在。"""
    history = "用户：三环内有哪些公园\n助手：共 12 个公园"
    msgs = build_messages("那里有多少地铁站", "SCHEMA_TEXT_HERE", history=history)
    user = msgs[-1]["content"]
    assert "对话上下文（最近对话，供指代消解）：\n" + history in user
    assert user.index("对话上下文") < user.index("问题：那里有多少地铁站")
    assert "SCHEMA_TEXT_HERE" in user


def test_build_messages_without_history_unchanged():
    """无 history（None/空串）时 user 内容与旧行为完全一致（兼容现测试）。"""
    old = build_messages("三环内有多少公园", "SCHEMA")
    assert "对话上下文" not in old[-1]["content"]
    for h in (None, ""):
        assert build_messages("三环内有多少公园", "SCHEMA", history=h) == old


def test_build_messages_renders_chat_fewshot():
    """chat 条目渲染为 答（chat）：{json}，SQL 条目仍渲染为 SQL：，两者共存于 system。"""
    msgs = build_messages("交通不堵最方便的是哪个公园", "SCHEMA")
    system = msgs[0]["content"]
    assert "问：交通不堵最方便的是哪个公园\n答（chat）：{\"mode\":\"chat\",\"reply\":" in system
    assert "问：今天天气怎么样\n答（chat）：" in system
    assert "无法直接判断拥堵" in system  # chat 示例 reply 内容原样进入 prompt
    assert "问：三环内有多少个公园\nSQL：" in system  # SQL 条目渲染不受影响


def test_render_fewshot_chat_json_is_valid():
    """chat 条目渲染出的 JSON 可被 json.loads 解析回 mode/reply。"""
    import json
    for s in load_fewshot():
        if s.get("mode") == "chat":
            rendered = render_fewshot([s])
            payload = rendered.split("答（chat）：", 1)[1]
            data = json.loads(payload)
            assert data == {"mode": "chat", "reply": s["reply"]}


# few-shot 自身合规性回归：防止将来手改样例引入过不了 validator 的坏 SQL
# （chat 条目无 sql 字段，跳过——它本就不该生成 SQL）
@pytest.mark.parametrize("shot", [s for s in load_fewshot() if "sql" in s],
                         ids=lambda s: s["question"][:20])
def test_fewshot_sql_passes_validator(shot):
    ok, reason = validate(shot["sql"], set(DEFAULT_TABLES))
    assert ok, f"few-shot [{shot['question']}] 不合规: {reason}"


# few-shot 真库回归（审查 F1–F3 教训）：validator 只查表白名单/单条语句/危险函数，
# 对列歧义（F1）、函数类型不存在（F2）完全失明；从 EXPLAIN 升级为直接执行——
# EXPLAIN 系 utility 语句无法参数绑定主体，任何拼接形态都会被安全扫描静态规则命中，
# 而真执行比 EXPLAIN 验证更彻底（顺带抓 F3 类运行期错误）；
# 安全性由 validator 白名单 + 只读账号 + statement_timeout 三层兜底
@pytest.mark.integration
@pytest.mark.parametrize("shot", [s for s in load_fewshot() if "sql" in s],
                         ids=lambda s: s["question"][:20])
def test_fewshot_sql_executes_on_real_db(shot):
    r = execute_readonly(shot["sql"], Config())
    assert r.ok, f"few-shot [{shot['question']}] 执行失败: {r.error}"
