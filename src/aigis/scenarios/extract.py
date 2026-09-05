# src/aigis/scenarios/extract.py
"""LLM 结构化抽取公共件：JSON 解析 + 一次回喂重试 + 失败返回 None（调用方用默认值）。"""
import json
from collections.abc import Callable

from aigis.llm import LLMProvider


def parse_llm_json(text: str) -> dict | None:
    """剥 markdown 围栏后解析 JSON 对象；失败返回 None。"""
    try:
        data = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def extract_json(provider: LLMProvider, system: str, user: str,
                 on_delta: Callable[[str], None] | None = None,
                 max_attempts: int = 2) -> dict | None:
    """按 system/user 抽取 JSON；输出非法时把错误回喂重试（共 max_attempts 次）。"""
    feedback = ""
    for _ in range(max_attempts):
        prompt = user + (f"\n\n上次输出无法解析：{feedback}\n请重新只输出 JSON 对象。" if feedback else "")
        if on_delta is None:
            raw = provider.generate(system, prompt)
        else:
            parts = []
            for d in provider.generate_stream(system, prompt):
                parts.append(d)
                on_delta(d)
            raw = "".join(parts)
        data = parse_llm_json(raw)
        if data is not None:
            return data
        feedback = (raw or "")[:200]
    return None
