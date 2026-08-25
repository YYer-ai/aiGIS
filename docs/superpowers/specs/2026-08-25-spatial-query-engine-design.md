# Phase 1 设计文档：spatial_query 引擎（中文 → 空间 SQL → PostGIS → GeoJSON）

> 状态：待用户审核
> 日期：2026-08-25
> 对应 README：Phase 1（含金量本体，预估 3-4 周）

## 1. 背景与目标

构建项目核心引擎：用户用中文提问（如"三环内绿化覆盖率最高的五个街区"），系统组装 schema 上下文与空间函数 few-shot，调用 LLM 生成 PostGIS 空间 SQL，经安全校验后以只读账号执行，结果转为 GeoJSON 输出。

**验收标准**（同 README）：
- 命令行可跑：`uv run aigis "<中文问题>"` 输出 SQL、结果摘要与 GeoJSON 文件。
- 自建 50 条中文测试题，SQL 生成准确率 ≥80%（人工标注期望语义，评估器统计）。

**范围裁剪（YAGNI）**：Phase 1 不做 LangGraph、MCP、FastAPI/WebSocket、向量库——全部留待 Phase 2/3。

## 2. 已确认决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 主力 LLM | DeepSeek API | 代码能力强、价格低、OpenAI 兼容 SDK |
| 离线备份 LLM | Ollama + qwen2.5-coder:7b（约 4.7GB） | 断网时开发不断档，CPU 可推理 |
| schema 上下文 | 全量 schema 注入 prompt | Phase 1 仅 5-6 张表（4 张 OSM 表 + ring_areas 等），零 RAG 依赖；后续表多再升级向量库 |
| 测试数据城市 | 北京 | README 示例场景（"三环内"），pbf 约 150MB |
| OSM 导入模式 | osm2pgsql flex（自定义 lua） | 列名语义化 + 中文 COMMENT，LLM 可直接读懂 |
| 离线韧性 | 全部件本地化 | 数据/模型/工具/镜像全部落盘，仅 DeepSeek 调用需网络 |

## 3. 数据层设计

### 3.1 OSM 导入（flex 模式）

数据源：Geofabrik 中国裁剪的北京区域 pbf，下载至 `data/beijing-latest.osm.pbf` **永久本地保存**。导入脚本 `scripts/import_osm.ps1` 只读本地文件，断网可重复执行。

产出 4 张语义化表（PostGIS，SRID 4326）：

| 表 | 内容 | 关键列（除 id/geom/name 外） |
|---|---|---|
| `osm_pois` | 兴趣点（点） | amenity, shop, tourism, cuisine, opening_hours |
| `osm_roads` | 道路（线） | highway, ref, maxspeed, oneway |
| `osm_areas` | 面状地物（面） | leisure, landuse, natural, building, area |
| `osm_boundaries` | 行政区划（面） | admin_level, boundary, name:zh |

- 常用标签提升为真实列，全部字段与表加**中文 COMMENT**（`COMMENT ON TABLE/COLUMN`）。
- `name` 保留中文名（OSM 中文标注优先，fallback name）。

### 3.2 环路多边形（"三环内"语义）

预处理脚本 `scripts/prep_rings.py`：
- 从 `osm_roads` 提取环路闭合线（`name LIKE '%三环%'` 等，含二环至六环）。
- `ST_Polygonize` 生成环内面，落表 `ring_areas`（ring_name, ring_level, geom），加中文注释。
- 查询"三环内"即 `ST_Contains(r_areas.geom, x.geom)`；若 `ring_areas` 缺失该环路，执行阶段报 relation 不存在，由自修复回环提示 LLM 改用对环路名做 `ST_DWithin` 缓冲区近似。

### 3.3 街区与绿化覆盖率（SQL 可计算的明确定义）

- **街区**：优先 `osm_boundaries` 中 `admin_level=8/9/10`（街道办/乡镇）；若 OSM 覆盖不全，降级 `admin_level=6`（区县）并在测试题标注。预处理脚本统计各级覆盖数后选择。
- **绿化覆盖率**：`osm_areas` 中 `leisure IN ('park','garden') OR landuse IN ('grass','forest','meadow') OR natural='wood'` 的面与街区面 `ST_Intersection` 面积 / 街区面积。定义写入 schema 注释与 few-shot，保证 LLM 口径一致。

## 4. 引擎流程设计

```
中文问题
  → 上下文组装：全量 schema（含中文 COMMENT + 每表 3 行样本值）+ 空间函数 few-shot（~15 条）
  → LLM 生成（JSON 输出：{sql, reasoning}）
  → 校验器 validator
  → aigis_readonly 只读执行（双保险）
  → 失败：PG 错误信息回喂 LLM 自修复（上限 3 次）
  → 结果 → GeoJSON FeatureCollection（ST_AsGeoJSON + 属性平铺）
```

### 4.1 模块划分（`src/aigis/`）

| 模块 | 职责 | 依赖 |
|---|---|---|
| `config.py` | 读 `.env`（DB 连接、LLM key、provider 切换） | dotenv |
| `schema_export.py` | 从库导出全量 schema 上下文（DDL+注释+样本值） | psycopg |
| `prompt.py` | 系统提示词 + few-shot 库（`data/fewshot.yaml`）组装 | — |
| `llm.py` | Provider 抽象：DeepSeek / Ollama，OpenAI 兼容协议 | openai SDK |
| `validator.py` | SQL 安全与合法性校验 | sqlglot |
| `executor.py` | 只读执行 + 错误分类 | psycopg |
| `geojson_out.py` | 行集 → GeoJSON FeatureCollection | psycopg/shapely |
| `repair.py` | 自修复回环（错误回喂重生成，≤3 次） | llm/validator/executor |
| `cli.py` | 命令行入口（typer） | typer |

### 4.2 校验规则（validator，单测覆盖）

- sqlglot 解析失败 → 拒绝；仅允许**单条 SELECT**（含 CTE）；禁止 DML/DDL/COPY/pg_sleep/pg_read_file 等一切非查询语句与函数。
- 引用的表必须存在于 schema 白名单；拒绝多语句（`;` 分隔的第二条语句）。
- 兜底：执行一律走 `aigis_readonly` 只读账号（已验证 CREATE 被拒）。

### 4.3 LLM Provider 抽象

- 统一 OpenAI 兼容接口；`.env` 中 `LLM_PROVIDER=deepseek|ollama` 一键切换。
- DeepSeek：`https://api.deepseek.com`，模型 `deepseek-chat`。
- Ollama：`http://localhost:11434/v1`，模型 `qwen2.5-coder:7b`（准备阶段预下载）。
- 网络/超时异常：清晰报错并自动重试 1 次。

## 5. CLI 与评估

- `uv run aigis "三环内绿化覆盖率最高的五个街区"` → 终端打印生成的 SQL 与结果行数摘要，写 `out/result.geojson`。
- 评估：`eval/questions.yaml`（50 条中文题 + 期望语义标签 + 人工判定结果集是否正确的字段），`uv run aigis-eval` 输出报告：执行成功率、SQL 语义准确率、自修复命中率。

## 6. 测试与错误处理

- pytest 单测：validator（危险 SQL 拦截矩阵）、geojson_out（几何/属性转换）、prompt 组装（上下文完整性）、prep_rings（多边形生成）。
- 端到端冒烟（导入数据后）：3 条典型题——三环内查询 / ST_DWithin 距离查询 / 中文名模糊匹配。
- 所有外部失败（LLM 超时、DB 断连）给出面向用户的明确错误信息；引擎自身异常不打断 CLI，以非零码退出。

## 7. 离线韧性（本次用户约束）

| 部件 | 本地化状态 |
|---|---|
| PostGIS 镜像 + 容器 | ✅ 已拉取（postgis/postgis:16-3.5） |
| osm2pgsql 2.3.1 便携版 | ✅ 已在 tools/ |
| 北京 pbf 数据 | ⬜ 准备阶段下载至 data/（一次性） |
| Ollama + qwen2.5-coder:7b | ⬜ 准备阶段安装/下载（一次性） |
| Python 依赖 | ✅ uv 本地缓存（.venv） |
| 需联网环节 | 仅 DeepSeek API；断网切 Ollama |

## 8. 风险与对策

- OSM 北京街道级边界覆盖不全 → 降级区县级，测试题如实标注（见 3.3）。
- LLM 生成 hstore 语法（flex 后无 hstore）→ few-shot 全部用扁平列示例，schema 注释强调。
- 环路数据缺失/不闭合 → ring_areas 预处理时校验并报告；缺失环路在 prompt 中注明不可用。
- 中文地名变体（"海淀区"/"海淀"）→ 样本值注入 prompt + few-shot 用 `name LIKE '%海淀%'` 风格。
