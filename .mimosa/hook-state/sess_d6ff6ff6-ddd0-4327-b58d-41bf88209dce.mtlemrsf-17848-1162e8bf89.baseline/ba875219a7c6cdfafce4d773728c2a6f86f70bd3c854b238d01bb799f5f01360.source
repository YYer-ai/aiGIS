# src/aigis_web/store.py
"""会话存储（SQLite，混合记忆核心的持久层）。

连接策略：每请求短连接（open → 用 → close），无跨线程共享，
天然适配 FastAPI 线程池；SQLite 外键级联需每连接 PRAGMA foreign_keys=ON。
库文件默认仓库根 data/chat.db（data/ 已 gitignore），测试可改写模块级 _DB_PATH。
"""
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    summarized INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
"""

# 恒定 6 位微秒的 isoformat：字符串排序等价时间排序（updated_at 倒序依赖）
_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="microseconds")  # noqa: E731


def _default_db_path() -> Path:
    if root := os.environ.get("AIGIS_ROOT"):
        return Path(root) / "data" / "chat.db"
    return Path(__file__).resolve().parent.parent.parent / "data" / "chat.db"


_DB_PATH = _default_db_path()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)  # 幂等建表：首次连接即初始化
    return conn


def _row_session(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "title": r["title"], "summary": r["summary"],
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


def _row_message(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "session_id": r["session_id"], "role": r["role"],
            "content": r["content"], "meta": json.loads(r["meta"]),
            "summarized": r["summarized"], "created_at": r["created_at"]}


def create_session(title: str = "") -> dict:
    sid, now = uuid.uuid4().hex, _NOW()
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO sessions (id, title, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?)", (sid, title, now, now))
    return {"id": sid, "title": title, "summary": "",
            "created_at": now, "updated_at": now}


def get_session(sid: str) -> dict | None:
    with closing(_connect()) as conn:
        r = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    return _row_session(r) if r else None


def list_sessions() -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC, id DESC").fetchall()
    return [_row_session(r) for r in rows]


def rename_session(sid: str, title: str) -> bool:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, sid))
    return cur.rowcount > 0


def delete_session(sid: str) -> bool:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
    return cur.rowcount > 0


def set_summary(sid: str, summary: str) -> bool:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("UPDATE sessions SET summary = ? WHERE id = ?",
                           (summary, sid))
    return cur.rowcount > 0


def append_message(sid: str, role: str, content: str, meta: dict | None = None) -> dict:
    """追加消息；touch 会话 updated_at；首条消息且 title 为空时自动取 content 前 20 字。"""
    now = _NOW()
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, meta, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, role, content, json.dumps(meta or {}, ensure_ascii=False), now))
        mid = cur.lastrowid
        conn.execute(
            "UPDATE sessions SET updated_at = ?, "
            "title = CASE WHEN title = '' AND "
            "(SELECT count(*) FROM messages WHERE session_id = ?) = 1 "
            "THEN ? ELSE title END WHERE id = ?",
            (now, sid, content[:20], sid))
    return {"id": mid, "session_id": sid, "role": role, "content": content,
            "meta": meta or {}, "summarized": 0, "created_at": now}


def get_messages(sid: str, limit: int | None = None) -> list[dict]:
    """正序返回会话最近 limit 条消息（默认全部）。"""
    sql = ("SELECT * FROM (SELECT * FROM messages WHERE session_id = ? "
           "ORDER BY id DESC LIMIT ?) ORDER BY id ASC")
    with closing(_connect()) as conn:
        rows = conn.execute(sql, (sid, limit if limit is not None else -1)).fetchall()
    return [_row_message(r) for r in rows]


def mark_summarized(sid: str, message_ids: list[int]) -> int:
    """标记已并入会话摘要的消息（summarized=1）；返回实际新标记行数。"""
    if not message_ids:
        return 0
    ph = ",".join("?" * len(message_ids))
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            f"UPDATE messages SET summarized = 1 "
            f"WHERE session_id = ? AND summarized = 0 AND id IN ({ph})",
            (sid, *message_ids))
    return cur.rowcount


def summarized_count(sid: str) -> int:
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT count(*) FROM messages WHERE session_id = ? AND summarized = 1",
            (sid,)).fetchone()[0]
