# T1 报告：自然语言回答生成（summarize + SSE answer 流）

> 日期：2026-08-27 · 提交 `eb4fc46` · 测试 119 passed（基线 114 + 新增 5）

## 改动

### 1. `src/aigis/llm.py` — `summarize()`

```python
def summarize(question, columns, sample_rows, row_count, cfg) -> Iterator[str]
```

- 系统 prompt：查询结果播报员（一句自然中文 / 严格基于结果不编造 / 阿拉伯数字 / 空结果说没找到）。
- 用户内容 = 问题 + 列名 + 前 5 行样本（内部截断 `sample_rows[:5]`，JSON 序列化）+ 总行数。
- 内部 `make_provider(cfg).generate_stream(...)` 流式转发 token。
- **异常容错**：`make_provider`（缺 key）或流式生成抛任何异常 → yield 模板 `f"查询完成，共 {row_count} 行结果。"` 后结束，不抛。

### 2. `src/aigis_web/schemas.py`

- `QueryResponse` 新增 `answer: str = ""`。

### 3. `src/aigis_web/app.py`

- 新增 `_summary_sample(rows)`：前 5 行、单元格 str 化。
- `/api/query`（同步）：执行成功（`out.ok`）后 `"".join(summarize(...))` 一次拿全填 `resp.answer`；`cfg` 提前 load 一次复用。
- `/api/query/stream`（SSE）：worker 线程内、result 前同线程执行——
  `status(总结中)` → 每个 token 发 `answer_delta {"text":...}` → `resp.answer = 拼接全文` → `result`。
  执行失败（`ok=False`）不进入总结，result 中 answer 为空串。

## 测试（TDD：先 red 后 green）

新增 5 个：

- `test_summarize_streams_natural_answer`：流式拼接 + prompt 要素（系统约束/问题/列名/样本/总行数）。
- `test_summarize_falls_back_to_template_on_llm_error`：LLMError → 单条模板降级。
- `test_summarize_falls_back_when_missing_key`：缺 key → 模板。
- `test_stream_answer_multi_delta`：SSE 事件序 `status×2 → answer_delta×3 → result`，result.answer 为全文。
- `test_stream_no_answer_on_failed_query`：ok=False 时不调 summarize，`answer: ""`。

更新 2 个现有测试（避免真实 LLM 调用 / 适配新事件序）：

- `test_query_ok`：patch summarize，断言 `answer` 字段。
- `test_stream_event_sequence`：事件序加入 `status(总结中)` + `answer_delta`，断言 result 含 answer。

## 备注 / 疑虑

- 流中途异常（已 yield 部分 token 后抛）会产出"部分文本 + 模板"拼接的 answer——按 spec"任何异常 yield 模板"字面实现，未做部分内容回滚（generate_stream 的异常重试发生在建连阶段，流中途异常罕见）。
- CLI 未接 answer（spec 明确"CLI 不变，answer 可选输出"）。
- 前端 ChatPanel 消费 answer_delta 属 T2，未动。
