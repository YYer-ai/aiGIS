# tests/test_repair.py
from unittest.mock import MagicMock
import pytest
from aigis.config import Config
from aigis.repair import run_query, run_query_stream

# GOOD 用白名单表 osm_pois（真库 28074 行）：spatial_ref_sys 不在 DEFAULT_TABLES
# 白名单内，会被 validator 拒绝，无法走通"修复后成功"路径
BAD = '{"sql":"SELECT * FROM nope","reasoning":"x"}'
GOOD = '{"sql":"SELECT count(*) AS n FROM osm_pois","reasoning":"ok"}'
CHAT = '{"mode":"chat","reply":"当前数据不含实时交通信息，可查询三环内公园分布"}'
CHAT_REPLY = "当前数据不含实时交通信息，可查询三环内公园分布"


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
    # 降级调用同样返回无 mode 的 BAD → 降级失败 → 最终 ok=False
    provider = MagicMock()
    provider.generate.return_value = BAD
    out = run_query("x", Config(), max_retries=2, provider=provider)
    assert not out.ok and out.attempts == 2 and ("nope" in out.error or out.error)


@pytest.mark.integration
def test_fallback_chat_after_max_retries():
    """失败降级：3 轮 BAD 后追加的降级调用返回 chat 回复 → ok=True chat_mode。"""
    provider = MagicMock()
    provider.generate.side_effect = [BAD, BAD, BAD, CHAT]
    out = run_query("x", Config(), max_retries=3, provider=provider)
    assert out.ok and out.chat_mode and out.attempts == 3
    assert out.answer == CHAT_REPLY and out.error == ""
    assert out.sql == "SELECT * FROM nope"  # 保留最后一次尝试的 SQL


def test_chat_mode_returns_directly_without_executing(monkeypatch):
    """chat 模式直返：首轮 mode=chat → 不校验不执行（execute 未被调用）。"""
    monkeypatch.setattr("aigis.repair._cached_schema", lambda cfg: "SCHEMA")
    execute = MagicMock()
    monkeypatch.setattr("aigis.repair.execute_readonly", execute)
    provider = MagicMock()
    provider.generate.return_value = CHAT
    out = run_query("交通不堵最方便的是哪个公园", Config(), provider=provider)
    assert out.ok and out.chat_mode and out.attempts == 1
    assert out.answer == CHAT_REPLY
    assert out.sql == "" and out.error == ""
    execute.assert_not_called()


def test_validation_failure_retry_can_switch_to_chat(monkeypatch):
    """回喂转 chat：第1轮 BAD SQL 校验失败 → feedback 含'可改用 chat'提示 → 第2轮 mode=chat → ok=True。"""
    monkeypatch.setattr("aigis.repair._cached_schema", lambda cfg: "SCHEMA")
    execute = MagicMock()
    monkeypatch.setattr("aigis.repair.execute_readonly", execute)
    provider = MagicMock()
    provider.generate.side_effect = [BAD, CHAT]
    out = run_query("交通不堵最方便的是哪个公园", Config(), provider=provider)
    assert out.ok and out.chat_mode and out.attempts == 2
    assert out.answer == CHAT_REPLY and out.error == ""
    assert out.sql == "SELECT * FROM nope"  # 保留失败轮的 SQL 供追溯
    # 第2轮回喂的 prompt 追加了转 chat 提示（校验失败轮才有，引导转向）
    second_user = provider.generate.call_args_list[1].args[1]
    assert "可改用" in second_user and '"mode":"chat"' in second_user
    execute.assert_not_called()


def test_chat_mode_stream_pushes_reply_as_delta(monkeypatch):
    """流式 chat：status(回答中) + reply 一次整段作为 on_delta 推送。"""
    monkeypatch.setattr("aigis.repair._cached_schema", lambda cfg: "SCHEMA")
    provider = MagicMock()
    provider.generate_stream.return_value = iter([CHAT])
    deltas: list[str] = []
    statuses: list[str] = []
    out = run_query_stream("你好", Config(), on_delta=deltas.append,
                           on_status=statuses.append, provider=provider)
    assert out.ok and out.chat_mode and out.answer == CHAT_REPLY
    assert statuses == ["生成SQL（第1次）", "回答中"]
    assert deltas[-1] == CHAT_REPLY  # reply 整段（在原始 JSON delta 之后）


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


def test_run_query_passes_history_into_prompt(monkeypatch):
    """history 透传：mock provider 捕获的 user 消息含对话上下文段（问题之前）。"""
    monkeypatch.setattr("aigis.repair._cached_schema", lambda cfg: "SCHEMA")
    ok_result = MagicMock(ok=True, columns=["n"], rows=[(42,)])
    monkeypatch.setattr("aigis.repair.execute_readonly", lambda sql, cfg: ok_result)
    provider = MagicMock()
    provider.generate.return_value = GOOD
    history = "用户：三环内有哪些区\n助手：西城、东城"
    out = run_query("那里有多少兴趣点", Config(), provider=provider, history=history)
    assert out.ok
    user = provider.generate.call_args.args[1]
    assert "对话上下文（最近对话，供指代消解）：" in user and history in user
    assert user.index("对话上下文") < user.index("问题：那里有多少兴趣点")
    # 无 history 时行为不变
    provider2 = MagicMock()
    provider2.generate.return_value = GOOD
    run_query("x", Config(), provider=provider2)
    assert "对话上下文" not in provider2.generate.call_args.args[1]


def test_run_query_stream_passes_history_into_prompt(monkeypatch):
    """流式版同样透传 history。"""
    monkeypatch.setattr("aigis.repair._cached_schema", lambda cfg: "SCHEMA")
    ok_result = MagicMock(ok=True, columns=["n"], rows=[(42,)])
    monkeypatch.setattr("aigis.repair.execute_readonly", lambda sql, cfg: ok_result)
    provider = MagicMock()
    provider.generate_stream.return_value = iter([GOOD])
    history = "用户：查过三环内公园"
    out = run_query_stream("那里有几个地铁站", Config(), on_delta=lambda t: None,
                           provider=provider, history=history)
    assert out.ok
    user = provider.generate_stream.call_args.args[1]
    assert "对话上下文" in user and "查过三环内公园" in user
