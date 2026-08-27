# src/aigis/repair.py
"""自修复回环：组装 prompt → LLM 生成 JSON → 校验 → 只读执行 → 失败回喂重试 ≤3 次 → GeoJSON。"""
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from aigis.config import Config
from aigis.executor import execute_readonly
from aigis.geojson_out import rows_to_geojson
from aigis.llm import make_provider
from aigis.prompt import build_messages
from aigis.schema_export import DEFAULT_TABLES, export_schema
from aigis.validator import validate


@dataclass
class Outcome:
    question: str
    sql: str = ""
    reasoning: str = ""
    attempts: int = 0
    ok: bool = False
    error: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    geojson: dict | None = None


def _parse_llm_json(text: str) -> tuple[str, str]:
    """剥掉 markdown 代码围栏后解析 {"sql":..., "reasoning":...}。"""
    data = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    return data["sql"], data.get("reasoning", "")


def _fetch(provider, system: str, user: str,
           on_delta: Callable[[str], None] | None) -> str:
    """无回调走 generate；有回调走 generate_stream 并逐 delta 推送。"""
    if on_delta is None:
        return provider.generate(system, user)
    parts: list[str] = []
    for d in provider.generate_stream(system, user):
        parts.append(d)
        on_delta(d)
    return "".join(parts)


def _run(question: str, cfg: Config, max_retries: int, provider,
         on_delta: Callable[[str], None] | None = None,
         on_status: Callable[[str], None] | None = None) -> Outcome:
    out = Outcome(question=question)
    provider = provider or make_provider(cfg)
    schema_text = _cached_schema(cfg)
    feedback = ""
    for attempt in range(1, max_retries + 1):
        out.attempts = attempt
        if on_status:
            on_status(f"生成SQL（第{attempt}次）")
        msgs = build_messages(
            question + (f"\n\n上次尝试失败，信息：{feedback}" if feedback else ""),
            schema_text)
        raw = _fetch(provider, msgs[0]["content"], msgs[1]["content"], on_delta)
        try:
            sql, reasoning = _parse_llm_json(raw)
        except (ValueError, KeyError) as e:  # JSONDecodeError 是 ValueError 子类
            feedback = f"输出不是合法 JSON：{e}"
            out.error = f"LLM 输出解析失败: {e}"
            continue
        out.sql, out.reasoning = sql, reasoning
        ok, reason = validate(sql, set(DEFAULT_TABLES))
        if not ok:
            feedback = f"校验未通过：{reason}"
            out.error = feedback
            continue
        if on_status:
            on_status("执行查询")
        result = execute_readonly(sql, cfg)
        if result.ok:
            out.ok, out.columns, out.rows = True, result.columns, result.rows
            out.geojson = rows_to_geojson(result.columns, result.rows)
            return out
        feedback = f"数据库执行错误：{result.error}"
        out.error = feedback
    return out


def run_query(question: str, cfg: Config, max_retries: int = 3, provider=None) -> Outcome:
    return _run(question, cfg, max_retries, provider)


def run_query_stream(question: str, cfg: Config,
                     on_delta: Callable[[str], None] | None = None,
                     on_status: Callable[[str], None] | None = None,
                     max_retries: int = 3, provider=None) -> Outcome:
    """与 run_query 同构的流式版：全部轮次的 LLM delta 逐段回调 on_delta。"""
    return _run(question, cfg, max_retries, provider, on_delta, on_status)


# 模块级缓存：同一数据库的 schema 只导出一次（重试/多次查询共享）
_SCHEMA_CACHE: dict[str, str] = {}


def _cached_schema(cfg: Config) -> str:
    key = f"{cfg.db_host}:{cfg.db_port}/{cfg.db_name}"
    if key not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[key] = export_schema(
            f"host={cfg.db_host} port={cfg.db_port} "
            f"dbname={cfg.db_name} user={cfg.admin_user} password={cfg.admin_password}")
    return _SCHEMA_CACHE[key]
