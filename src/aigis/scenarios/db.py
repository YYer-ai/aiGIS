# src/aigis/scenarios/db.py
"""场景引擎只读连接件：只读账号 + 15s 语句超时（与 executor 同约束）。

场景 SQL 全部由代码模板生成（非 LLM 产物），在调用方以字面量 SQL + %s 参数
绑定执行（表名/列名为代码内常量），不经 validator（那是 NL→SQL 通道的防线）。
"""
import psycopg

from aigis.config import Config

# 只读会话 + 语句超时；force_custom_plan：场景因子查询的 LATERAL 相关子查询在
# 泛型计划下不利用空间索引（实测超 15s），强制按参数值生成自定义计划（毫秒级）
_RO_OPTIONS = ("-c statement_timeout=15000 -c default_transaction_read_only=on "
               "-c plan_cache_mode=force_custom_plan")


class ScenarioDbError(RuntimeError):
    pass


def readonly_conn(cfg: Config):
    """只读连接（上下文管理器形态，调用方 with 使用）。"""
    return psycopg.connect(host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
                           user=cfg.db_user, password=cfg.db_password,
                           options=_RO_OPTIONS)


def db_error(e: Exception) -> ScenarioDbError:
    """psycopg 异常 → 面向用户的中文场景错误。"""
    if isinstance(e, psycopg.OperationalError) and getattr(e, "sqlstate", None) == "57014":
        return ScenarioDbError("查询超时（15秒限制），请缩小范围后重试")
    return ScenarioDbError(f"数据库连接失败，请确认 aigis-postgis 容器在运行：{e}")
