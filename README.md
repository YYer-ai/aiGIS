# AI-GIS 个人项目：中文自然语言空间查询 + GeoAI Agent 对话平台

> 一句话定位：用户用中文提问（如"三环内绿化覆盖率最高的五个街区"），系统自动生成 PostGIS 空间 SQL 执行，结果渲染到地图，并可通过 Agent 调用 QGIS 分析能力——一个"查询 + 操作"集成的 GIS 对话平台。

---

## 一、背景调研结论（2026-08）

### 1. AI × GIS 主要结合方向

| 方向 | 典型做法 | 个人可行性 |
|---|---|---|
| LLM 智能体操作 GIS | MCP 让 LLM 控制 QGIS 加载图层、执行分析 | 高（2025-2026 热点） |
| 自然语言查空间数据 | Text-to-Spatial-SQL：LLM 生成 PostGIS 查询并渲染 | 高（蓝海，本项目的核心） |
| 遥感影像 AI 分析 | SAM 分割、地物分类、变化检测 | 中高（GeoAI 插件已封装好） |
| GeoAI 空间建模 | GNN 空间预测（房价、交通、人口） | 中（偏研究） |

### 2. GitHub 定量对比（选型依据）

| 方向 | 相关仓库数 | 头部项目 | 竞争判断 |
|---|---|---|---|
| SAM 遥感分割 | 27（泛 GeoAI 池 1642） | opengeos/geoai 3322★ | 红海 |
| QGIS MCP | 71 | jjsantos01/qgis_mcp 1070★（已停更） | 中度拥挤 |
| **自然语言→PostGIS** | **25**（过半为 0-3★ 课程级项目） | golem 200★（非完整应用） | **明显蓝海** |

### 3. 蓝海成立的三个依据

1. **通用 Text-to-SQL 巨头全部不覆盖空间 SQL**：vanna（2.3万★）、WrenAI、Chat2DB 均不支持 `ST_DWithin`、`ST_Intersects` 等空间函数，真实技术空白。
2. **中文场景双重空白**：中文地名模糊匹配（"海淀区"、"三环内"）+ 行政区划语义 + 空间 SQL 生成，无成熟开源项目。
3. **学术界刚确认需求**：2026 年 GeoSQL-Eval（Expert Systems with Applications）专门评测 LLM 在 PostGIS 自然语言转 SQL 上的能力——需求已确认，工程实现空白。

## 二、PostGIS 与 QGIS 的角色定位（主次关系，非二选一）

| 维度 | PostGIS（主干引擎） | QGIS（工具 + 逃生舱） |
|---|---|---|
| LLM 生成产物 | 空间 SQL（语法封闭、可校验、可自修复） | PyQGIS 代码（不可控，仅 headless 降级使用） |
| 安全 | 只读账号 + 权限控制，天然沙箱 | — |
| 性能 | GiST 空间索引，千万级要素 | 桌面进程，大数据易卡 |
| 服务化 | 天然后端 | PyQGIS 服务化坑多 |
| 分析上限 | SQL 写不了水文/网络分析/地统计 | processing 工具箱几百个算法（v2 扩展点） |

**分工**：
- QGIS：数据管理（导入清洗 OSM）、开发调试（人工核对查询结果）、后期重型分析通道（`qgis_process` 无界面模式）。
- PostGIS：查询引擎主干，项目含金量本体。
- 注意：**不做纯 QGIS 插件形态**——会滑回"QGIS MCP"红海赛道，且桌面插件传播成本高、核心能力被绑死。

## 三、总体架构

```
┌─────────────── 对话平台（Web）───────────────┐
│  左栏：对话流（含工具调用过程可视化）              │
│  右栏：MapLibre GL 地图画布（图层卡片渲染）      │
└──────────────────┬───────────────────────────┘
                   │ WebSocket（流式）
┌──────────────────▼───────────────────────────┐
│  Agent 编排层  FastAPI + LangGraph            │
│  意图路由 → 工具调用循环 → 多轮记忆              │
├──────────────────────────────────────────────┤
│  工具层（封装成 MCP Server，一份工具多处复用）    │
│  · spatial_query  NL→SQL→PostGIS→GeoJSON     │
│  · run_analysis   重型分析→qgis_process       │
│  · render_layer   推图层到前端地图             │
│  · load_data      OSM/文件导入                │
├──────────────────────────────────────────────┤
│  RAG 层                                       │
│  · 空间 schema 向量库（表/字段/样本值）          │
│  · 空间函数 few-shot 库（中文意图→ST_* 样例）   │
├──────────────────────────────────────────────┤
│  执行层：PostGIS（查询）+ QGIS headless（分析） │
└──────────────────────────────────────────────┘
```

### "查询"与"操作"的统一方式

Agent 层做意图路由，两类能力走同一个循环：

- 查询类（"三环内绿化率最高的街区"）→ `spatial_query`（RAG 检索 → 生成 SQL → 只读执行）→ `render_layer`。
- 操作类（"对结果做 500 米缓冲区并叠加地铁站点"）→ SQL 能做则直接做；做不了（网络分析/插值）→ `run_analysis` 调 `qgis_process`。
- 后期可通过现成 qgis_mcp 接入 QGIS 桌面端，共用同一个 MCP 工具集——两个方向在此汇合。

## 四、技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| Agent 框架 | LangGraph（Python） | 状态机式编排可控，checkpointer 做多轮记忆 |
| 工具协议 | MCP（FastMCP） | 一份工具服务 Web / QGIS / AI 客户端 |
| RAG 向量库 | chromadb 或 sqlite-vec | 个人项目规模够用 |
| Embedding | bge-m3 | 中文效果好，可本地 |
| LLM | 自建 Qwen3.8-27B API（OpenAI 兼容），端点可配置切换 | 生成 SQL 属代码任务，选代码强的模型 |
| 后端 | FastAPI + WebSocket | 流式输出回复与工具状态 |
| 前端 | React + Vite + MapLibre GL JS | 双栏：聊天流 + 地图画布；大数据可加 deck.gl |
| 空间数据库 | PostgreSQL + PostGIS（Docker） | GPL v2 开源，行业标准 |
| 数据源 | OSM（osm2pgsql 入库）+ 政府开放数据 | 免费覆盖全国 |

## 五、快速开始

1. 起库：`docker compose up -d`（PostgreSQL + PostGIS）
2. 导入数据：`pwsh scripts/import_osm.ps1`（OSM 城区裁剪包入库），再 `uv run python scripts/prep_rings.py` 生成二环~六环面
3. 配置 LLM：`.env` 中设置 `LLM_API_KEY`（OpenAI 兼容端点）
4. 提问：`uv run aigis "三环内有多少个公园"`
5. 评估：`uv run aigis-eval`

## 六、演进路线（不摊大饼）

| 阶段 | 内容 | 预估 | 验收标准 |
|---|---|---|---|
| **Phase 1（含金量本体）** | 只做 `spatial_query` 引擎：中文 → schema RAG → 空间 SQL → 只读执行 → GeoJSON | 3-4 周 | 命令行可跑；自建 50 条中文测试问题，SQL 生成准确率达标（目标 ≥80%）✅ 完成（2026-08-26）：50 题执行成功率 100%，语义准确率 94%（目标 ≥80%），自修复命中 2 题；错题 3 个均系数据覆盖（城区裁剪），非引擎错误 |
| **Phase 2** | MCP 封装 + LangGraph 循环 + Web 双栏对话平台 | 约 1 个月 | 对话加载图层到地图；工具调用过程可视化 |
| **Phase 3** | `qgis_process` 重型分析、QGIS 桌面端（qgis_mcp）、多模态（AI 看地图截图续聊） | 按需 | 缓冲区/网络分析类指令端到端跑通 |

> 原则：引擎不准，上层全是空壳。Phase 1 的准确率工程（schema RAG、空间函数 few-shot、错误自修复回环）是整个项目的护城河。

## 七、参考项目

- [opengeos/geoai](https://github.com/opengeos/geoai) — GeoAI 工具库与 QGIS 插件（SAM 3、树木/水体提取）
- [jjsantos01/qgis_mcp](https://github.com/jjsantos01/qgis_mcp) — QGIS 的 MCP Server（已停更，可参考实现）
- [nkarasiak/qgis-mcp](https://github.com/nkarasiak/qgis-mcp) — 活跃的 QGIS MCP 实现
- [anitagraser/qgis_mcp](https://github.com/anitagraser/qgis_mcp) — QGIS 接本地 Ollama（离线方案）
- [opengeos/GeoAgent](https://github.com/opengeos/GeoAgent) — 多模态 GeoAI Agent
- [vanna-ai/vanna](https://github.com/vanna-ai/vanna) — Text-to-SQL 代表作（对照其空间函数盲区）
- [PostGIS](https://github.com/postgis/postgis) — GPL v2，OSGeo 旗舰项目
