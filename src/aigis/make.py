# src/aigis/make.py
"""地图制作引擎（写通道安全件，spec §2）：CTAS 模板校验 + LLM 生成 → maker 执行 → registry 注册。

安全模型：制作 = 派生新表，不修改既有数据。
- validate_make 仅放行单条 CREATE TABLE user_layers.<合法名> AS <只读 SELECT>；
- 内层 SELECT 复用只读 validator（表白名单/函数黑名单/禁 CTE 写入）；
- 真正落库走 aigis_maker 通道（仅 user_layers schema 可写，public 零写权限）。
"""
import json
import re
from dataclasses import dataclass

import psycopg
import sqlglot
from psycopg import sql as pgsql
from sqlglot import exp

from aigis.config import Config
from aigis.geojson_out import rows_to_geojson
from aigis.llm import make_provider
from aigis.repair import _cached_schema
from aigis.schema_export import DEFAULT_TABLES
from aigis.validator import validate

# 图层表名：小写字母开头，仅小写字母/数字/下划线，≤48 字符；
# 保留 registry 注册表本体与 pg_/sql_ 系统前缀
_LAYER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_RESERVED_LAYER_NAMES = {"registry"}
_RESERVED_LAYER_PREFIXES = ("pg_", "sql_")

# 防御纵深：CTAS 树内不得再出现任何直接写语句
_WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter,
                exp.TruncateTable)


def _valid_layer_name(name: str) -> bool:
    return (_LAYER_NAME_RE.match(name) is not None
            and name not in _RESERVED_LAYER_NAMES
            and not name.startswith(_RESERVED_LAYER_PREFIXES))


def _inner_query(stmt: exp.Create) -> exp.Select | exp.Union | None:
    """取 CTAS 的 AS 后查询；括号包裹的子查询剥壳。"""
    inner = stmt.expression
    if isinstance(inner, exp.Subquery):
        inner = inner.unnest()
    return inner if isinstance(inner, (exp.Select, exp.Union)) else None


def _parse_ctas(sql: str) -> tuple[exp.Create | None, str]:
    """解析为单条 CREATE TABLE user_layers.<合法名> AS <查询>；返回 (语句, 中文错误)。"""
    sql = sql.strip()
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        return None, f"SQL 解析失败: {e}"
    if len(statements) != 1:
        return None, "仅允许单条语句"
    stmt = statements[0]
    if not isinstance(stmt, exp.Create) or stmt.kind != "TABLE":
        return None, ("仅允许 CREATE TABLE ... AS SELECT 制作语句（派生新表），"
                      f"当前为 {type(stmt).__name__}")
    table = stmt.this
    if not isinstance(table, exp.Table) or _inner_query(stmt) is None:
        return None, "仅允许 CREATE TABLE ... AS SELECT（不支持列定义式建表或缺 AS 查询）"
    schema = (table.db or "").lower()
    if schema != "user_layers":
        return None, f"目标 schema 必须是 user_layers（当前：{schema or '未指定'}）"
    if not _valid_layer_name(table.name):
        return None, (f"表名 {table.name} 非法：需小写字母开头、仅含小写字母/数字/"
                      "下划线、长度 1-48，且不得为 registry 或 pg_/sql_ 开头")
    if list(stmt.find_all(*_WRITE_NODES)):
        return None, "含数据修改/结构变更语句，制作仅允许派生新表"
    return stmt, ""


def validate_make(sql: str) -> tuple[bool, str]:
    """制作 SQL 校验：结构（单条 user_layers CTAS + 合法表名）+ 内层只读校验。

    内层表白名单 = DEFAULT_TABLES（public 只读源）
    + 引用的合法 user_layers 图层表（图层叠加制作），逐 Table 检查。
    """
    stmt, err = _parse_ctas(sql)
    if stmt is None:
        return False, err
    inner = _inner_query(stmt)
    allowed = set(DEFAULT_TABLES)
    for t in inner.find_all(exp.Table):
        if (t.db or "").lower() == "user_layers" and t.catalog == "":
            if not _valid_layer_name(t.name.lower()):
                return False, f"引用的 user_layers 表 {t.name} 名字非法"
            allowed.add(t.name.lower())
    ok, reason = validate(inner.sql(dialect="postgres"), allowed)
    if not ok:
        return False, f"内层查询不合法：{reason}"
    return True, ""


def _ctas_target_name(sql: str) -> str:
    """提取已通过校验的 CTAS 目标表名（以 SQL 为准，不信 LLM 的 table_name 字段）。"""
    stmt, _ = _parse_ctas(sql)
    return stmt.this.name


# ---------- run_make_task ----------

_MAKE_SYSTEM_TEMPLATE = """你是 GIS 地图制作专家，把中文制作指令转成一条派生新图层的建图 SQL。
规则：
1. 只输出一个 JSON 对象：{{"table_name": "...", "sql": "...", "label": "..."}}，不要多余文本。
2. table_name 用小写英文短名（字母开头，仅小写字母/数字/下划线，不超过 48 字符）；label 是中文图层名。
3. sql 必须是单条 CREATE TABLE user_layers.<table_name> AS SELECT ...，禁止 DROP/ALTER/INSERT/UPDATE/DELETE。
4. SELECT 数据源只能用 public 业务表（osm_pois/osm_roads/osm_areas/osm_boundaries/ring_areas）或已有 user_layers 图层表；新表必须保留名为 geom 的 geometry 列（不要转成文本）。
5. 缓冲/距离/面积用米制：ST_Buffer(geom::geography, 米)::geometry、ST_Area(geom::geography)。
以下是参考样例（中文指令 → JSON）：
{fewshot}"""

_MAKE_FEWSHOT: list[tuple[str, dict]] = [
    # 缓冲
    ("把三环内的公园做500米缓冲区，生成新图层",
     {"table_name": "parks_3ring_buf500",
      "sql": "CREATE TABLE user_layers.parks_3ring_buf500 AS "
             "SELECT a.name, ST_Buffer(a.geom::geography, 500)::geometry AS geom "
             "FROM osm_areas a JOIN ring_areas r ON r.ring_name='三环' "
             "WHERE ST_Contains(r.geom, a.geom) AND a.leisure='park'",
      "label": "三环内公园500米缓冲"}),
    # 裁剪
    ("把道路裁剪到四环范围内，做成新图层",
     {"table_name": "roads_in_4ring",
      "sql": "CREATE TABLE user_layers.roads_in_4ring AS "
             "SELECT r.name, ST_Intersection(r.geom, a.geom) AS geom "
             "FROM osm_roads r JOIN ring_areas a ON a.ring_name='四环' "
             "WHERE ST_Intersects(r.geom, a.geom)",
      "label": "四环内道路"}),
    # 合并（同名面 dissolve）
    ("把同名相邻的公园面合并成一个图层",
     {"table_name": "parks_merged",
      "sql": "CREATE TABLE user_layers.parks_merged AS "
             "SELECT name, ST_Union(geom) AS geom FROM osm_areas "
             "WHERE leisure='park' GROUP BY name",
      "label": "同名公园合并面"}),
    # 相交
    ("找出与长安街相交的公园，生成新图层",
     {"table_name": "parks_on_changanjie",
      "sql": "CREATE TABLE user_layers.parks_on_changanjie AS "
             "SELECT a.name, a.geom FROM osm_areas a JOIN osm_roads r "
             "ON r.name LIKE '%长安街%' WHERE a.leisure='park' "
             "AND ST_Intersects(a.geom, r.geom)",
      "label": "与长安街相交的公园"}),
    # 质心
    ("为每个区县生成一个质心点图层",
     {"table_name": "district_centroids",
      "sql": "CREATE TABLE user_layers.district_centroids AS "
             "SELECT name, ST_Centroid(geom) AS geom FROM osm_boundaries "
             "WHERE admin_level=6",
      "label": "区县质心"}),
    # 简化
    ("把区县边界做简化，生成轻量图层",
     {"table_name": "district_simple",
      "sql": "CREATE TABLE user_layers.district_simple AS "
             "SELECT name, ST_SimplifyPreserveTopology(geom, 0.001) AS geom "
             "FROM osm_boundaries WHERE admin_level=6",
      "label": "区县边界简化"}),
]


def _make_messages(question: str, schema_text: str) -> list[dict]:
    fewshot = "\n".join(f"问：{q}\nJSON：{json.dumps(d, ensure_ascii=False)}"
                        for q, d in _MAKE_FEWSHOT)
    system = _MAKE_SYSTEM_TEMPLATE.format(fewshot=fewshot)
    user = (f"数据库 schema（含中文注释与样本值）：\n{schema_text}\n\n"
            f"制作指令：{question}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


@dataclass
class MakeOutcome:
    ok: bool = False
    table_name: str = ""
    label: str = ""
    sql: str = ""
    feature_count: int = 0
    geojson: dict | None = None
    error: str = ""


def _connect_maker(cfg: Config):
    """maker 通道连接：仅 user_layers 可写；制作（缓冲/叠加）可能重，超时放宽到 60s。"""
    return psycopg.connect(
        host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
        user=cfg.maker_user, password=cfg.maker_password,
        options="-c statement_timeout=60000",
        autocommit=True,
    )


def _sample_geojson(cur, table_name: str) -> dict | None:
    """新图层 GeoJSON 采样：ST_AsGeoJSON(geom) 前 500 要素；失败降级 None 不影响制作。"""
    try:
        cur.execute(pgsql.SQL(
            "SELECT ST_AsGeoJSON(geom) AS geometry FROM {} LIMIT 500").format(
            pgsql.Identifier("user_layers", table_name)))
        return rows_to_geojson([d.name for d in cur.description], cur.fetchall())
    except psycopg.Error:
        return None


def run_make_task(question: str, cfg: Config, provider=None) -> MakeOutcome:
    """制作主流程：prompt → LLM 生成 JSON → make 校验 → maker 执行 → registry 注册 → 采样。"""
    out = MakeOutcome()
    provider = provider or make_provider(cfg)
    msgs = _make_messages(question, _cached_schema(cfg))
    try:
        raw = provider.generate(msgs[0]["content"], msgs[1]["content"])
        data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
        out.sql = data["sql"]
        out.label = str(data.get("label", ""))
    except (ValueError, KeyError) as e:  # JSONDecodeError 是 ValueError 子类
        out.error = f"LLM 输出解析失败: {e}"
        return out
    ok, reason = validate_make(out.sql)
    if not ok:
        out.error = f"制作 SQL 校验未通过：{reason}"
        return out
    out.table_name = _ctas_target_name(out.sql)
    try:
        with _connect_maker(cfg) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(out.sql)
                except psycopg.errors.DuplicateTable:
                    out.error = f"图层 {out.table_name} 已存在，请换个表名重试"
                    return out
                except psycopg.Error as e:
                    out.error = f"执行失败：{e}".strip()
                    return out
                # 执行失败不注册：以下三步都在建表成功之后
                cur.execute(pgsql.SQL("SELECT count(*) FROM {}").format(
                    pgsql.Identifier("user_layers", out.table_name)))
                out.feature_count = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO user_layers.registry "
                    "(layer_name, label, sql, feature_count) VALUES (%s, %s, %s, %s)",
                    (out.table_name, out.label, out.sql, out.feature_count))
                out.geojson = _sample_geojson(cur, out.table_name)
                out.ok = True
    except psycopg.Error as e:  # 注册/统计阶段的库级错误
        out.error = f"注册失败：{e}".strip()
    return out


def drop_maker_layer(table_name: str, cfg: Config) -> tuple[bool, str]:
    """删除图层（管理账号）：DROP TABLE IF EXISTS + registry DELETE；表名过制作同款校验。"""
    if not _valid_layer_name(table_name):
        return False, f"表名 {table_name} 非法：需小写字母开头、仅含小写字母/数字/下划线"
    try:
        with psycopg.connect(
                host=cfg.db_host, port=cfg.db_port, dbname=cfg.db_name,
                user=cfg.admin_user, password=cfg.admin_password,
                autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(pgsql.SQL("DROP TABLE IF EXISTS {}").format(
                    pgsql.Identifier("user_layers", table_name)))
                cur.execute("DELETE FROM user_layers.registry WHERE layer_name = %s",
                            (table_name,))
        return True, ""
    except psycopg.Error as e:
        return False, str(e).strip()
