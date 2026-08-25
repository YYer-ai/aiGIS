# src/aigis/executor.py
from dataclasses import dataclass, field
import psycopg
from aigis.config import Config

@dataclass
class ExecResult:
    ok: bool = False
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str = ""

def execute_readonly(sql: str, cfg: Config) -> ExecResult:
    try:
        with psycopg.connect(
            host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
            user=cfg.db_user, password=cfg.db_password,
            options="-c statement_timeout=15000 -c default_transaction_read_only=on",
            autocommit=True,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                if cur.description is None:
                    return ExecResult(ok=True)
                return ExecResult(ok=True, columns=[d.name for d in cur.description],
                                  rows=cur.fetchall())
    except psycopg.Error as e:
        return ExecResult(error=str(e).strip())
