# src/aigis_web/settings_store.py
"""模型配置持久层（运维界面后端）：供应商档案 + config.toml 运行参数 + 连接验证。

- 供应商档案 data/providers.json（cc-switch 式多供应商：可存多套、一套激活）。
  激活 = 三元组（base_url/api_key/model）写回项目根 .env 并同步 os.environ——
  load_config() 每请求重读，且 load_dotenv 默认不遮蔽进程已有环境变量，
  故必须同步进程环境，否则激活后仍用旧值。
- 运行参数 config.toml（tomlkit 读写，保留用户手写的其他段落与注释）：
  [llm] thinking/temperature/timeout_seconds，[session] history_rounds/history_char_limit。
- 验证连接：OpenAI 兼容 chat.completions 发 1 token 探针（key+地址+模型全链路）。
"""
import json
import os
import time
import uuid
from pathlib import Path

import tomlkit
from dotenv import set_key
from openai import (OpenAI, APIConnectionError, APIStatusError,
                    APITimeoutError)

_ROOT = Path(__file__).resolve().parent.parent.parent
_PROVIDERS_PATH = _ROOT / "data" / "providers.json"
_TOML_PATH = _ROOT / "config.toml"
_ENV_PATH = _ROOT / ".env"

THINKING_LEVELS = ("off", "low", "medium", "high")

_LLM_DEFAULTS = {"thinking": "off", "temperature": 0.0, "timeout_seconds": 300}
_SESSION_DEFAULTS = {"history_rounds": 3, "history_char_limit": 200}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


# ---------- 供应商档案（providers.json） ----------

def _load_providers_doc() -> dict:
    try:
        doc = json.loads(_PROVIDERS_PATH.read_text("utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("providers"), list):
            return doc
    except (OSError, ValueError):
        pass
    return {"active": None, "providers": []}


def _save_providers_doc(doc: dict) -> None:
    _PROVIDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROVIDERS_PATH.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), "utf-8")


def mask_key(key: str) -> str:
    """api_key 脱敏视图：仅露尾 4 位；短键全掩码。"""
    if not key:
        return ""
    return f"***{key[-4:]}" if len(key) > 4 else "****"


def list_providers() -> list[dict]:
    """供应商列表（api_key 脱敏；UI 展示用，不回传明文）。"""
    doc = _load_providers_doc()
    return [{**p, "api_key": mask_key(p.get("api_key", ""))}
            for p in doc["providers"]]


def get_active_id() -> str | None:
    return _load_providers_doc()["active"]


def upsert_provider(pid: str | None, name: str, base_url: str,
                    api_key: str, model: str) -> dict:
    """新增/更新供应商。api_key 传掩码值（***xxxx）或空表示沿用原值，明文一律覆盖。

    返回含 active 标记的完整档案（脱敏）。
    """
    doc = _load_providers_doc()
    kept = api_key and not api_key.startswith("***")
    if pid:
        for p in doc["providers"]:
            if p["id"] == pid:
                p.update({"name": name, "base_url": base_url,
                          "model": model,
                          "api_key": api_key if kept else p["api_key"]})
                break
        else:
            raise KeyError(f"供应商 {pid} 不存在")
    else:
        pid = uuid.uuid4().hex
        doc["providers"].append({"id": pid, "name": name, "base_url": base_url,
                                 "api_key": api_key, "model": model,
                                 "created_at": _now()})
    _save_providers_doc(doc)
    return {**next(p for p in doc["providers"] if p["id"] == pid),
            "api_key": mask_key(api_key if kept
                                else _plain_key(doc, pid))}


def _plain_key(doc: dict, pid: str) -> str:
    return next((p["api_key"] for p in doc["providers"] if p["id"] == pid), "")


def delete_provider(pid: str) -> None:
    doc = _load_providers_doc()
    before = len(doc["providers"])
    doc["providers"] = [p for p in doc["providers"] if p["id"] != pid]
    if len(doc["providers"]) == before:
        raise KeyError(f"供应商 {pid} 不存在")
    if doc["active"] == pid:
        doc["active"] = None  # 激活者被删：回到 .env 现值，无激活标记
    _save_providers_doc(doc)


def activate_provider(pid: str) -> dict:
    """激活：三元组写 .env + 同步进程环境（load_dotenv 不覆盖已有 env，见模块注释）。"""
    doc = _load_providers_doc()
    p = next((x for x in doc["providers"] if x["id"] == pid), None)
    if p is None:
        raise KeyError(f"供应商 {pid} 不存在")
    if not _ENV_PATH.exists():
        _ENV_PATH.touch()
    for env_k, v in (("LLM_BASE_URL", p["base_url"]),
                     ("LLM_API_KEY", p["api_key"]),
                     ("LLM_MODEL", p["model"])):
        set_key(str(_ENV_PATH), env_k, v)
        os.environ[env_k] = v  # 热生效：不重启 uvicorn，下一请求即用新值
    doc["active"] = pid
    _save_providers_doc(doc)
    return {**p, "api_key": mask_key(p["api_key"])}


def get_provider_plain(pid: str) -> dict:
    """按 id 取明文档案（激活/验证内部用，不外发）。"""
    doc = _load_providers_doc()
    p = next((x for x in doc["providers"] if x["id"] == pid), None)
    if p is None:
        raise KeyError(f"供应商 {pid} 不存在")
    return p


# ---------- 运行参数（config.toml） ----------

def read_llm_settings() -> dict:
    try:
        doc = tomlkit.parse(_TOML_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {**_LLM_DEFAULTS, **_SESSION_DEFAULTS}
    llm, ses = doc.get("llm", {}), doc.get("session", {})
    out = dict(_LLM_DEFAULTS)
    out.update({k: llm[k] for k in _LLM_DEFAULTS if k in llm})
    out.update({k: ses[k] for k in _SESSION_DEFAULTS if k in ses})
    return out


def write_llm_settings(patch: dict) -> dict:
    """合并写入 [llm]/[session] 段（tomlkit 保留其他段落与注释），返回合并后全量。"""
    doc = tomlkit.document()
    if _TOML_PATH.exists():
        try:
            doc = tomlkit.parse(_TOML_PATH.read_text("utf-8"))
        except ValueError:
            doc = tomlkit.document()  # 手写损坏：重建（.env 供应商配置不受影响）
    for section, keys in (("llm", _LLM_DEFAULTS), ("session", _SESSION_DEFAULTS)):
        if section not in doc:
            doc[section] = tomlkit.table()
        for k in keys:
            if patch.get(k) is not None:
                doc[section][k] = patch[k]
    _TOML_PATH.write_text(tomlkit.dumps(doc), "utf-8")
    return {**dict(doc["llm"]), **dict(doc["session"])}


# ---------- 连接验证 ----------

def verify_connection(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """OpenAI 兼容探针：1 token chat 请求，key+地址+模型全链路验证。

    api_key 允许空（本地无鉴权服务以 EMPTY 占位）。返回 (是否成功, 消息)。
    """
    cli = OpenAI(base_url=base_url, api_key=api_key or "EMPTY", timeout=15,
                 max_retries=0)
    t0 = time.monotonic()
    try:
        resp = cli.chat.completions.create(
            model=model, max_tokens=1,
            messages=[{"role": "user", "content": "ping"}])
        n = resp.model or model  # 后端回显的实际模型名（可能带版本后缀）
        return True, f"连接成功：{n}（{time.monotonic() - t0:.1f}s）"
    except APIStatusError as e:
        hint = ("key 无效或无权限" if e.status_code in (401, 403)
                else "模型名不存在或无权限" if e.status_code == 404
                else "账户余额/配额问题" if e.status_code in (402, 429) else "")
        return False, f"HTTP {e.status_code}：{e.message}" + (f"——{hint}" if hint else "")
    except (APITimeoutError, APIConnectionError) as e:
        return False, f"连接失败：{e.__class__.__name__}——请检查 Base URL 与网络"
