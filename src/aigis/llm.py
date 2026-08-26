# src/aigis/llm.py
import time
from typing import Protocol
from openai import OpenAI, APITimeoutError, APIConnectionError, APIStatusError
from aigis.config import Config


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    def generate(self, system: str, user: str) -> str: ...


class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 300.0, client=None):
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
            except APIStatusError as e:  # 401/402 等状态错误重试无意义，直接面向用户报错
                raise LLMError(
                    f"LLM API 返回错误 {e.status_code}：{e.message}"
                    "——请检查 key 与账户余额")
            # openai 3.x 的 APITimeoutError 不继承内建 TimeoutError，需一并捕获
            except (APITimeoutError, APIConnectionError, TimeoutError):
                if attempt == 2:
                    raise LLMError(
                        "LLM API 连接失败（已重试一次），请检查网络与 LLM_API_KEY")
                time.sleep(2)


def make_provider(cfg: Config) -> LLMProvider:
    if not cfg.llm_api_key:
        raise LLMError("缺少 LLM_API_KEY，请在 .env 配置")
    return OpenAICompatProvider(cfg.llm_base_url, cfg.llm_api_key,
                                cfg.llm_model)
