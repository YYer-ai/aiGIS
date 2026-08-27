# 用户体验优化设计（流式反馈 + 直观呈现 + 交互打磨）

> 状态：自主模式（用户授权自主决策），最终汇报披露全部裁定
> 日期：2026-08-27
> 前置：三阶段已收尾（main，106 passed）

## 1. 目标与验收

优化"更直观更流畅"：
- **流式反馈**：提问后 SQL 逐字流式显示（SSE），等待期显示已耗时与预期提示——消除 15 秒干等感。
- **直观呈现**：单值结果大数字卡片；多行结果表格（前 10 行 + 剩余计数）；消息自动滚动到底。
- **交互打磨**：Enter 发送；3 个示例问题 chips；地图要素点击 popup 显示属性。
- **一键启动**：`start_web.ps1` 自动构建（如需）+ 起服务 + 开浏览器。

验收：浏览器实测——发送问题后 2 秒内出现 SQL 逐字增长；结果卡片/表格正确渲染；点地图要素出 popup；Enter 与 chips 可用；`pwsh start_web.ps1` 一条命令可用。

## 2. 技术方案

### 2.1 引擎流式（最小侵入）

- `llm.py`：`OpenAICompatProvider.generate_stream(system, user) -> Iterator[str]`（SDK stream=True，yield delta.content；超时/连接错误重试逻辑与 generate 一致）。
- `repair.py`：`run_query_stream(question, cfg, on_delta: Callable[[str], None] | None = None, max_retries=3) -> Outcome`——与 run_query 同构，唯一差异：LLM 输出经 generate_stream 并把每个 delta 回调 on_delta。**自修复轮次仅首轮流式推送**（重试轮的拼接噪声对用户无意义，只推最终轮——裁定：全部轮次都推送但前端按轮次重置 SQL 块，更诚实；选后者，成本低）。
- CLI/evaluator 不受影响（run_query 保留）。

### 2.2 后端 SSE

- `app.py` 新增 `GET /api/query/stream?q=<urlencoded>`（EventSource 兼容 GET）：
  - 事件流（`text/event-stream`）：`event: status`（data: {stage}：理解问题→生成SQL→执行→完成）；`event: delta`（data: {text}）；`event: result`（data: QueryResponse JSON）；`event: error`（data: {error}）。
  - run_query_stream 内部映射：进入循环前发"理解问题"；每轮 LLM 开始发"生成SQL（第N次）"+delta 流；执行前发"执行查询"。
  - 心跳：每 15s 发 `: ping` 注释行防代理断连。

### 2.3 前端

- `api.js`：`streamQuery(question, {onStatus, onDelta, onResult, onError})`——fetch ReadableStream + TextDecoder 按 SSE 帧解析。
- `ChatPanel`：发送走 streamQuery；SQL 块实时追加文本；等待条显示已耗时秒数与"通常 10-25 秒"；Enter 发送；3 个 chips（三环内有多少个公园 / 距天安门2公里内有哪些餐厅 / 五环内面积最大的三个公园）；新消息 scrollTop 到底。
- 结果呈现：res 返回后——单行单列数值 → 大数字卡片；多行 → 前 10 行 HTML 表格 + "共 N 行"；其余照旧。
- `MapPanel`：map.on('click', layer) → 收集 properties 渲染 `maplibregl.Popup`（对 pt/ln/pg 三个子层都挂）。

### 2.4 启动脚本

- `start_web.ps1`：检查 web/dist 不存在则 `npm run build` → `Start-Process` 起 uvicorn → `Start-Process "http://localhost:8000"`。

## 3. 测试

- 单测：generate_stream（mock chunk 序列拼接正确、异常路径）；SSE 端点（TestClient 逐事件断言序列 status→delta*→result）。
- 前端人工验收（浏览器实测清单，控制器执行）。

## 4. 风险

- SSE 经 Vite 代理需 `proxy: {"/api": {target, ChangeEvent…}}`——Vite 默认支持 SSE 透传，无需额外配置（若实测断流，加 `configure` 关闭缓冲）。
- EventSource 编码：中文问题需 encodeURIComponent——GET q 参数方案自带。
