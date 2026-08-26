# Web 操作台设计文档（Phase 2 首个交付：查询对话 + 地图渲染）

> 状态：待用户审核
> 日期：2026-08-26
> 前置：Phase 1 引擎已合并 main（50 题 94% 达标）

## 1. 目标与范围

把 Phase 1 的 CLI 引擎包装为本地 Web 操作台：浏览器里提问 → 展示生成的 SQL/尝试次数/结果摘要 → 地图画布渲染 GeoJSON 图层，支持多查询叠加。

**裁剪（YAGNI，后续迭代）**：多轮对话记忆、WebSocket 流式、工具调用过程可视化、LangGraph/MCP、用户鉴权——均不在本 spec。

**验收标准**：
- `uv run uvicorn aigis_web.app:app` 一条命令启动，浏览器 `http://localhost:5173`（Vite dev）或 `http://localhost:8000`（静态托管产物）可用。
- 双栏交互：提问"三环内有多少个公园"→ 左栏显示 SQL 与行数、右栏渲染结果图层（有几何时）。
- 多图层叠加：至少 3 次查询的图层可独立开关/删除。
- 计数类无几何结果不渲染地图，仅显示数值摘要。

## 2. 架构

```
React + Vite + MapLibre GL JS（前端，dev 端口 5173）
   │  POST /api/query {question}
   ▼
FastAPI（src/aigis_web/app.py，端口 8000）
   │  复用 run_query(question, cfg)
   ▼
Phase 1 引擎（validator → 只读执行 → GeoJSON）
```

- 前后端分离开发：Vite dev server 代理 `/api` → 8000；生产模式 FastAPI 托管 `web/dist` 静态产物。
- 无状态：每次查询独立 HTTP 请求，会话状态（图层列表）仅存前端。

## 3. 后端（`src/aigis_web/`）

| 文件 | 职责 |
|---|---|
| `app.py` | FastAPI 实例；`POST /api/query`；静态托管（dist 存在时）；CORS（dev 5173） |
| `schemas.py` | Pydantic 响应模型 QueryResponse{sql, reasoning, attempts, ok, error, row_count, columns, geojson} |

- `POST /api/query`：load_config → run_query → 序列化。LLMError/OperationalError 返回 502 + 中文错误消息（不 traceback）；question 为空 422。
- 超时兜底：LLM 300s + statement_timeout 15s 已有；uvicorn 默认不设额外超时。
- 依赖：`fastapi、uvicorn[standard]`（uv add）。

## 4. 前端（`web/`，React 18 + Vite 5 + MapLibre GL JS）

| 文件 | 职责 |
|---|---|
| `web/src/App.jsx` | 双栏布局（左 40% 对话 / 右 60% 地图） |
| `web/src/api.js` | fetch 封装 POST /api/query |
| `web/src/ChatPanel.jsx` | 消息流：用户问题、SQL 代码块（可折叠）、尝试次数、行数/错误；输入框+发送 |
| `web/src/MapPanel.jsx` | MapLibre 地图（北京中心 [116.4,39.9]，zoom 10，深色底图 raster 瓦片 OSM）；图层卡片列表：每查询一个 source+layer，开关/删除 |
| `web/src/main.jsx`、`index.html`、`vite.config.js`、`package.json` | 入口与 Vite 代理配置（/api→8000） |

- GeoJSON 渲染：point 用 circle、linestring 用 line、polygon 用 fill+line 描边，随机色区分图层。
- 底图：OSM raster 瓦片（无 key）；断网时地图空白但查询功能不受影响。
- 图层上限 10 个，超出提示删除旧图层（防内存膨胀）。

## 5. 测试

- 后端 pytest：`tests/test_web.py`——TestClient mock run_query（成功/失败/空问题三态，断言响应结构与错误码）。
- 前端不设自动化测试（规模小，人工验收清单代替）。

## 6. 风险

- MapLibre 底图瓦片海外加载慢 → 备选高德 raster 瓦片 URL（https://webst*.is.autonavi.com，个人开发可用），实现做成常量可切换。
- LLM 响应 12-20s → 前端发送后按钮 loading 态 + "思考中…"提示，不做流式。
