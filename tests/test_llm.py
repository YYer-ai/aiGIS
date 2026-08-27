# tests/test_llm.py
from unittest.mock import MagicMock
import openai
import pytest
from aigis.config import Config
from aigis.llm import OpenAICompatProvider, make_provider, LLMError

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
