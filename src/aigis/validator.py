"""SQL 安全校验器：只放行单条 SELECT/UNION，表白名单 + 危险函数黑名单。"""
import sqlglot
from sqlglot import exp

# 危险函数黑名单（小写匹配）
DANGEROUS_FUNCS = {
    "pg_sleep",
    "pg_read_file",
    "pg_ls_dir",
    "pg_read_binary_file",
    "lo_import",
    "lo_export",
    "dblink",
    "pg_terminate_backend",
}


def validate(sql: str, allowed_tables: set[str]) -> tuple[bool, str]:
    """校验 SQL 是否为安全的单条只读查询。

    返回 (True, "") 表示通过；否则返回 (False, 中文原因)。
    """
    sql = sql.strip()
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        return False, f"SQL 解析失败: {e}"
    if len(statements) != 1:
        return False, "仅允许单条语句"
    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union)):
        return False, f"非查询语句：解析结果为 {type(stmt).__name__}，仅允许 SELECT/UNION 查询"
    # CTE 名是对本地子查询的引用，不算物理表
    cte_names = {c.alias for c in stmt.find_all(exp.CTE)}
    for t in stmt.find_all(exp.Table):
        if t.name not in allowed_tables and t.name not in cte_names:
            return False, f"表 {t.name} 不在表白名单"
    for f in stmt.find_all(exp.Func):
        # 具名函数（如 COUNT）用 sql_name()，匿名函数（如 pg_sleep、ST_AsGeoJSON）用 this
        if isinstance(f, exp.Anonymous):
            name = str(f.this).lower()
        else:
            name = (f.sql_name() or "").lower()
        if name in DANGEROUS_FUNCS:
            return False, f"禁止函数 {name}"
    return True, ""
