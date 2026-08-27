# 地图制作能力设计（GIS 编辑通道 + 制图工作台）

> 状态：自主模式（用户目标：AI+GIS 完美结合，地图制作基本功能可用）
> 日期：2026-08-27
> 前置：引擎 96% / Web 操作台 / 流式 / answer 生成（后端已就绪）

## 1. 目标与验收

把"只读查询+渲染"升级为"**可制作的地图工作台**"：

1. **AI 制作指令**：对话输入"把三环内的公园做500米缓冲区生成新图层"→ AI 生成受控建图 SQL → 持久化为用户图层 → 自动加载到地图。
2. **图层持久化**：查询结果可"保存为图层"；图层库面板列出历史图层，可加载/删除（服务重启不丢）。
3. **制图样式编辑**：选中图层可调颜色/透明度/点半径/线宽；按属性列**分类设色**（类别色板）或**数值渐变**（插值色）；选属性列开**标注**。
4. **导出**：当前地图画布导出 PNG。

验收（浏览器实测）：制作指令端到端出新图层并上图；图层库重启后仍在；样式/分类设色/标注实时生效；PNG 落盘；只读数据零污染（osm_* 不可写，写操作仅限 user_layers schema）。

## 2. 安全模型（写通道，核心）

- 新 DB 角色 `aigis_maker`：仅 `CREATE/USAGE ON SCHEMA user_layers` + 该 schema 内表的全部 DML；**public 业务表无任何写权限**（grantee 校验测试）。
- 制作 SQL **模板约束**（make 校验器，独立于只读 validator）：
  - 仅允许单条 `CREATE TABLE user_layers.<name> AS <SELECT...>`；
  - 表名白名单正则 `^[a-z][a-z0-9_]{0,47}$` + 长度 + 保留字拒列；
  - 内层 SELECT 复用只读 validator（表白名单/函数黑名单/单条/禁 CTE 写入）；
  - 拒绝 DROP/TRUNCATE/ALTER/INSERT/UPDATE/DELETE 一切直接 DML（制作=派生新表，不修改）。
- 注册表 `user_layers.registry(layer_name PK, label, sql, feature_count, created_at)`——maker 只 INSERT 该表，删图层走管理账号端点。

## 3. 架构

```
前端地图工作台
 ├ 对话（查询+制作统一入口，后端意图路由）
 ├ 图层卡片（持久图层标记 ♻ / 临时标记）
 ├ 样式编辑抽屉（颜色/透明度/半径/线宽/分类设色/渐变/标注）
 ├ 图层库面板（加载/删除）
 └ 导出 PNG
FastAPI
 ├ GET /api/query/stream（已有；加意图路由→制作流）
 ├ POST /api/layers/save {geojson,label,name?}（临时结果→持久图层）
 ├ GET /api/layers（registry 列表）/ GET /api/layers/{name}/geojson
 ├ DELETE /api/layers/{name}（管理账号）
 └ POST /api/make/stream（SSE：status/delta/result——与查询流同构）
引擎（repair 扩展）
 └ run_make_task(question,cfg)->Outcome：制作 prompt（few-shot：缓冲/裁剪/合并/相交/质心/简化 样例）
    → make 校验 → maker 执行 → registry 注册 → 新图层统计+GeoJSON 采样返回
```

## 4. 意图路由（轻量）

`/api/query/stream` 入口判别 question：含制作关键词（做成/生成.*图层|保存为图层|新建图层|缓冲.*图层|叠加.*图层）→ 转制作流；否则查询流。误判兜底：制作流失败时 SSE 提示可用查询表述重试。不引入 LLM 意图分类（YAGNI）。

## 5. 前端要点

- MapPanel 增强：`layer.style`（color/opacity/radius/width/classify{column,palette}/gradient{column}/label{column}）→ 数据驱动 paint 表达式重建子图层；样式编辑实时生效。
- 持久图层加载：registry GeoJSON 端点 → 与查询图层同管线渲染；卡片 ♻ 标记 + 删除调 DELETE。
- PNG 导出：map 实例 `preserveDrawingBuffer:true` + 按钮 `canvas.toDataURL('image/png')` 下载。
- answer（上一 spec）的 ChatPanel 重构同步落地：默认 answer 文本+大数字卡片，SQL/推理/表格入"详情"。

## 6. 测试

- maker 权限矩阵（业务表写拒/user_layers 写许）；make 校验矩阵（合法 CTAS 通过/DML 拒/表名非法拒/内层 SELECT 违规拒）；run_make_task 端到端（mock provider 返回固定 CTAS → 真 user_layers 建表→注册→清理）；意图路由单测；图层 CRUD API 单测。前端浏览器实测。
