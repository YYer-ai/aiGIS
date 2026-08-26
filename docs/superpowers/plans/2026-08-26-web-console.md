# Web 操作台实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FastAPI 包装 Phase 1 引擎 + React/MapLibre 双栏操作台：提问 → SQL/结果摘要 → 地图 GeoJSON 图层叠加。

**Architecture:** 前后端分离：`src/aigis_web/`（FastAPI，8000 端口，POST /api/query 复用 run_query）+ `web/`（Vite dev 5173 代理 /api，生产由 FastAPI 托管 dist）。无状态，图层状态仅在前端。

**Tech Stack:** fastapi、uvicorn[standard]、pytest（后端）；React 18、Vite 5、maplibre-gl（前端，npm）。

**Spec:** `docs/superpowers/specs/2026-08-26-web-console-design.md`

## Global Constraints

- 后端执行账号 aigis_readonly（Config 默认），LLM 端点/密钥来自 `.env`（llm_* 键）
- 错误处理：LLMError/OperationalError → HTTP 502 + 中文消息；空 question → 422；不 traceback
- 前端地图中心 [116.4, 39.9] zoom 10；底图 OSM raster，高德瓦片 URL 做常量备选
- 图层上限 10；计数类无几何结果不渲染仅摘要
- 提交信息中文；后端测试 pytest（TestClient + mock），前端人工验收
- Node v24.14.1 已装；npm 源若慢可用 `--registry=https://registry.npmmirror.com`

---

### Task 1: 后端 API（TDD）

**Files:**
- Modify: `pyproject.toml`（dependencies 加 fastapi、uvicorn[standard]；dev 组加 httpx——TestClient 依赖）
- Create: `src/aigis_web/__init__.py`、`src/aigis_web/app.py`、`src/aigis_web/schemas.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `create_app() -> FastAPI`（app.py 暴露模块级 `app = create_app()`）；`POST /api/query` 请求 `{question: str}` 响应 `QueryResponse{sql, reasoning, attempts, ok, row_count, columns, geojson, error}`（ok=False 时 502 且 body 含 error 字段）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web.py
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from aigis_web.app import create_app

def _mock_outcome(ok=True, rows=None, geojson=None, error=""):
    m = MagicMock()
    m.ok = ok; m.sql = "SELECT 1"; m.reasoning = "r"; m.attempts = 1
    m.rows = rows or []; m.columns = ["count"]; m.geojson = geojson; m.error = error
    return m

def test_query_ok():
    with patch("aigis_web.app.run_query", return_value=_mock_outcome(rows=[(292,)], geojson={"type": "FeatureCollection", "features": []})):
        c = TestClient(create_app())
        r = c.post("/api/query", json={"question": "三环内有多少个公园"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] and body["row_count"] == 1 and body["sql"] == "SELECT 1"

def test_query_llm_error_502():
    from aigis.llm import LLMError
    with patch("aigis_web.app.run_query", side_effect=LLMError("缺少 LLM_API_KEY")):
        c = TestClient(create_app())
        r = c.post("/api/query", json={"question": "x"})
        assert r.status_code == 502 and "LLM_API_KEY" in r.json()["error"]

def test_query_db_error_502():
    import psycopg
    with patch("aigis_web.app.run_query", side_effect=psycopg.OperationalError("conn refused")):
        c = TestClient(create_app())
        r = c.post("/api/query", json={"question": "x"})
        assert r.status_code == 502 and "数据库" in r.json()["error"]

def test_empty_question_422():
    c = TestClient(create_app())
    r = c.post("/api/query", json={"question": "  "})
    assert r.status_code == 422
```

- [ ] **Step 2: 跑测试确认失败** — `uv run pytest tests/test_web.py -v` → ImportError

- [ ] **Step 3: 实现**

```python
# src/aigis_web/schemas.py
from pydantic import BaseModel

class QueryRequest(BaseModel):
    question: str

class QueryResponse(BaseModel):
    sql: str = ""
    reasoning: str = ""
    attempts: int = 0
    ok: bool = False
    row_count: int = 0
    columns: list[str] = []
    geojson: dict | None = None
    error: str = ""

# src/aigis_web/app.py
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from aigis.config import load_config
from aigis.llm import LLMError
from aigis.repair import run_query
from aigis_web.schemas import QueryRequest, QueryResponse
import psycopg

def create_app() -> FastAPI:
    app = FastAPI(title="AI-GIS 操作台")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.post("/api/query")
    def query(req: QueryRequest):
        if not req.question.strip():
            return JSONResponse(status_code=422, content={"error": "问题不能为空"})
        try:
            out = run_query(req.question.strip(), load_config())
        except LLMError as e:
            return JSONResponse(status_code=502, content=QueryResponse(error=str(e)).model_dump())
        except psycopg.OperationalError as e:
            return JSONResponse(status_code=502,
                content=QueryResponse(error=f"数据库连接失败，请确认 aigis-postgis 容器在运行：{e}").model_dump())
        return QueryResponse(sql=out.sql, reasoning=out.reasoning, attempts=out.attempts,
                             ok=out.ok, row_count=len(out.rows), columns=out.columns,
                             geojson=out.geojson, error=out.error)

    dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="static")
    return app

app = create_app()
```

- [ ] **Step 4: 全绿 + uv sync 装依赖** — `uv run pytest`（98 基线 + 4 新增）
- [ ] **Step 5: 提交** — `git commit -m "feat: Web 后端 API（POST /api/query 复用引擎）"`

---

### Task 2: 前端骨架（Vite + React）

**Files:**
- Create: `web/package.json`、`web/vite.config.js`、`web/index.html`、`web/src/main.jsx`、`web/src/App.jsx`（占位双栏）、`web/src/api.js`、`web/src/styles.css`

**Interfaces:**
- Produces: `npm run dev`（5173）可打开占位页；`api.js` 暴露 `postQuery(question) -> Promise<object>`（失败 throw Error(中文)）

- [ ] **Step 1: 初始化文件**

```json
// web/package.json
{
  "name": "aigis-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build" },
  "dependencies": { "maplibre-gl": "^5.0.0", "react": "^18.3.0", "react-dom": "^18.3.0" },
  "devDependencies": { "@vitejs/plugin-react": "^4.3.0", "vite": "^5.4.0" }
}
```

```js
// web/vite.config.js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
```

```html
<!-- web/index.html -->
<!doctype html>
<html lang="zh">
<head><meta charset="UTF-8"/><title>AI-GIS 操作台</title>
<link href="/node_modules/maplibre-gl/dist/maplibre-gl.css" rel="stylesheet"/></head>
<body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body>
</html>
```

```jsx
// web/src/main.jsx
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
createRoot(document.getElementById("root")).render(<App />);
```

```js
// web/src/api.js
export async function postQuery(question) {
  const r = await fetch("/api/query", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `请求失败(${r.status})`);
  return body;
}
```

- [ ] **Step 2: 安装并启动验证**

```bash
cd web && npm install --registry=https://registry.npmmirror.com
npm run dev &   # 后台起，curl http://localhost:5173 应返回 HTML
```

- [ ] **Step 3: .gitignore 追加 `web/node_modules/` 与 `web/dist/`；提交** — `git commit -m "feat: 前端骨架（Vite+React+/api 代理）"`

---

### Task 3: ChatPanel 对话流

**Files:**
- Create: `web/src/ChatPanel.jsx`
- Modify: `web/src/App.jsx`（双栏布局挂 ChatPanel/MapPanel 占位）、`web/src/styles.css`

**Interfaces:**
- Consumes: `postQuery`（api.js）
- Produces: `<ChatPanel onResult={(res) => void}/>`——每次查询成功后把 QueryResponse 交给父组件（供地图加图层）

- [ ] **Step 1: 实现 ChatPanel**——消息列表 state：{role: 'user'|'assistant', text, res?}；发送流程：追加用户消息 → loading 态（输入框禁用+「思考中…」）→ postQuery → 追加助手消息（SQL 代码块 + `<details>` 折叠 reasoning + 尝试次数 + 行数/错误）→ onResult(res)。ok=false 时助手消息显示红色错误。
- [ ] **Step 2: App.jsx 双栏**——flex 布局：左 40% ChatPanel、右 60% MapPanel（本任务占位 div）；styles.css 写最小可用样式（深色主题可选，保持简单）。
- [ ] **Step 3: 手动验证**——起后端（uv run uvicorn aigis_web.app:app --port 8000）+ 前端 dev，发一条问题看消息流（允许真实 LLM 调用）；截图或 DOM 描述记入报告。
- [ ] **Step 4: 提交** — `git commit -m "feat: 对话面板（消息流/SQL折叠/loading态）"`

---

### Task 4: MapPanel 地图画布

**Files:**
- Create: `web/src/MapPanel.jsx`

**Interfaces:**
- Consumes: 父组件传入 `layers: [{id, name, geojson, visible}]` 与 `onToggle(id)`、`onRemove(id)`
- Produces: MapLibre 地图 + 图层卡片列表 UI

- [ ] **Step 1: 实现地图**——useRef 挂容器，useEffect 初始化 `new maplibregl.Map({container, style: {version:8, sources:{osm:{type:'raster',tiles:[OSM_TILE_URL],tileSize:256}}, layers:[{id:'osm',type:'raster',source:'osm'}]}, center:[116.4,39.9], zoom:10})`；`const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"` 与备选高德 `https://webrd04.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}` 常量并列（默认 OSM，注释切换）。
- [ ] **Step 2: 图层同步**——useEffect 监听 layers：对每个 layer，source id `lyr-${id}`；按 geojson 首个 feature 几何类型加 circle/line/fill(+line outline) 三个 layer（id `lyr-${id}-pt/-ln/-pg`）；visible=false 时 setLayoutProperty visibility none；fitBounds 到图层 bbox（ turf 不引入，用 geojson 遍历坐标算 bbox 简版）。移除时 removeLayer/removeSource。随机色 `hsl(${Math.random()*360},70%,60%)` 存入 layer 对象。
- [ ] **Step 3: 图层卡片列表**——右上角浮层：每图层一行（色点+名称+行数+可见开关+删除按钮）；上限 10 由父组件控制。
- [ ] **Step 4: 手动验证**——连续查 3 条有几何的问题（如"名字包含中关村的兴趣点"“五环内面积最大的三个公园”“名字包含长安街的道路”），确认图层叠加/开关/删除/fitBounds。
- [ ] **Step 5: 提交** — `git commit -m "feat: 地图画布（MapLibre 图层叠加管理）"`

---

### Task 5: 组装、生产构建与验收

**Files:**
- Modify: `web/src/App.jsx`（layers state：onResult 追加图层，超 10 提示；计数类无 geometry features 时不加图层仅消息提示）

- [ ] **Step 1: App 组装图层状态**——`const [layers, setLayers] = useState([])`；onResult：若 res.ok && res.geojson?.features?.length 且 features 有非空 geometry → setLayers([...layers, {id: Date.now(), name: question.slice(0,12), geojson: res.geojson, visible: true}].slice(-10))。
- [ ] **Step 2: 生产构建验证**——`cd web && npm run build`；重启 uvicorn（不带 dev 代理）访问 `http://localhost:8000` 确认静态托管可用。
- [ ] **Step 3: 人工验收清单**（记入报告）——①双栏布局 ②三环公园计数（无数值地图图层，仅摘要）③三个几何查询叠加图层 ④开关/删除图层 ⑤断 LLM（改错 key）→ 502 中文提示不崩溃 ⑥空问题 422 前端提示。
- [ ] **Step 4: README 追加 Web 使用说明（dev 与生产两种启动方式）；提交** — `git commit -m "feat: Web 操作台组装与验收（双栏+图层管理）"`

---

## Self-Review 记录

- Spec 覆盖：§3 后端→Task 1；§4 前端五文件→Task 2-5；§5 测试→Task 1 TDD + Task 5 人工清单；§6 风险→底图常量备选（Task 4）、loading 态（Task 3）。无缺口。
- 占位符：前端组件为行为级描述（React 组件无法在计划里逐行预写而不失弹性），验收步骤可操作；后端代码完整。
- 类型一致：QueryResponse 字段与前端 res.geojson/row_count/columns 消费一致；onResult/onToggle/onRemove 签名一致。
