# N1 实现报告：引擎 chat 模式

提交：`df19624` feat: 引擎 chat 模式——AI 自主决策不硬编 SQL+失败降级

## 问题背景

开放性问题（如"交通不堵最方便的是哪个公园"，库中无交通数据）会被硬编 SQL，
首轮常触发 15s statement_timeout 再回喂重试，总耗时 2-4 分钟且最终失败。

## 改动内容

### 1. `src/aigis/prompt.py`

- SYSTEM_TEMPLATE 新增规则 7：问题无法用 schema 数据回答（实时交通/天气/
  主观推荐/闲聊）时输出 `{"mode":"chat","reply":"..."}`，不编造 SQL。
- 规则 1 同步说明 mode 省略默认 query，JSON 约定向后兼容。

### 2. `src/aigis/repair.py`

- `Outcome` 新增 `chat_mode: bool = False` 与 `answer: str = ""`。
- `_parse_llm_json` 改为返回解析后的 dict（原 tuple 拆包移入 `_run`）。
- `_run` 每轮解析后先查 `mode == "chat"`：直接返回 ok=True、chat_mode=True、
  answer=reply，**不校验不执行**；流式下 on_status("回答中") + reply 一次
  整段经 on_delta 推送（在原始 JSON delta 之后）。
- **失败降级**：for 循环 max_retries 耗尽后，用新 `_CHAT_FALLBACK_SYSTEM`
  prompt（含最后错误）追加一次 LLM 调用；返回 mode=chat 则 ok=True、
  chat_mode=True、answer=reply、error=""（out.sql 保留最后一次尝试的 SQL）。
  降级解析失败/无 mode=chat 时保留原 ok=False Outcome。

### 3. `src/aigis_web/schemas.py` / `app.py`

- `QueryResponse` 新增 `chat_mode: bool = False`；`_to_response` 透传
  chat_mode/answer。
- `/api/query` 与 SSE `query_worker` 的总结条件改为 `out.ok and not
  out.chat_mode`——chat 模式 answer 已由引擎填好，跳过 summarize（不再发
  "总结中"/answer_delta），result 事件直接携带 chat_mode=True 与完整 answer。

## 测试

基线 192 → **197 passed**（uv run pytest 全绿）。

新增（tests/test_repair.py +3，tests/test_web.py +2）：

| 测试 | 覆盖 |
|---|---|
| test_chat_mode_returns_directly_without_executing | 首轮 mode=chat → attempts=1、answer=reply、execute 未被调用 |
| test_chat_mode_stream_pushes_reply_as_delta | 流式 status 序列 ["生成SQL（第1次）","回答中"]，reply 整段推送 |
| test_fallback_chat_after_max_retries | 3 轮 BAD 后第 4 次调用 chat → ok=True、chat_mode、sql 保留 |
| test_query_chat_mode_skips_summary | 同步端点 chat：不 summarize，body 带 chat_mode/answer |
| test_stream_chat_mode_events | SSE chat：status(回答中)→delta(reply)→result，无总结中/answer_delta |

同步调整：`test_gives_up_after_max` 语义变为"降级也失败才 ok=False"
（provider 恒返回无 mode 的 BAD，断言不变）；`_mock_outcome` 显式设
chat_mode/answer 默认值（避免 MagicMock 属性 truthy 误判）。

## 验证步骤

1. `uv run pytest -q` → 197 passed。
2. 手工（需真环境）：启动 web 后问"交通不堵最方便的是哪个公园"→ 应直接
   出现一句中文回复（无 SQL 执行、无 15s 超时重试链）。
3. 手工：问一个会失败三次的查询 → 最终应得到解释性回复而非裸错误。

## 疑虑

1. **降级调用的原始 delta 也会流出**：流式下降级/直返 chat 时，LLM 原始
   JSON（`{"mode":"chat",...}`）碎片先经 on_delta 推送，随后才是 reply 整段。
   前端把 delta 当"过程区"浅色小字显示，result 后 answer 正常呈现——不影
   响最终展示，但过程区会短暂出现 JSON 文本。若要干净可改 `_fetch` 缓冲不
   推，代价是失去流式感知，本次未做。
2. **降级成功在 eval 统计中计为 exec_ok**（ok=True）：eval 集均为 schema
   可答的标准查询，正常不会触发；如将来 eval 加入开放题需区分口径。
3. `attempts` 语义保持"SQL 尝试次数"，降级调用不计入——文档口径未写明，
   若前端将来展示 attempts 请按此理解。
