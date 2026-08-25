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
    p = OpenAICompatProvider("https://api.deepseek.com", "sk-x", "deepseek-chat", client=fake)
    assert '"sql"' in p.generate("sys", "user")

def test_retry_once_on_timeout():
    fake = MagicMock()
    fake.chat.completions.create.side_effect = [TimeoutError, MagicMock(
        choices=[MagicMock(message=MagicMock(content="ok"))])]
    p = OpenAICompatProvider("https://x", "k", "m", client=fake)
    assert p.generate("s", "u") == "ok"

def test_make_provider_by_cfg():
    p = make_provider(Config(deepseek_api_key="sk-1"))
    assert p.model == "deepseek-chat"

def test_missing_key_raises():
    with pytest.raises(LLMError):
        make_provider(Config(deepseek_api_key=""))

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
