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
    chat_mode: bool = False  # AI 判定无需 SQL，直接自然语言回复
    answer: str = ""  # chat 模式下的回复文本，web 层直接使用


# 失败降级 prompt：SQL 尝试全部失败后，让 LLM 直接给用户一句解释性回复
_CHAT_FALLBACK_SYSTEM = (
    "用户向空间数据库提出了一个查询，但之前的 SQL 尝试均失败。"
    "请直接给用户一句中文回复解释原因与建议。"
    '只输出一个 JSON 对象：{"mode":"chat","reply":"一句自然的中文回答"}，不要多余文本。')

# 回喂提示：校验/执行失败轮追加，引导模型在问题本质上无法回答时转向 chat
_CHAT_HINT = ('若该问题本质上无法用现有数据回答（如刚才因超时/错误失败），'
              '可改用 {"mode":"chat","reply":"..."} 直接回复用户')


def _parse_llm_json(text: str) -> dict:
    """剥掉 markdown 代码围栏后解析 LLM 输出的 JSON 对象。"""
    return json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())


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
            data = _parse_llm_json(raw)
        except ValueError as e:  # JSONDecodeError 是 ValueError 子类
            feedback = f"输出不是合法 JSON：{e}"
            out.error = f"LLM 输出解析失败: {e}"
            continue
        if data.get("mode") == "chat":  # AI 判定无需 SQL：直接回复，不校验不执行
            reply = str(data.get("reply", ""))
            out.ok, out.chat_mode, out.answer = True, True, reply
            out.reasoning, out.error = reply, ""
            if on_status:
                on_status("回答中")
            if on_delta:  # 流式兼容：reply 一次整段推送
                on_delta(reply)
            return out
        try:
            sql, reasoning = data["sql"], data.get("reasoning", "")
        except KeyError as e:
            feedback = f"输出不是合法 JSON：缺少 {e} 字段"
            out.error = f"LLM 输出解析失败: {e}"
            continue
        out.sql, out.reasoning = sql, reasoning
        ok, reason = validate(sql, set(DEFAULT_TABLES))
        if not ok:
            feedback = f"校验未通过：{reason}。{_CHAT_HINT}"
            out.error = feedback
            continue
        if on_status:
            on_status("执行查询")
        result = execute_readonly(sql, cfg)
        if result.ok:
            out.ok, out.columns, out.rows = True, result.columns, result.rows
            out.geojson = rows_to_geojson(result.columns, result.rows)
            return out
        feedback = f"数据库执行错误：{result.error}。{_CHAT_HINT}"
        out.error = feedback
    # 全部 SQL 尝试失败 → 降级 chat：追加一次 LLM 调用直接生成给用户的解释回复
    fallback_user = f"问题：{question}\n\n之前的 SQL 尝试均失败：{out.error or '未知错误'}"
    try:
        data = _parse_llm_json(
            _fetch(provider, _CHAT_FALLBACK_SYSTEM, fallback_user, on_delta))
        if data.get("mode") == "chat":
            out.ok, out.chat_mode = True, True
            out.answer = str(data.get("reply", ""))
            out.error = ""
            if on_status:
                on_status("回答中")
            if on_delta:
                on_delta(out.answer)
    except ValueError:
        pass  # 降级也失败：保留原失败 Outcome（ok=False）
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
