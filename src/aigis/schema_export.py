"""从 PostGIS 导出全量 schema 上下文（表/列结构 + 中文 COMMENT + 每表样本值），供 LLM prompt 注入。"""
import psycopg

DEFAULT_TABLES = ["osm_pois", "osm_roads", "osm_areas", "osm_boundaries", "ring_areas"]


def export_schema(conn_info: str, tables: list[str] | None = None) -> str:
    """导出指定表的 schema 文本块；tables 缺省为 DEFAULT_TABLES（validator 白名单同源）。"""
    tables = tables or DEFAULT_TABLES
    parts: list[str] = []
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        for t in tables:
            cur.execute("""
                SELECT obj_description(c.oid) FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname='public' AND c.relname=%s""", (t,))
            row = cur.fetchone()
            if not row:
                continue
            parts.append(f"TABLE {t}")
            if row[0]:
                parts.append(f"COMMENT ON TABLE {t} IS '{row[0]}';")
            cur.execute("""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod),
                       col_description(a.attrelid, a.attnum)
                FROM pg_attribute a
                WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum""", (t,))
            cols = cur.fetchall()
            for name, typ, cmt in cols:
                parts.append(f"  {name} {typ}")
                if cmt:
                    parts.append(f"COMMENT ON COLUMN {t}.{name} IS '{cmt}';")
            # 样本值排除几何列：WKB 十六进制对 LLM 无信息量，徒增 token
            sample_cols = [name for name, typ, _ in cols if not typ.startswith("geometry")]
            if sample_cols:
                col_list = ", ".join(f'"{c}"' for c in sample_cols)
                try:
                    # f-string 安全：t 来自白名单常量，列名来自系统目录，均非用户输入
                    cur.execute(f"SELECT {col_list} FROM {t} LIMIT 3")
                    for sample in cur.fetchall():
                        parts.append("  SAMPLE: " + repr(sample)[:300])
                except psycopg.Error:
                    conn.rollback()
    return "\n".join(parts)
