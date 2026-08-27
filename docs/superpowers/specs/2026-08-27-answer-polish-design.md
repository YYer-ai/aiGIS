# 回复形态优化（自然语言回答 + 详情折叠）

> 状态：自主模式（用户指令明确：默认只显示 AI 输出文本，点详情才显示代码内容）
> 日期：2026-08-27

## 1. 目标与验收

- 助手消息默认只显示：**一句自然语言回答**（基于真实查询结果生成，如"三环内共有 292 个公园"）+ 单值结果的大数字卡片。
- "详情"按钮展开：查询状态（行数/尝试次数）、SQL 代码、推理过程、多行结果表格。
- 回答生成失败（LLM 异常）降级为模板文本"查询完成，共 N 行结果"，不影响主流程。
- 验收：浏览器实测——发问后先见阶段与浅色 SQL 生成进度，随后回答逐字流出；默认无代码块；点详情完整展开。

## 2. 方案

### 2.1 引擎层（`src/aigis/llm.py` + `app.py`）

- `llm.py` 新增 `summarize(question: str, columns: list[str], sample_rows: list, row_count: int, cfg: Config) -> Iterator[str]`：
  - prompt：系统"用一句自然的中文回答用户问题，基于给定查询结果；不编造，空结果就说没有找到；数字用阿拉伯数字"；用户内容=问题+列名+前 5 行样本+总行数。
  - 流式返回 token；异常 → yield 单条模板文本后结束（不抛）。
- SSE 端点新增事件：执行成功后发 `event: status`（"总结中"）→ `event: answer_delta`（data {"text"}）若干 → `event: result`。QueryResponse 增加 `answer: str` 字段（/api/query 同步端点也填充——非流式一次拿全）。
- CLI 不变（answer 可选输出）。

### 2.2 前端（ChatPanel）

- 消息默认态：answer 文本（流式追加）+ ResultBody 仅大数字卡片模式。
- "详情"toggle 按钮（默认收起）：内含 状态行（查询成功·N行·尝试N次）/ SQL code / 推理过程 / 多行表格（前10行+截断提示）。
- loading 期：保留阶段提示+计时；SQL 流式文本改为**浅色小字**弱化显示（生成中可见进度，完成即收起入详情）。
- 失降级：answer 为模板文本时照常显示。

## 3. 测试

- 后端：summarize mock 流式拼接/异常降级；SSE 事件序 status(总结中)→answer_delta*→result 含 answer 字段；QueryResponse answer 字段断言。
- 前端浏览器实测（协调者执行）。
