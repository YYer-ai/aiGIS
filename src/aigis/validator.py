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
    # F2（审查裁定扩容）：会话层 GUC/锁/序列类
    "set_config",
    "setval",
    "reset",
    "pg_advisory_lock",
    # 最终审查修复波：advisory 锁同族（防换名绕过）
    "pg_advisory_lock_all",
    "pg_advisory_xact_lock",
    "pg_try_advisory_lock",
    "pg_advisory_unlock",
    # 最终审查修复波：xml 导出函数族（字符串参数里的查询不受表白名单约束）
    "query_to_xml",
    "query_to_xml_and_xmlschema",
    "table_to_xml",
    "table_to_xml_and_xmlschema",
    "database_to_xml",
}


def validate(sql: str, allowed_tables: set[str]) -> tuple[bool, str]:
    """校验 SQL 是否为安全的单条只读查询。

    返回 (True, "") 表示通过；否则返回 (False, 中文原因)。
    """
    sql = sql.strip()
    # 表名比较统一小写（PG unquoted 标识符折叠为小写，大小写白名单等价）
    allowed_tables = {t.lower() for t in allowed_tables}
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        return False, f"SQL 解析失败: {e}"
    if len(statements) != 1:
        return False, "仅允许单条语句"
    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union)):
        return False, f"非查询语句：解析结果为 {type(stmt).__name__}，仅允许 SELECT/UNION 查询"
    # 数据修改 CTE（WITH t AS (INSERT/UPDATE/DELETE ...) SELECT ...）顶层是 Select，
    # 不落进上面的语句类型检查，需整树遍历写节点（第一层防线不得对此失明）
    writes = list(stmt.find_all(exp.Insert, exp.Update, exp.Delete))
    if writes:
        return False, "含数据修改语句（INSERT/UPDATE/DELETE），仅允许只读查询"
    # CTE 名是对本地子查询的引用，不算物理表
    cte_names = {c.alias.lower() for c in stmt.find_all(exp.CTE)}
    for t in stmt.find_all(exp.Table):
        if t.name.lower() not in allowed_tables and t.name.lower() not in cte_names:
            return False, f"表 {t.name} 不在表白名单"
    for f in stmt.find_all(exp.Func):
        # 具名函数（如 COUNT）用 sql_name()；匿名函数（如 pg_sleep、ST_AsGeoJSON）
        # 用 f.name 统一取裸名——裸名/引号包裹/schema 前缀三种形态一致，防 quoted 绕过（F1）
        if isinstance(f, exp.Anonymous):
            name = f.name.lower()
        else:
            name = (f.sql_name() or "").lower()
        if name in DANGEROUS_FUNCS:
            return False, f"禁止函数 {name}"
    return True, ""
