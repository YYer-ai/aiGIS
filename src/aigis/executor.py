# src/aigis/executor.py
from dataclasses import dataclass, field
import psycopg
from psycopg import sql
from aigis.config import Config

# 只读会话 + 语句超时，execute_readonly / explain_readonly 共用
_RO_OPTIONS = "-c statement_timeout=15000 -c default_transaction_read_only=on"


def _connect_ro(cfg: Config):
    return psycopg.connect(
        host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
        user=cfg.db_user, password=cfg.db_password,
        options=_RO_OPTIONS,
        autocommit=True,
    )


@dataclass
class ExecResult:
    ok: bool = False
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str = ""


def execute_readonly(sql: str, cfg: Config) -> ExecResult:
    try:
        with _connect_ro(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                if cur.description is None:
                    return ExecResult(ok=True)
                return ExecResult(ok=True, columns=[d.name for d in cur.description],
                                  rows=cur.fetchall())
    except psycopg.Error as e:
        return ExecResult(error=str(e).strip())


def explain_readonly(sql_text: str, cfg: Config) -> bool:
    """EXPLAIN 只做解析/规划不执行；sql_text 须为仓库内静态常量 SQL（fewshot 回归用）。
    返回 True=可编译，False/异常=编译失败。"""
    try:
        with _connect_ro(cfg) as conn:
            with conn.cursor() as cur:
                # sql_text 来自仓库内静态 YAML 常量（与 schema_export.py 同性质），走 psycopg
                # 官方 sql.SQL 组装 API 声明可信字面量，杜绝字符串拼接 SQL
                cur.execute(sql.SQL("EXPLAIN {}").format(sql.SQL(sql_text)))
                return cur.fetchone() is not None
    except psycopg.Error:
        return False
