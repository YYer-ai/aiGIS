# src/aigis/llm.py
import json
import time
from typing import Iterator, Protocol
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

    def generate_stream(self, system: str, user: str) -> Iterator[str]:
        """流式生成：逐 delta 内容片段 yield（异常处理与 generate 一致，重试一次）。"""
        for attempt in (1, 2):
            try:
                stream = self._cli.chat.completions.create(
                    model=self.model, temperature=0, stream=True,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}])
                for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta is not None:
                        yield delta
                return
            except APIStatusError as e:
                raise LLMError(
                    f"LLM API 返回错误 {e.status_code}：{e.message}"
                    "——请检查 key 与账户余额")
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


_SUMMARY_SYSTEM = ("你是查询结果播报员。用一句自然的中文回答用户的问题，"
                   "严格基于给定查询结果，不编造；数字用阿拉伯数字；"
                   "空结果就说没有找到相关数据。")


def summarize(question: str, columns: list[str], sample_rows: list,
              row_count: int, cfg: Config) -> Iterator[str]:
    """把查询结果总结成一句自然语言回答，流式逐 token yield。

    回答生成属增强项：任何异常降级为模板文本后结束，不抛出、不影响主流程。
    """
    user = (f"用户问题：{question}\n"
            f"列名：{', '.join(columns)}\n"
            f"前5行样本：{json.dumps(sample_rows[:5], ensure_ascii=False, default=str)}\n"
            f"总行数：{row_count}")
    try:
        yield from make_provider(cfg).generate_stream(_SUMMARY_SYSTEM, user)
    except Exception:
        yield f"查询完成，共 {row_count} 行结果。"
