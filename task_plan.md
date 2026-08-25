# AI-GIS Phase 1 任务计划

> 目标：中文自然语言 → 空间 SQL → PostGIS 只读执行 → GeoJSON 命令行引擎，50 题准确率 ≥80%。
> 依据文档：`docs/superpowers/specs/2026-08-25-spatial-query-engine-design.md`（设计）
> 　　　　　`docs/superpowers/plans/2026-08-25-spatial-query-engine.md`（实现计划）
> 执行方式：子代理驱动（superpowers:subagent-driven-development），分支 feature/phase1-spatial-query

## 阶段

| # | 阶段 | 状态 | 备注 |
|---|------|------|------|
| 0 | 环境准备（Docker/PostGIS/osm2pgsql/uv） | complete | 容器 aigis-postgis 运行中 |
| 1 | 依赖与包骨架 | complete | Task 1 |
| 2 | 本地资源（北京 pbf，Ollama 已裁决撤销） | complete | Task 2 |
| 3 | config.py | complete | Task 3 |
| 4 | OSM 导入管道（flex + 中文 COMMENT + way级POI质心） | complete | Task 4 |
| 5 | 环路多边形预处理（buffer方案，五环全成环） | complete | Task 5 |
| 6 | schema_export.py | complete | Task 6 |
| 7 | validator.py（安全核心，修复quoted绕过后25测试） | complete | Task 7 |
| 8 | prompt.py + few-shot 库（15条，EXPLAIN回归） | complete | Task 8 |
| 9 | llm.py DeepSeek 客户端 | complete | Task 9 |
| 10 | executor.py 只读执行 | complete | Task 10 |
| 11 | geojson_out.py | complete | Task 11 |
| 12 | repair.py 自修复回环 | complete | Task 12 |
| 13 | cli.py（错误路径冒烟验证） | complete | Task 13 |
| 14 | 评估器 + 50 题集（82 passed） | complete | Task 14 |
| 15 | 端到端冒烟 + 50题真跑 + 人工标注 + 达标验收 + README | **blocked** | 等 DeepSeek 充值（402） |
| 16 | 最终全分支审查 + 收尾 | pending | Task 15 后 |

## 关键决策

- LLM：DeepSeek API 直连（用户裁决不用 Ollama，2026-08-25）
- 数据：BBBike 北京城区提取 22MB；osm2pgsql flex 4 表+ring_areas，中文 COMMENT
- 安全：sqlglot 白名单校验（修复 quoted 绕过+黑名单扩容）+ 只读账号 + statement_timeout 三层
- 环路：ST_Polygonize 因数据缝隙失效 → 分级容差 buffer + 最大内洞（偏差 1-15%）

## 遇到的错误

| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| Docker Hub 直连超时 | 1 | daemon.json 配 3 个国内镜像源 |
| osm2pgsql flex 三处 API 与计划不符 | 1 | 按官方手册修正（回调注册/geom_column/is_closed） |
| ST_Polygonize 产出 0 面 | 1 | OSM 分段缝隙数据事实，改 buffer 方案 |
| validator quoted 函数名绕过（Critical） | 1 | f.name.lower() 统一形态 + 对抗测试 |
| 4 条 few-shot SQL 真库报错 | 1 | 别名/numeric强转/LIMIT1 + 15条EXPLAIN回归 |
| Mimosa 拦截 f-string SQL（2处 high） | 1 | 参数化/Identifier 组装 |
| DeepSeek 402 余额不足 | 等待 | 用户充值后继续 Task 15 |
