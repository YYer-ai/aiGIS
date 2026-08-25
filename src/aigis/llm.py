# src/aigis/llm.py
import time
from typing import Protocol
from openai import OpenAI, APITimeoutError, APIConnectionError
from aigis.config import Config


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    def generate(self, system: str, user: str) -> str: ...


class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 60.0, client=None):
        self.model = model
        self._cli = client or OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    def generate(self, system: str, user: str) -> str:
        for attempt in (1, 2):  # 失败自动重试一次
            try:
                resp = self._cli.chat.completions.create(
                    model=self.model, temperature=0,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}])
                return resp.choices[0].message.content
            # openai 3.x 的 APITimeoutError 不继承内建 TimeoutError，需一并捕获
            except (APITimeoutError, APIConnectionError, TimeoutError):
                if attempt == 2:
                    raise LLMError(
                        "DeepSeek API 连接失败（已重试一次），请检查网络与 DEEPSEEK_API_KEY")
                time.sleep(2)


def make_provider(cfg: Config) -> LLMProvider:
    if not cfg.deepseek_api_key:
        raise LLMError("缺少 DEEPSEEK_API_KEY，请在 .env 配置")
    return OpenAICompatProvider(cfg.deepseek_base_url, cfg.deepseek_api_key,
                                cfg.deepseek_model)
