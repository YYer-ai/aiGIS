# N2 前端实现报告：地图按需出现 + chat 模式呈现 + 慢生成提示

提交：`e9189f8 feat: 地图按需出现+chat 呈现+慢生成提示与取消`
构建：`cd web && npm run build` 通过（vite 5.4.21，37 modules；chunk >500kB 警告为 maplibre-gl 体积，先前已存在）

## 改动明细（web/src/，4 文件 +157/-28）

### 1. 地图按需出现（App.jsx + styles.css）
- `App.jsx`：新增 `mapOpened` 闩锁 state——`layers.length > 0` 时置 true 并**保持**（图层全删后地图不收回，避免闪烁；刷新页面回到无地图全宽对话态）；MapPanel 改为 `{mapOpened && <MapPanel .../>}` 条件渲染，首次挂载才建 map，规避 display:none 初始化问题。
- `styles.css`：
  - `.chat-panel` 无地图时 `flex: 0 0 100%`，`.console.has-map .chat-panel` 收窄至 `flex: 0 0 40%`，`transition: flex-basis 0.4s ease`。
  - `.map-panel` 改 `flex: 0 0 60%` + 挂载时 `@keyframes map-slide-in`（flex-basis 0%→60% + opacity 0→1，0.4s），与 ChatPanel 收窄同步，合计恒 100%，整体平滑滑入；`overflow: hidden` 防浮层卡片在动画期间溢出。
- 不上图层逻辑无需改：`handleResult` 原本按 `res.geojson?.features?.some(f=>f.geometry)` 判断，chat 结果无 geojson 天然不加层。

### 2. chat 模式呈现（ChatPanel.jsx）
- **流式过程区**：StreamingMessage 检测累积 delta `sql` 以 `{` 开头且含 `mode` → 过程区改显示"思考中…"（不展示 LLM 原始 JSON 碎片）；result 到达后进入终态渲染，过程 SQL 自然消失。
- **终态**：ResultBody 顶部新增 `res.chat_mode` 分支——只显示 answer 气泡（`.msg-chat`：💬 前缀 + 浅绿底 + 左侧绿条，与查询态 answer 区分）；无"查询成功·N 行"meta、无大数字卡片/表格；sql 为空时无任何折叠，sql 非空（失败降级遗留）保留小字"（曾尝试的 SQL 见详情 ▾）"折叠（`.msg-detail-mini`）。

### 3. 慢提示与取消（ChatPanel.jsx + api.js）
- 耗时分级：默认"已用时 N 秒 · 通常 10-25 秒"；**>30s** →"生成较慢（模型推理中），可稍候或换个更具体的问法"；**>90s** →"仍在生成——建议停止后重试" + **停止**按钮（`.stop-btn`）。
- `api.js` `streamQuery` 增加第三参 `signal`（透传 fetch）；fetch/read 抛 AbortError 时调 `onAbort`（不误报"连接中断"）。
- ChatPanel `send` 持有 `AbortController`（abortRef），停止按钮 → `abort()` → onAbort 终结消息为"已取消"（`.msg-cancelled` 灰色斜体，非红色报错样式）。

## 实现取舍 / 疑虑
1. **chat 模式 delta 识别为启发式**：以"累积文本以 `{` 开头且含 `mode`"判定 chat 思考中。首帧 `{` 到 `mode` 字样出现之间可能闪现极短 JSON 碎片（毫秒级）；后端若能在 status 事件带 `chat` 阶段标识可彻底消除（对应后端疑虑 1）。
2. **abort 只断前端流**：AbortController 仅断开浏览器侧 SSE 连接，后端 LLM 推理可能继续跑完（uvicorn 会在写响应失败时结束该请求，但上游模型调用不中断）。真正服务端取消需后端支持（如断连检测 / cancel 端点），未在本任务范围。
3. **`overflow: hidden` 加在 .map-panel**：滑入动画期间防浮层溢出；地图边缘的 popup 可能被面板边界裁剪（MapLibre popup 本身会尽量自动内移，影响极小）。
4. **`.has-map` 类驱动布局**：地图收回（删空图层）不发生是刻意设计——收回再滑入会有 0.4s 双向闪动；刷新是回到全宽对话态的唯一途径，符合"页面刷新后回到无地图态"的规格。

## 验证步骤（供浏览器验收）
1. 刷新页面 → 无地图，ChatPanel 全宽。
2. 提问空间查询（如"三环内有多少个公园"）→ 出结果时地图列从右滑入（0.4s），对话列收窄 40%。
3. 删掉地图全部图层 → 地图仍保留；刷新 → 回到无地图全宽态。
4. 提问 chat 类问题（如"你能做什么"）→ 过程区显示"思考中…"（无 JSON 碎片）；结果为 💬 浅绿气泡，无 SQL/行数 meta。
5.（可选，耗时场景）>30s 出现慢生成提示；>90s 出现"停止"按钮，点击后消息显示"已取消"。
