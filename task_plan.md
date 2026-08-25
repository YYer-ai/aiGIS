# AI-GIS Phase 1 任务计划

> 目标：中文自然语言 → 空间 SQL → PostGIS 只读执行 → GeoJSON 命令行引擎，50 题准确率 ≥80%。
> 依据文档：`docs/superpowers/specs/2026-08-25-spatial-query-engine-design.md`（设计）
> 　　　　　`docs/superpowers/plans/2026-08-25-spatial-query-engine.md`（实现计划，含全部代码与 TDD 步骤）

## 阶段

| # | 阶段 | 状态 | 对应计划任务 |
|---|------|------|-------------|
| 0 | 环境准备（Docker/PostGIS/osm2pgsql/uv） | complete | （已完成，容器 aigis-postgis 运行中） |
| 1 | 依赖与包骨架 | pending | Task 1 |
| 2 | 本地资源（北京 pbf + Ollama 模型） | pending | Task 2 |
| 3 | config.py | pending | Task 3 |
| 4 | OSM 导入管道（flex + 中文 COMMENT） | pending | Task 4 |
| 5 | 环路多边形预处理 | pending | Task 5 |
| 6 | schema_export.py | pending | Task 6 |
| 7 | validator.py（安全核心） | pending | Task 7 |
| 8 | prompt.py + few-shot 库 | pending | Task 8 |
| 9 | llm.py Provider 抽象 | pending | Task 9 |
| 10 | executor.py 只读执行 | pending | Task 10 |
| 11 | geojson_out.py | pending | Task 11 |
| 12 | repair.py 自修复回环 | pending | Task 12 |
| 13 | cli.py + 端到端冒烟 | pending | Task 13 |
| 14 | 评估器 + 50 题集 | pending | Task 14 |
| 15 | 人工标注、达标验收、文档 | pending | Task 15 |

## 关键决策

- LLM：DeepSeek 主力（.env: DEEPSEEK_API_KEY 待用户填）+ Ollama qwen2.5-coder:7b 离线备份
- 数据：北京 pbf 本地保存 data/；osm2pgsql flex 4 表 + 中文 COMMENT
- 安全：sqlglot 白名单校验 + aigis_readonly 只读账号双保险；statement_timeout=15s
- 几何输出约定：ST_AsGeoJSON(geom) AS geometry 列

## 遇到的错误

| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| Docker Hub 直连超时 | 1 | daemon.json 配 3 个国内镜像源后成功 |
| docker pull 找不到 credential helper | 1 | PATH 前置 Docker bin 目录 |
| osm2pgsql.org 下载慢（~1MB/min） | 1 | 等待完成即可，无需处理 |
