# spatial_query 引擎实现计划（Phase 1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建中文自然语言 → 空间 SQL → PostGIS 只读执行 → GeoJSON 的命令行引擎，50 题准确率 ≥80%。

**Architecture:** 单 Python 包 `src/aigis`（9 个单一职责模块），数据管道独立脚本（osm2pgsql flex 导入 + 环路预处理）。LLM 层为 OpenAI 兼容 Provider（DeepSeek API 直连，用户裁决不用本地 Ollama）。SQL 安全 = sqlglot 校验白名单 + `aigis_readonly` 只读账号双保险，执行错误回喂 LLM 自修复（≤3 次）。

**Tech Stack:** Python 3.12（uv 托管）、psycopg 3、sqlglot、openai SDK、typer、pytest；PostGIS 16-3.5（Docker，已就绪）；osm2pgsql 2.3.1（tools/，已就绪）。

**Spec:** `docs/superpowers/specs/2026-08-25-spatial-query-engine-design.md`

## Global Constraints

- DB 连接：host=localhost port=5432 dbname=aigis；引擎执行账号 `aigis_readonly/aigis_readonly`，管理账号 `aigis/aigis_dev_2026`（来自 `.env`）。
- LLM：DeepSeek API（`https://api.deepseek.com` + `deepseek-chat`，`.env` 配 `DEEPSEEK_API_KEY`；用户裁决不用本地 Ollama）。
- SQL 校验：仅单条 SELECT（含 CTE/UNION），表白名单，函数黑名单 `pg_sleep/pg_read_file/pg_ls_dir/pg_read_binary_file/lo_import/lo_export`，禁止 COPY。
- 几何输出约定：LLM 生成 SQL 必须用 `ST_AsGeoJSON(geom) AS geometry` 列输出几何。
- 提交信息一律中文；测试框架 pytest；每任务一提交。
- 环境：Windows + Git Bash；面向用户的命令给 PowerShell 7 兼容格式。
- 网络约束：项目代码只允许 http/https 出站请求（本计划内 LLM 客户端均如此）；Phase 1 无服务端。

---

### Task 1: 依赖与包骨架

**Files:**
- Modify: `pyproject.toml`
- Create: `src/aigis/__init__.py`、`tests/__init__.py`（空文件）
- Modify: `.gitignore`（加 `out/`）

**Interfaces:**
- Produces: 包结构 `src/aigis`，依赖 `openai、typer、sqlglot、pyyaml、pytest` 可 import。

- [ ] **Step 1: 更新 pyproject.toml**

```toml
[project]
name = "aigis"
version = "0.1.0"
description = "AI-GIS：中文自然语言空间查询 + GeoAI Agent 对话平台"
requires-python = ">=3.12"
dependencies = [
    "psycopg[binary]>=3.2",
    "python-dotenv>=1.0",
    "openai>=1.50",
    "typer>=0.12",
    "sqlglot>=25.0",
    "pyyaml>=6.0",
]

[project.scripts]
aigis = "aigis.cli:app"

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aigis"]
```

- [ ] **Step 2: 创建骨架并安装**

```bash
mkdir -p src/aigis tests
touch src/aigis/__init__.py tests/__init__.py
cd "E:/aiGIS" && uv sync
```

验证：`uv run python -c "import sqlglot, typer, openai; print('deps ok')"` → `deps ok`

- [ ] **Step 3: .gitignore 追加 `out/` 一行，提交**

```bash
git add -A && git commit -m "chore: 项目骨架与 Phase 1 依赖"
```

---

### Task 2: 本地资源准备（离线韧性）

**Files:**
- Create: `data/`（保存 beijing pbf）

**Interfaces:**
- Produces: `data/beijing-latest.osm.pbf`（约 150MB）。

- [ ] **Step 1: 下载北京 pbf**

Geofabrik 中国裁剪无北京单独分区，用 BBBike 或 download.geofabrik 的 china 后 `osmium` 裁剪过重；直接采用 BBBike 提取服务不可靠，故用 [最新方案]：从 `https://download.geofabrik.de/asia/china-latest.osm.pbf` 无法只取北京。**采用镜像源**：`https://osm-internal.download.geofabrik.de/china/north-china-latest.osm.pbf` 不存在。最终采用：`https://download.bbbike.org/osm/bbbike/Beijing/Beijing.osm.pbf`（BBBike 官方北京提取，约 100-300MB，社区维护）。

```bash
mkdir -p "E:/aiGIS/data"
curl -L -o "E:/aiGIS/data/beijing-latest.osm.pbf" "https://download.bbbike.org/osm/bbbike/Beijing/Beijing.osm.pbf"
```

验证：`ls -la E:/aiGIS/data/`，文件 >50MB 且非 HTML（`head -c 4` 应为二进制 pbf 魔数）。
若 BBBike 失败，fallback：下载 geofabrik `asia/china-latest.osm.pbf`（约 1GB）后用 osm2pgsql 边界过滤导入（`--bbox 115.4 39.4 117.5 41.1`）。

- [ ] **Step 2: .env 追加 LLM 配置**

```env
DEEPSEEK_API_KEY=<用户填入>
```

- [ ] **Step 3: 提交（data/ 与 .env 均被 gitignore 忽略，只提交确认）**

```bash
git status --short   # 确认 data/、.env 未被跟踪
git commit --allow-empty -m "chore: 本地资源准备完成（北京pbf）"
```

---

### Task 3: config.py

**Files:**
- Create: `src/aigis/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config` dataclass 与 `load_config(env_file: str | None = None) -> Config`；字段见下。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_config.py
from pathlib import Path
from aigis.config import Config, load_config

def test_defaults():
    c = Config()
    assert c.db_host == "localhost" and c.db_port == 5432 and c.db_name == "aigis"
    assert c.db_user == "aigis_readonly"      # 引擎执行账号
    assert c.admin_user == "aigis"             # 管理账号（schema 导出/预处理）
    assert c.deepseek_model == "deepseek-chat"

def test_load_env(tmp_path: Path):
    f = tmp_path / ".env"
    f.write_text("DEEPSEEK_API_KEY=sk-test\n", encoding="utf-8")
    c = load_config(str(f))
    assert c.deepseek_api_key == "sk-test"
```

- [ ] **Step 2: 跑测试确认失败** — `uv run pytest tests/test_config.py -v` → ImportError

- [ ] **Step 3: 实现**

```python
# src/aigis/config.py
from dataclasses import dataclass
import os
from dotenv import load_dotenv

@dataclass
class Config:
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "aigis"
    db_user: str = "aigis_readonly"       # 只读执行
    db_password: str = "aigis_readonly"
    admin_user: str = "aigis"             # 导出 schema / 预处理
    admin_password: str = "aigis_dev_2026"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

def load_config(env_file: str | None = None) -> Config:
    load_dotenv(env_file)
    return Config(
        db_host=os.getenv("POSTGRES_HOST", "localhost"),
        db_port=int(os.getenv("POSTGRES_PORT", "5432")),
        db_name=os.getenv("POSTGRES_DB", "aigis"),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
    )
```

- [ ] **Step 4: 跑测试确认通过** — `uv run pytest tests/test_config.py -v` → 2 passed

- [ ] **Step 5: 提交** — `git add -A && git commit -m "feat: 配置模块（.env 驱动，DeepSeek/Ollama 切换）"`

---

### Task 4: OSM 导入管道（flex + 中文 COMMENT）

**Files:**
- Create: `scripts/osm2pgsql-flex.lua`、`scripts/import_osm.ps1`、`scripts/add_comments.sql`

**Interfaces:**
- Produces: 库表 `osm_pois/osm_roads/osm_areas/osm_boundaries`（geometry 4326，含中文 COMMENT）。

- [ ] **Step 1: 写 flex 配置**

```lua
-- scripts/osm2pgsql-flex.lua：北京 OSM → 4 张语义化表
local tables = {}
tables.pois = osm2pgsql.define_table({
  name = 'osm_pois',
  ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
  columns = {
    { column = 'name', type = 'text' },
    { column = 'amenity', type = 'text' }, { column = 'shop', type = 'text' },
    { column = 'tourism', type = 'text' }, { column = 'cuisine', type = 'text' },
  },
  geom_column = { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
})
tables.roads = osm2pgsql.define_table({
  name = 'osm_roads',
  ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
  columns = {
    { column = 'name', type = 'text' }, { column = 'highway', type = 'text' },
    { column = 'ref', type = 'text' }, { column = 'maxspeed', type = 'int' },
  },
  geom_column = { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
})
tables.areas = osm2pgsql.define_table({
  name = 'osm_areas',
  ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
  columns = {
    { column = 'name', type = 'text' },
    { column = 'leisure', type = 'text' }, { column = 'landuse', type = 'text' },
    { column = 'natural', type = 'text' }, { column = 'building', type = 'text' },
  },
  geom_column = { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
})
tables.boundaries = osm2pgsql.define_table({
  name = 'osm_boundaries',
  ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
  columns = {
    { column = 'name', type = 'text' },
    { column = 'admin_level', type = 'int' }, { column = 'boundary', type = 'text' },
  },
  geom_column = { column = 'geom', type = 'geometry', projection = 4326, not_null = true },
})

local GREEN_LEISURE = osm2pgsql.sbox{ 'park', 'garden' }

function process_node(o)
  if o.tags.amenity or o.tags.shop or o.tags.tourism then
    tables.pois:insert({ name = o.tags.name, amenity = o.tags.amenity,
      shop = o.tags.shop, tourism = o.tags.tourism, cuisine = o.tags.cuisine,
      geom = o:as_point() })
  end
end

function process_way(o)
  if o.tags.highway then
    tables.roads:insert({ name = o.tags.name, highway = o.tags.highway,
      ref = o.tags.ref, maxspeed = tonumber(o.tags.maxspeed),
      geom = o:as_linestring() })
  end
  if o:is_closed() and (o.tags.leisure or o.tags.landuse or o.tags.natural
      or (o.tags.building and o.tags.building ~= 'no')) then
    tables.areas:insert({ name = o.tags.name, leisure = o.tags.leisure,
      landuse = o.tags.landuse, natural = o.tags.natural,
      building = o.tags.building, geom = o:as_polygon() })
  end
end

function process_relation(o)
  if o.tags.boundary == 'administrative' and o.tags.admin_level then
    tables.boundaries:insert({ name = o.tags.name,
      admin_level = tonumber(o.tags.admin_level), boundary = o.tags.boundary,
      geom = o:as_multipolygon() })
  elseif o.tags.leisure or o.tags.landuse or o.tags.natural then
    tables.areas:insert({ name = o.tags.name, leisure = o.tags.leisure,
      landuse = o.tags.landuse, natural = o.tags.natural,
      building = o.tags.building, geom = o:as_multipolygon() })
  end
end
```

- [ ] **Step 2: 写中文 COMMENT SQL（scripts/add_comments.sql）**

覆盖 4 张表的表注释与每列注释，例如：

```sql
COMMENT ON TABLE osm_areas IS '面状地物（公园/绿地/水体/建筑区等），含名称与分类标签';
COMMENT ON COLUMN osm_areas.leisure IS '休闲类型：park=公园, garden=花园';
COMMENT ON COLUMN osm_areas.landuse IS '土地利用：grass=草地, forest=森林, meadow=牧场';
COMMENT ON COLUMN osm_areas.natural IS '自然地物：wood=树林, water=水体';
COMMENT ON TABLE osm_boundaries IS '行政区划边界面，admin_level: 4=北京市, 6=区县, 8/9=街道/乡镇';
COMMENT ON TABLE osm_pois IS '兴趣点（点）：餐饮/购物/景点等';
COMMENT ON TABLE osm_roads IS '道路（线）：highway 类型，ref 含环路编号如 S32/G2';
-- 其余列同理，绿化覆盖率定义注释：
COMMENT ON TABLE osm_areas IS '面状地物；绿化= leisure IN (park,garden) OR landuse IN (grass,forest,meadow) OR natural=wood';
```

（执行文件中写全所有列，上为节选要求——**执行者必须为每张表每列写 COMMENT，不得留缺**。）

- [ ] **Step 3: 写导入脚本 scripts/import_osm.ps1**

```powershell
# 用法: pwsh scripts/import_osm.ps1  （读取 data/beijing-latest.osm.pbf）
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
& "$root/tools/osm2pgsql/osm2pgsql-bin/osm2pgsql.exe" `
  --create --slim --drop --output=flex --style "$PSScriptRoot/osm2pgsql-flex.lua" `
  --database aigis --host localhost --port 5432 --username aigis --prefix planet `
  "$root/data/beijing-latest.osm.pbf"
if ($LASTEXITCODE -ne 0) { throw "osm2pgsql 导入失败" }
# 环境变量 PGPASSWORD 由调用者提供
Get-Content "$PSScriptRoot/add_comments.sql" | docker exec -i aigis-postgis psql -U aigis -d aigis -v ON_ERROR_STOP=1
docker exec aigis-postgis psql -U aigis -d aigis -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO aigis_readonly;"
docker exec aigis-postgis psql -U aigis -d aigis -c "ANALYZE;"
```

- [ ] **Step 4: 执行导入并验证**

```bash
cd "E:/aiGIS" && pwsh -File scripts/import_osm.ps1
docker exec aigis-postgis psql -U aigis -d aigis -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;"
```

预期：4 张表行数均 >0（pois 数十万、roads 数十万、areas 数十万、boundaries 数千）。

- [ ] **Step 5: 提交** — `git add scripts/ && git commit -m "feat: OSM flex 导入管道（4 表 + 中文注释）"`

---

### Task 5: 环路多边形预处理（prep_rings）

**Files:**
- Create: `scripts/prep_rings.py`
- Test: `tests/test_prep_rings.py`

**Interfaces:**
- Produces: `build_ring_sql(ring_keyword: str) -> str`（生成提取某环路的 SQL）；脚本入口 `uv run python scripts/prep_rings.py` 产出 `ring_areas(ring_name text, ring_level int, geom geometry(MultiPolygon,4326))`。

- [ ] **Step 1: 写失败测试（纯 SQL 文本断言，不依赖库）**

```python
# tests/test_prep_rings.py
from aigis_prep import build_ring_sql  # scripts/ 加 conftest path 或用 sys.path
# conftest.py: sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from prep_rings import build_ring_sql

def test_ring_sql_contains_core_ops():
    sql = build_ring_sql("三环")
    for frag in ("osm_roads", "三环", "ST_LineMerge", "ST_Polygonize", "ST_Area"):
        assert frag in sql
```

（`tests/conftest.py` 里加 `sys.path.insert(0, .../scripts)`。）

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现 scripts/prep_rings.py**

```python
"""环路多边形预处理：osm_roads 环路线 → ring_areas 环内面。
闭合环用 ST_Polygonize 取最大面积面；不闭合 fallback ST_Buffer 300m。"""
import sys
from pathlib import Path
import psycopg

RING_LEVELS = {"二环": 2, "三环": 3, "四环": 4, "五环": 5, "六环": 6}

def build_ring_sql(ring_keyword: str) -> str:
    return f"""
    WITH ring_lines AS (
      SELECT ST_LineMerge(ST_Collect(geom)) AS geom
      FROM osm_roads
      WHERE name LIKE '%{ring_keyword}%' AND highway IN ('motorway','trunk','primary','secondary')
    ), candidates AS (
      SELECT (ST_Dump(ST_Polygonize(geom))).geom AS poly FROM ring_lines WHERE ST_IsClosed(geom)
    )
    SELECT '{ring_keyword}', {RING_LEVELS[ring_keyword]}, ST_Multi(poly)
    FROM candidates ORDER BY ST_Area(poly::geography) DESC LIMIT 1
    """

DROP_SQL = "DROP TABLE IF EXISTS ring_areas;"
CREATE_SQL = """CREATE TABLE ring_areas(
  ring_name text, ring_level int,
  geom geometry(MultiPolygon,4326) NOT NULL);
COMMENT ON TABLE ring_areas IS '环路环内面：查询“N环内”用 ST_Contains(ring_areas.geom, x.geom)，ring_level 2-6';
CREATE INDEX ON ring_areas USING GIST(geom);"""

def main(conn_info: str):
    with psycopg.connect(conn_info, autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute(DROP_SQL); cur.execute(CREATE_SQL)
        for ring in RING_LEVELS:
            cur.execute(build_ring_sql(ring))
            row = cur.fetchone()
            if row and row[2]:
                cur.execute("INSERT INTO ring_areas VALUES (%s,%s,%s)", row)
                print(f"{ring}: 面积 {psycopg.types.string storytelling}" if False else f"{ring}: OK")
            else:
                print(f"{ring}: 未闭合或未找到，跳过（LLM 将走 ST_DWithin 近似）")
        cur.execute("GRANT SELECT ON ring_areas TO aigis_readonly;")

if __name__ == "__main__":
    main("host=localhost port=5432 dbname=aigis user=aigis password=aigis_dev_2026")
```

（注意：上面 print 中故意留的 `if False else` 是错误示例——**执行者实现时直接写** `print(f"{ring}: OK, 面积(km²)={area_km2:.1f}")`，area 由 `ST_Area(geom::geography)/1e6` 附加 SELECT 返回。）

- [ ] **Step 4: 跑测试通过 + 执行预处理并验证**

```bash
uv run pytest tests/test_prep_rings.py -v
uv run python scripts/prep_rings.py
docker exec aigis-postgis psql -U aigis -d aigis -c "SELECT ring_name, round(ST_Area(geom::geography)/1e6,1) AS km2 FROM ring_areas ORDER BY ring_level;"
```

预期：至少三环~六环有面（二环如缺失可接受，打印已提示）。三环内面积约 60-160 km² 量级为合理。

- [ ] **Step 5: 提交** — `git add scripts/prep_rings.py tests/ && git commit -m "feat: 环路多边形预处理（polygonize+buffer降级）"`

---

### Task 6: schema_export.py

**Files:**
- Create: `src/aigis/schema_export.py`
- Test: `tests/test_schema_export.py`

**Interfaces:**
- Produces: `export_schema(conn_info: str, tables: list[str] | None = None) -> str`——返回含 DDL、中文 COMMENT、每表 3 行样本值的文本块（供 prompt 注入）。默认表 = `['osm_pois','osm_roads','osm_areas','osm_boundaries','ring_areas']`。

- [ ] **Step 1: 写失败测试（用容器真库，标记 integration）**

```python
# tests/test_schema_export.py
import pytest
from aigis.schema_export import export_schema
ADMIN = "host=localhost port=5432 dbname=aigis user=aigis password=aigis_dev_2026"

@pytest.mark.integration
def test_export_contains_comments_and_samples():
    text = export_schema(ADMIN)
    assert "osm_roads" in text and "COMMENT" in text
    assert "ring_areas" in text
    for t in ("osm_pois", "osm_areas", "osm_boundaries"):
        assert f"TABLE {t}" in text
```

`pyproject.toml` 追加 markers：`[tool.pytest.ini_options] markers = ["integration: 需要运行中的 PostGIS 容器"]`。

- [ ] **Step 2: 跑测试确认失败** → ModuleNotFoundError

- [ ] **Step 3: 实现**

```python
# src/aigis/schema_export.py
import psycopg

DEFAULT_TABLES = ["osm_pois", "osm_roads", "osm_areas", "osm_boundaries", "ring_areas"]

def export_schema(conn_info: str, tables: list[str] | None = None) -> str:
    tables = tables or DEFAULT_TABLES
    parts: list[str] = []
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        for t in tables:
            cur.execute("""
                SELECT c.relkind, obj_description(c.oid) FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname='public' AND c.relname=%s""", (t,))
            row = cur.fetchone()
            if not row:
                continue
            parts.append(f"TABLE {t} -- {row[1] or ''}")
            cur.execute("""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod),
                       col_description(a.attrelid, a.attnum)
                FROM pg_attribute a
                WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum""", (t,))
            cols = cur.fetchall()
            for name, typ, cmt in cols:
                parts.append(f"  {name} {typ} -- {cmt or ''}")
            try:
                cur.execute(f"SELECT * FROM {t} LIMIT 3")  # t 来自白名单常量
                for sample in cur.fetchall():
                    parts.append("  SAMPLE: " + repr(sample)[:300])
            except psycopg.Error:
                conn.rollback()
    return "\n".join(parts)
```

- [ ] **Step 4: 跑测试通过** — `uv run pytest tests/test_schema_export.py -v` → passed（容器需在跑）

- [ ] **Step 5: 提交** — `git add -A && git commit -m "feat: schema 导出（DDL+中文注释+样本值）"`

---

### Task 7: validator.py（安全核心）

**Files:**
- Create: `src/aigis/validator.py`
- Test: `tests/test_validator.py`

**Interfaces:**
- Produces: `validate(sql: str, allowed_tables: set[str]) -> tuple[bool, str]`；(True,"") 通过；False 带中文原因。

- [ ] **Step 1: 写失败测试（拦截矩阵）**

```python
# tests/test_validator.py
import pytest
from aigis.validator import validate

TABLES = {"osm_pois", "osm_roads", "osm_areas", "osm_boundaries", "ring_areas"}

GOOD = [
    "SELECT name FROM osm_pois WHERE amenity='restaurant' LIMIT 5",
    "WITH t AS (SELECT * FROM osm_areas) SELECT count(*) FROM t",
    "SELECT name FROM osm_pois UNION SELECT name FROM osm_areas",
    "SELECT name, ST_AsGeoJSON(geom) AS geometry FROM osm_roads WHERE name LIKE '%长安街%'",
]
BAD = [
    ("DELETE FROM osm_pois", "非查询"),
    ("DROP TABLE osm_pois", "非查询"),
    ("INSERT INTO osm_pois VALUES (1)", "非查询"),
    ("UPDATE osm_areas SET name='x'", "非查询"),
    ("SELECT pg_sleep(10)", "函数"),
    ("SELECT pg_read_file('x')", "函数"),
    ("SELECT 1; SELECT 2", "单条"),
    ("SELECT * FROM pg_tables", "表白"),
    ("SELECT * FROM information_schema.columns", "表白"),
    ("COPY osm_pois TO 'x'", "非查询"),
    ("SELECT lo_import('x')", "函数"),
    ("SELECT * FROM nonexistent_table", "表白"),
    ("不是SQL", "解析"),
]

@pytest.mark.parametrize("sql", GOOD)
def test_good_sql_passes(sql):
    ok, reason = validate(sql, TABLES)
    assert ok, reason

@pytest.mark.parametrize("sql,expect", BAD)
def test_bad_sql_rejected(sql, expect):
    ok, reason = validate(sql, TABLES)
    assert not ok and expect in reason
```

- [ ] **Step 2: 跑测试确认失败**（13 个 BAD 应全 FAIL：模块不存在）

- [ ] **Step 3: 实现**

```python
# src/aigis/validator.py
import sqlglot
from sqlglot import exp

DANGEROUS_FUNCS = {"pg_sleep", "pg_read_file", "pg_ls_dir", "pg_read_binary_file",
                   "lo_import", "lo_export", "dblink", "pg_terminate_backend"}

def validate(sql: str, allowed_tables: set[str]) -> tuple[bool, str]:
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        return False, f"SQL 解析失败: {e}"
    if len(statements) != 1:
        return False, "仅允许单条语句"
    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union)):
        return False, "仅允许 SELECT 查询（禁止 DML/DDL/COPY）"
    for t in stmt.find_all(exp.Table):
        if t.name not in allowed_tables:
            return False, f"表 {t.name} 不在白名单"
    for f in stmt.find_all(exp.Func):
        name = (f.sql_name() or "").lower() if isinstance(f, exp.Anonymous) is False else str(f.this).lower()
        if isinstance(f, exp.Anonymous):
            name = str(f.this).lower()
        if name in DANGEROUS_FUNCS:
            return False, f"禁止函数 {name}"
    return True, ""
```

（执行者注意：sqlglot 版本中 `exp.Anonymous` 与具名函数取名方式不同，如上代码已覆盖两分支；若某版本 API 不同，以测试矩阵全绿为准。）

- [ ] **Step 4: 跑测试通过**（26 个参数化全绿）

- [ ] **Step 5: 提交** — `git add -A && git commit -m "feat: SQL 安全校验器（sqlglot 白名单+函数黑名单）"`

---

### Task 8: prompt.py + few-shot 库

**Files:**
- Create: `src/aigis/prompt.py`、`data/fewshot.yaml`
- Test: `tests/test_prompt.py`

**Interfaces:**
- Produces:
  - `SYSTEM_TEMPLATE`（含角色/输出 JSON 约定/几何列约定）
  - `load_fewshot(path: str | None = None) -> list[dict]`（键：question, sql）
  - `build_messages(question: str, schema_text: str) -> list[dict]`（OpenAI messages 格式）

- [ ] **Step 1: 写 data/fewshot.yaml（≥15 条，中文意图→空间 SQL）**

覆盖类别矩阵（每类 ≥2 条，样例）：

```yaml
- question: 三环内有多少个公园
  sql: SELECT count(*) FROM osm_areas a JOIN ring_areas r ON r.ring_name='三环' WHERE ST_Contains(r.geom, a.geom) AND a.leisure='park'
- question: 距天安门广场2公里内的地铁站……（osm_roads→站点表缺，改用 pois）
- question: 名字包含"海淀"的行政区
  sql: SELECT name, ST_AsGeoJSON(geom) AS geometry FROM osm_boundaries WHERE name LIKE '%海淀%'
- question: 五环内面积最大的三个公园
  sql: SELECT name, ST_AsGeoJSON(geom) AS geometry FROM osm_areas a JOIN ring_areas r ON r.ring_name='五环' WHERE ST_Contains(r.geom,a.geom) AND a.leisure='park' ORDER BY ST_Area(a.geom::geography) DESC LIMIT 3
# ……共 15 条：ST_DWithin 距离类、ST_Contains 环内类、LIKE 模糊地名类、
# ST_Area 排序类、admin_level 行政区类、绿化覆盖率计算类（见下）、
# ST_Intersections 叠加类、buffer 缓冲类、最近邻类（ST_DWithin+ORDER BY）
```

其中绿化覆盖率 few-shot（完整写出，执行者照录）：

```yaml
- question: 三环内绿化覆盖率最高的五个区县
  sql: SELECT b.name, round((SUM(ST_Area(ST_Intersection(g.geom,b.geom)::geography)) / ST_Area(b.geom::geography) * 100)::numeric, 2) AS green_rate_pct, ST_AsGeoJSON(b.geom) AS geometry FROM osm_boundaries b JOIN osm_areas g ON ST_Intersects(g.geom,b.geom) AND (g.leisure IN ('park','garden') OR g.landuse IN ('grass','forest','meadow') OR g.natural='wood') JOIN ring_areas r ON r.ring_name='三环' AND ST_Contains(r.geom, ST_Centroid(b.geom)) WHERE b.admin_level=6 GROUP BY b.name, b.geom ORDER BY green_rate_pct DESC LIMIT 5
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_prompt.py
from aigis.prompt import build_messages, load_fewshot

def test_load_fewshot_default():
    shots = load_fewshot()
    assert len(shots) >= 15
    assert all("question" in s and "sql" in s for s in shots)

def test_build_messages():
    msgs = build_messages("三环内有多少公园", "SCHEMA_TEXT_HERE")
    assert msgs[0]["role"] == "system" and "GeoJSON" in msgs[0]["content"]
    assert msgs[-1]["role"] == "user" and "三环内" in msgs[-1]["content"]
    assert "SCHEMA_TEXT_HERE" in msgs[-1]["content"]
```

- [ ] **Step 3: 跑测试失败 → 实现 prompt.py**

```python
# src/aigis/prompt.py
from pathlib import Path
import yaml

SYSTEM_TEMPLATE = """你是空间查询专家，把中文问题转成一条 PostGIS SQL。
规则：
1. 只输出一个 JSON 对象：{"sql": "...", "reasoning": "简短中文说明"}，不要多余文本。
2. 仅单条 SELECT；只能用 schema 中列出的表和列。
3. 几何一律用 ST_AsGeoJSON(geom) AS geometry 输出为 GeoJSON。
4. 面积计算用 geography 强转（米制）：ST_Area(geom::geography)。
5. "N环内" 用 ring_areas 表 ST_Contains；距离用 ST_DWithin(geom::geography)。
6. 地名模糊匹配用 name LIKE '%关键词%'。
以下是参考样例（中文问题 → SQL）：
{fewshot}"""

def load_fewshot(path: str | None = None) -> list[dict]:
    p = Path(path) if path else Path(__file__).parent.parent.parent / "data" / "fewshot.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)

def build_messages(question: str, schema_text: str) -> list[dict]:
    shots = load_fewshot()
    fewshot = "\n".join(f"问：{s['question']}\nSQL：{s['sql']}" for s in shots)
    system = SYSTEM_TEMPLATE.format(fewshot=fewshot)
    user = f"数据库 schema（含中文注释与样本值）：\n{schema_text}\n\n问题：{question}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
```

注意 `load_fewshot` 默认路径要指向仓库 `data/fewshot.yaml`（安装为 wheel 时用环境变量 `AIGIS_ROOT` 或相对 cwd——**执行者按可运行为准，测试从仓库根跑**）。

- [ ] **Step 4: 跑测试通过；Step 5: 提交** — `git commit -m "feat: 提示词组装与空间函数 few-shot 库（15条）"`

---

### Task 9: llm.py（Provider 抽象）

**Files:**
- Create: `src/aigis/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces:
  - `LLMProvider` Protocol：`generate(system: str, user: str) -> str`
  - `OpenAICompatProvider(base_url: str, api_key: str, model: str, timeout: float = 60.0)`
  - `make_provider(cfg: Config) -> LLMProvider`
  - 异常 `LLMError(RuntimeError)`

- [ ] **Step 1: 写失败测试（mock OpenAI 客户端，不发真请求）**

```python
# tests/test_llm.py
from unittest.mock import MagicMock
import pytest
from aigis.config import Config
from aigis.llm import OpenAICompatProvider, make_provider, LLMError

def test_generate_returns_content():
    fake = MagicMock()
    fake.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"sql":"SELECT 1","reasoning":"r"}'))])
    p = OpenAICompatProvider("https://api.deepseek.com", "sk-x", "deepseek-chat", client=fake)
    assert '"sql"' in p.generate("sys", "user")

def test_retry_once_on_timeout():
    fake = MagicMock()
    fake.chat.completions.create.side_effect = [TimeoutError, MagicMock(
        choices=[MagicMock(message=MagicMock(content="ok"))])]
    p = OpenAICompatProvider("https://x", "k", "m", client=fake)
    assert p.generate("s", "u") == "ok"

def test_make_provider_by_cfg():
    p = make_provider(Config(deepseek_api_key="sk-1"))
    assert p.model == "deepseek-chat"

def test_missing_key_raises():
    with pytest.raises(LLMError):
        make_provider(Config(deepseek_api_key=""))
```

- [ ] **Step 2: 跑测试失败 → Step 3: 实现**

```python
# src/aigm/llm.py —— 注意包名是 aigis（防笔误：文件头注释执行者自查）
import time
from openai import OpenAI, APITimeoutError, APIConnectionError
from aigis.config import Config

class LLMError(RuntimeError): pass

class LLMProvider(Protocol):  # from typing import Protocol
    def generate(self, system: str, user: str) -> str: ...

class OpenAICompatProvider:
    def __init__(self, base_url, api_key, model, timeout=60.0, client=None):
        self.model = model
        self._cli = client or OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    def generate(self, system: str, user: str) -> str:
        for attempt in (1, 2):  # 失败自动重试一次
            try:
                resp = self._cli.chat.completions.create(
                    model=self.model, temperature=0,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}])
                return resp.choices[0].message.content
            except (APITimeoutError, APIConnectionError):
                if attempt == 2:
                    raise LLMError("DeepSeek API 连接失败（已重试一次），请检查网络与 DEEPSEEK_API_KEY")
                time.sleep(2)

def make_provider(cfg: Config) -> LLMProvider:
    if not cfg.deepseek_api_key:
        raise LLMError("缺少 DEEPSEEK_API_KEY，请在 .env 配置")
    return OpenAICompatProvider(cfg.deepseek_base_url, cfg.deepseek_api_key, cfg.deepseek_model)
```

- [ ] **Step 4: 全绿；Step 5: 提交** — `git commit -m "feat: LLM Provider 抽象（DeepSeek/Ollama 一键切换+重试）"`

---

### Task 10: executor.py

**Files:**
- Create: `src/aigis/executor.py`
- Test: `tests/test_executor.py`

**Interfaces:**
- Produces:
  - `ExecResult` dataclass：`ok: bool, columns: list[str], rows: list[tuple], error: str`
  - `execute_readonly(sql: str, cfg: Config) -> ExecResult`（连接串含 `options='-c statement_timeout=15000 -c default_transaction_read_only=on'`，autocommit）

- [ ] **Step 1: 集成测试（真库）**

```python
# tests/test_executor.py
import pytest
from aigis.config import Config
from aigis.executor import execute_readonly

@pytest.mark.integration
def test_select_ok():
    r = execute_readonly("SELECT count(*) FROM spatial_ref_sys", Config())
    assert r.ok and r.rows[0][0] > 8000

@pytest.mark.integration
def test_write_denied_and_timeout_error_captured():
    r = execute_readonly("SELECT pg_sleep(20)", Config())  # 超时 15s 拦截
    assert not r.ok and "timeout" in r.error.lower()
```

- [ ] **Step 2: 失败 → Step 3: 实现**

```python
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
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                if cur.description is None:
                    return ExecResult(ok=True)
                return ExecResult(ok=True, columns=[d.name for d in cur.description],
                                  rows=cur.fetchall())
    except psycopg.Error as e:
        return ExecResult(error=str(e).strip())
```

- [ ] **Step 4: 通过（容器在跑）；Step 5: 提交** — `git commit -m "feat: 只读执行器（超时+错误捕获）"`

---

### Task 11: geojson_out.py

**Files:**
- Create: `src/aigis/geojson_out.py`
- Test: `tests/test_geojson_out.py`

**Interfaces:**
- Produces: `rows_to_geojson(columns: list[str], rows: list[tuple]) -> dict`
  - 约定：名为 `geometry` 的列（值为 GeoJSON 字符串或 dict）→ Feature.geometry；其余列 → properties。
  - 无 geometry 列 → 每行 `geometry: None` 的 Feature（保留属性表格语义）。

- [ ] **Step 1: 失败测试**

```python
# tests/test_geojson_out.py
import json
from aigis.geojson_out import rows_to_geojson

PT = '{"type":"Point","coordinates":[116.4,39.9]}'

def test_with_geometry_column():
    fc = rows_to_geojson(["name", "geometry"], [("故宫", PT)])
    assert fc["type"] == "FeatureCollection"
    f = fc["features"][0]
    assert f["geometry"]["type"] == "Point"
    assert f["properties"] == {"name": "故宫"}

def test_geometry_as_dict():
    fc = rows_to_geojson(["geometry"], [(json.loads(PT),)])
    assert fc["features"][0]["geometry"]["type"] == "Point"

def test_without_geometry():
    fc = rows_to_geojson(["count"], [(5,)])
    assert fc["features"][0]["geometry"] is None
    assert fc["features"][0]["properties"] == {"count": 5}
```

- [ ] **Step 2: 失败 → Step 3: 实现**

```python
# src/aigis/geojson_out.py
import json
from typing import Any

def _parse_geom(v: Any):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return None
    return v if isinstance(v, dict) else None

def rows_to_geojson(columns: list[str], rows: list[tuple]) -> dict:
    feats = []
    geom_idx = columns.index("geometry") if "geometry" in columns else None
    prop_cols = [c for c in columns if c != "geometry"]
    for row in rows:
        geom = _parse_geom(row[geom_idx]) if geom_idx is not None else None
        props = dict(zip(prop_cols,
                         (v for i, v in enumerate(row) if i != geom_idx)))
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": feats}
```

- [ ] **Step 4: 通过；Step 5: 提交** — `git commit -m "feat: 行集转 GeoJSON FeatureCollection"`

---

### Task 12: repair.py（自修复回环）

**Files:**
- Create: `src/aigis/repair.py`
- Test: `tests/test_repair.py`

**Interfaces:**
- Consumes: `build_messages`、`make_provider().generate`、`validate`、`execute_readonly`、`rows_to_geojson`
- Produces:
  - `Outcome` dataclass：`question, sql, reasoning, attempts: int, ok: bool, error: str, columns, rows, geojson: dict | None`
  - `run_query(question: str, cfg: Config, max_retries: int = 3) -> Outcome`

- [ ] **Step 1: 失败测试（mock provider 返回坏→好 SQL 序列）**

```python
# tests/test_repair.py
from unittest.mock import MagicMock
import pytest
from aigis.config import Config
from aigis.repair import run_query

BAD = '{"sql":"SELECT * FROM nope","reasoning":"x"}'
GOOD = '{"sql":"SELECT count(*) AS n FROM spatial_ref_sys","reasoning":"ok"}'

@pytest.mark.integration
def test_repair_loop_recovers():
    provider = MagicMock()
    provider.generate.side_effect = [BAD, GOOD, GOOD]  # 第1次坏表名→修复→成功
    cfg = Config()
    out = run_query("有多少坐标系", cfg, provider=provider)
    assert out.ok and out.attempts == 2 and out.rows[0][0] > 8000
    assert out.geojson["features"][0]["properties"]["n"] > 8000

@pytest.mark.integration
def test_gives_up_after_max():
    provider = MagicMock()
    provider.generate.return_value = BAD
    out = run_query("x", Config(), max_retries=2, provider=provider)
    assert not out.ok and out.attempts == 2 and "nope" in out.error or out.error
```

- [ ] **Step 2: 失败 → Step 3: 实现**

```python
# src/aigis/repair.py
import json
from dataclasses import dataclass, field
from aigis.config import Config
from aigis.executor import execute_readonly
from aigis.geojson_out import rows_to_geojson
from aigis.llm import make_provider
from aigis.prompt import build_messages
from aigis.schema_export import DEFAULT_TABLES
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

def _parse_llm_json(text: str) -> tuple[str, str]:
    data = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    return data["sql"], data.get("reasoning", "")

def run_query(question: str, cfg: Config, max_retries: int = 3, provider=None) -> Outcome:
    out = Outcome(question=question)
    provider = provider or make_provider(cfg)
    schema_text = _cached_schema(cfg)
    feedback = ""
    for attempt in range(1, max_retries + 1):
        out.attempts = attempt
        msgs = build_messages(question + (f"\n\n上次尝试失败，信息：{feedback}" if feedback else ""), schema_text)
        raw = provider.generate(msgs[0]["content"], msgs[1]["content"])
        try:
            sql, reasoning = _parse_llm_json(raw)
        except (ValueError, KeyError) as e:
            feedback, out.error = f"输出不是合法 JSON：{e}", f"LLM 输出解析失败: {e}"
            continue
        out.sql, out.reasoning = sql, reasoning
        ok, reason = validate(sql, set(DEFAULT_TABLES))
        if not ok:
            feedback = f"校验未通过：{reason}"
            out.error = feedback
            continue
        result = execute_readonly(sql, cfg)
        if result.ok:
            out.ok, out.columns, out.rows = True, result.columns, result.rows
            out.geojson = rows_to_geojson(result.columns, result.rows)
            return out
        feedback = f"数据库执行错误：{result.error}"
        out.error = feedback
    return out

_SCHEMA_CACHE: dict[str, str] = {}
def _cached_schema(cfg: Config) -> str:
    key = f"{cfg.db_host}:{cfg.db_port}/{cfg.db_name}"
    if key not in _SCHEMA_CACHE:
        from aigis.schema_export import export_schema
        _SCHEMA_CACHE[key] = export_schema(f"host={cfg.db_host} port={cfg.db_port} "
            f"dbname={cfg.db_name} user={cfg.admin_user} password={cfg.admin_password}")
    return _SCHEMA_CACHE[key]
```

- [ ] **Step 4: 通过；Step 5: 提交** — `git commit -m "feat: 自修复回环（校验/执行错误回喂，≤3次）"`

---

### Task 13: cli.py + 冒烟

**Files:**
- Create: `src/aigis/cli.py`
- Modify: `pyproject.toml`（`[project.scripts] aigis = "aigis.cli:app"` 已在 Task 1 定义）

**Interfaces:**
- Produces: `app: typer.Typer`；命令 `aigis "问题" [--out PATH] [--show-sql]`。

- [ ] **Step 1: 实现（typer 薄壳，无单测——逻辑全在 repair）**

```python
# src/aigis/cli.py
import json
from pathlib import Path
import typer
from aigis.config import load_config
from aigis.repair import run_query

app = typer.Typer(add_completion=False, help="AI-GIS 中文空间查询引擎")

@app.command()
def query(
    question: str = typer.Argument(..., help="中文空间问题"),
    out: Path = typer.Option(Path("out/result.geojson"), "--out", "-o"),
    show_sql: bool = typer.Option(False, "--show-sql"),
):
    cfg = load_config()
    result = run_query(question, cfg)
    if show_sql or True:  # 始终打印 SQL，便于核对（README：工具过程可见）
        typer.echo(f"生成的 SQL（尝试 {result.attempts} 次）：\n{result.sql}\n")
    if not result.ok:
        typer.echo(f"查询失败：{result.error}", err=True)
        raise typer.Exit(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.geojson, ensure_ascii=False), encoding="utf-8")
    typer.echo(f"结果 {len(result.rows)} 行，GeoJSON 已写入 {out}")

if __name__ == "__main__":
    app()
```

（typer 单命令 app：`aigis "问题"` 直接生效；若入口解析成子命令，去掉 `@app.command()` 换 `app.command()(query)` 单命令模式——执行者以 `uv run aigis "三环内有多少公园"` 实测为准。）

- [ ] **Step 2: 端到端冒烟（需数据已导入 + DEEPSEEK_API_KEY）**

```bash
uv run aigis "三环内有多少个公园" --show-sql
uv run aigis "距离天安门2公里内有哪些地铁站" --show-sql
uv run aigis "名字包含海淀的行政区" --show-sql
```

预期：3 条均输出 SQL、行数、`out/result.geojson`（可用 QGIS 或 geojson.io 查看）。

- [ ] **Step 3: 提交** — `git commit -m "feat: CLI 命令 aigis（端到端冒烟通过）"`

---

### Task 14: 评估器与 50 题集

**Files:**
- Create: `eval/questions.yaml`、`src/aigis/evaluator.py`
- Modify: `pyproject.toml` scripts 加 `aigis-eval = "aigis.evaluator:app"`

**Interfaces:**
- Produces: `run_eval(questions: list[dict], cfg) -> dict`（键：total, exec_ok, repaired, results）；CLI `aigis-eval [--only-annotated]` 打印报告。

- [ ] **Step 1: 建 50 题集 eval/questions.yaml**

覆盖矩阵（执行者按此创作，每题字段齐全）：

| 类别 | 题数 | 代表题 |
|---|---|---|
| 环内过滤（ST_Contains+ring_areas） | 8 | 三环内有多少公园 |
| 距离查询（ST_DWithin） | 8 | 距天安门2公里内的地铁站 |
| 地名模糊（LIKE） | 6 | 名字包含"中关村"的兴趣点 |
| 面积排序（ST_Area geography） | 6 | 五环内面积最大的三个公园 |
| 行政区（admin_level） | 6 | 海淀区有多少餐厅 |
| 叠加计算（绿化覆盖率类） | 6 | 各区县绿化覆盖率排名 |
| 缓冲区（ST_Buffer） | 4 | 长安街两侧500米内的便利店 |
| 最近邻（ORDER BY ST_Distance LIMIT） | 6 | 离故宫最近的十个加油站 |

条目格式（全部 50 条照此写全，不得留 TODO）：

```yaml
- id: 1
  question: 三环内有多少个公园
  category: 环内过滤
  expected_semantics: ring_areas(三环) ST_Contains osm_areas leisure=park count
  expect_rows_range: [10, 2000]     # 数量级合理性区间，供自动粗检
  human_pass: null                   # 人工标注：结果是否正确
```

- [ ] **Step 2: 失败测试 → Step 3: 实现 evaluator.py**

```python
# src/aigis/evaluator.py
from pathlib import Path
import typer, yaml
from aigis.config import load_config
from aigis.repair import run_query

app = typer.Typer(add_completion=False)

def run_eval(questions: list[dict], cfg, progress=True) -> dict:
    results, repaired = [], 0
    for q in questions:
        out = run_query(q["question"], cfg)
        if out.ok and out.attempts > 1:
            repaired += 1
        in_range = None
        if out.ok and q.get("expect_rows_range") and out.rows:
            lo, hi = q["expect_rows_range"]
            n = out.rows[0][0] if len(out.rows) == 1 and len(out.rows[0]) == 1 else len(out.rows)
            in_range = lo <= n <= hi
        results.append({"id": q["id"], "question": q["question"], "ok": out.ok,
                        "attempts": out.attempts, "sql": out.sql,
                        "rows": len(out.rows), "in_range": in_range,
                        "human_pass": q.get("human_pass")})
        if progress:
            typer.echo(f"[{q['id']}] {'OK' if out.ok else 'FAIL'} 尝试{out.attempts}次 行数{len(out.rows)}")
    annotated = [r for r in results if r["human_pass"] is not None]
    acc = (sum(1 for r in annotated if r["human_pass"]) / len(annotated)) if annotated else None
    return {"total": len(results), "exec_ok": sum(1 for r in results if r["ok"]),
            "repaired": repaired, "accuracy_annotated": acc, "results": results}

@app.command()
def main(only_annotated: bool = typer.Option(False, "--only-annotated")):
    qs = yaml.safe_load(Path("eval/questions.yaml").read_text(encoding="utf-8"))
    if only_annotated:
        qs = [q for q in qs if q.get("human_pass") is not None]
    rep = run_eval(qs, load_config())
    typer.echo(f"\n总计 {rep['total']} | 执行成功 {rep['exec_ok']} | "
               f"经自修复 {rep['repaired']} | 人工标注准确率 {rep['accuracy_annotated']}")

if __name__ == "__main__":
    app()
```

`tests/test_evaluator.py`：mock run_query 返回固定 Outcome，断言 run_eval 统计（exec_ok/repaired/accuracy 计算）正确——**执行者按此意图写 2 个用例**（统计逻辑纯函数测试，不依赖真库/LLM）。

- [ ] **Step 4: 全绿 + 首轮全量评估**（`uv run aigis-eval`，人工标注前先看执行成功率与行数合理性）
- [ ] **Step 5: 提交** — `git commit -m "feat: 50 题评估器（执行成功率/自修复命中率/人工准确率）"`

---

### Task 15: 人工标注、达标验收与文档

**Files:**
- Modify: `eval/questions.yaml`（填 human_pass）
- Modify: `README.md`（Phase 1 状态与使用说明）

- [ ] **Step 1: 人工标注**——逐题核对 SQL 与结果（执行者输出 `eval/report.md`：题号/问题/SQL/前 3 行结果），用户标注 human_pass true/false。
- [ ] **Step 2: 复评** `uv run aigis-eval --only-annotated` → accuracy_annotated ≥ 0.80 为达标；未达标 → 分析错题类别，针对性补 few-shot（data/fewshot.yaml）后复评（此循环即"准确率工程"）。
- [ ] **Step 3: README 更新**（快速开始：启动容器 → 导入 → 配 .env → `uv run aigis "问题"`；当前准确率数字）。
- [ ] **Step 4: 提交** — `git commit -m "docs: Phase 1 验收结果与使用说明（准确率 X%）"`

---

## Self-Review 记录

- **Spec 覆盖**：spec §2 决策→Task 2/3/9；§3 数据层→Task 4/5；§4 引擎→Task 6-12；§5 CLI/评估→Task 13/14；§6 测试→各任务 TDD + Task 13 冒烟；§7 离线→Task 2；§8 风险→Task 5 fallback、Task 8 few-shot、Task 14 补样循环。无缺口。
- **占位符**：fewshot 与 questions.yaml 的省略号均附"执行者必须写全"的硬性要求与格式；prep_rings 中标注了示例错误并给出正确写法。
- **类型一致**：`validate(sql, allowed_tables) -> tuple[bool,str]`、`run_query(question, cfg, max_retries=3, provider=None) -> Outcome`、`rows_to_geojson(columns, rows) -> dict` 在消费方（repair/cli/evaluator）签名一致。
