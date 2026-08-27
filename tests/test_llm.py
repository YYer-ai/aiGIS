# tests/test_llm.py
from unittest.mock import MagicMock
import openai
import pytest
from aigis.config import Config
from aigis.llm import OpenAICompatProvider, make_provider, summarize, LLMError

def test_generate_returns_content():
    fake = MagicMock()
    fake.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"sql":"SELECT 1","reasoning":"r"}'))])
    p = OpenAICompatProvider("http://223.92.35.113:8001/v1", "sk-x", "qwen3827b", client=fake)
    assert '"sql"' in p.generate("sys", "user")

def test_retry_once_on_timeout():
    fake = MagicMock()
    fake.chat.completions.create.side_effect = [TimeoutError, MagicMock(
        choices=[MagicMock(message=MagicMock(content="ok"))])]
    p = OpenAICompatProvider("https://x", "k", "m", client=fake)
    assert p.generate("s", "u") == "ok"

def test_second_timeout_raises_llmerror(monkeypatch):
    # 两次都超时：重试一次后转 LLMError，不再吞异常
    monkeypatch.setattr("aigis.llm.time.sleep", lambda s: None)  # 跳过重试间隔
    fake = MagicMock()
    fake.chat.completions.create.side_effect = [TimeoutError, TimeoutError]
    p = OpenAICompatProvider("https://x", "k", "m", client=fake)
    with pytest.raises(LLMError, match="连接失败"):
        p.generate("s", "u")
    assert fake.chat.completions.create.call_count == 2

def test_make_provider_by_cfg():
    p = make_provider(Config(llm_api_key="sk-1"))
    assert p.model == "qwen3827b"

def test_missing_key_raises():
    with pytest.raises(LLMError):
        make_provider(Config(llm_api_key=""))

def test_status_error_becomes_llmerror_no_retry():
    # 402/401 等状态错误：转 LLMError（含状态码与余额提示）且不重试
    resp = MagicMock(status_code=402)
    err = openai.APIStatusError(
        "Error code: 402 - {'error': {'message': 'Insufficient Balance'}}",
        response=resp, body=None)
    fake = MagicMock()
    fake.chat.completions.create.side_effect = err
    p = OpenAICompatProvider("https://x", "k", "m", client=fake)
    with pytest.raises(LLMError, match="402") as exc_info:
        p.generate("s", "u")
    assert "余额" in str(exc_info.value)
    fake.chat.completions.create.assert_called_once()


def _chunk(text):
    c = MagicMock()
    c.choices = [MagicMock(delta=MagicMock(content=text))]
    return c


def test_generate_stream_yields_deltas_skipping_none():
    fake = MagicMock()
    fake.chat.completions.create.return_value = iter(
        [_chunk("SEL"), _chunk(None), _chunk("ECT 1")])
    p = OpenAICompatProvider("https://x", "k", "m", client=fake)
    parts = list(p.generate_stream("sys", "user"))
    assert parts == ["SEL", "ECT 1"]  # None delta（如 role 帧）被跳过
    assert "".join(parts) == "SELECT 1"
    assert fake.chat.completions.create.call_args.kwargs["stream"] is True


def test_generate_stream_retry_once_on_timeout(monkeypatch):
    monkeypatch.setattr("aigis.llm.time.sleep", lambda s: None)
    fake = MagicMock()
    fake.chat.completions.create.side_effect = [TimeoutError, iter([_chunk("ok")])]
    p = OpenAICompatProvider("https://x", "k", "m", client=fake)
    assert "".join(p.generate_stream("s", "u")) == "ok"


def test_generate_stream_second_timeout_raises_llmerror(monkeypatch):
    monkeypatch.setattr("aigis.llm.time.sleep", lambda s: None)
    fake = MagicMock()
    fake.chat.completions.create.side_effect = [TimeoutError, TimeoutError]
    p = OpenAICompatProvider("https://x", "k", "m", client=fake)
    with pytest.raises(LLMError, match="连接失败"):
        "".join(p.generate_stream("s", "u"))
    assert fake.chat.completions.create.call_count == 2


class _FakeProvider:
    def __init__(self, tokens=None, error=None):
        self.tokens, self.error = tokens, error
        self.calls = {}

    def generate_stream(self, system, user):
        self.calls = {"system": system, "user": user}
        if self.error:
            raise self.error
        return iter(self.tokens)


def test_summarize_streams_natural_answer(monkeypatch):
    fake = _FakeProvider(tokens=["三环内", "共有 292 个公园"])
    monkeypatch.setattr("aigis.llm.make_provider", lambda cfg: fake)
    parts = list(summarize("三环内有多少个公园", ["count"], [["292"]], 1,
                           Config(llm_api_key="sk-1")))
    assert "".join(parts) == "三环内共有 292 个公园"
    # prompt 含系统约束与用户要素（问题/列名/样本/总行数）
    assert "不编造" in fake.calls["system"] and "阿拉伯数字" in fake.calls["system"]
    assert "三环内有多少个公园" in fake.calls["user"]
    assert "count" in fake.calls["user"] and "292" in fake.calls["user"]
    assert "总行数：1" in fake.calls["user"]


def test_summarize_falls_back_to_template_on_llm_error(monkeypatch):
    monkeypatch.setattr("aigis.llm.make_provider",
                        lambda cfg: _FakeProvider(error=LLMError("连接失败")))
    parts = list(summarize("q", ["c"], [["1"]], 3, Config(llm_api_key="sk-1")))
    assert parts == ["查询完成，共 3 行结果。"]  # 降级模板，不抛


def test_summarize_falls_back_when_missing_key():
    # make_provider 缺 key 抛 LLMError：同样降级为模板
    parts = list(summarize("q", ["c"], [], 0, Config(llm_api_key="")))
    assert parts == ["查询完成，共 0 行结果。"]
