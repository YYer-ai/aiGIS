# T1 报告：chat 模式三层强化——示例/规则前置/回喂可转 chat

## 根因

15 条 few-shot 全是 SQL 示例，压倒了文字规则 7；Qwen 对"数据答不了的题"（实测"交通不堵最方便的是哪个公园"181s）仍硬编 SQL 走完重试环。

## 三层强化（零额外延迟）

### 1. few-shot 追加 3 条 chat 示例（data/fewshot.yaml）

裁定：chat 条目用独立字段形态 `{question, mode: chat, reply}`，不混入 sql 字段，避免破坏现有回归。追加：

- 你好，你能做什么 → 能力简介（中文空间查询/地图制作/图层管理）
- 交通不堵最方便的是哪个公园 → 说明无实时交通数据 + 近似判断 + 可查替代建议
- 今天天气怎么样 → 无天气数据说明 + 建议

### 2. SYSTEM_TEMPLATE 规则前置（src/aigis/prompt.py）

- 原规则 7（chat 逃生口）提升为规则 2，紧跟"只输出一个 JSON"之后
- 表述强化："优先判断……**必须**直接输出 chat，禁止编造 SQL 硬答；reply 要直接回答或说明数据局限并给出可查的替代建议"
- 原 2–6 顺延为 3–7

### 3. 回喂可转 chat（src/aigis/repair.py）

新增常量 `_CHAT_HINT`，在 `_run` 的**校验失败**与**执行失败**两处 feedback 末尾追加：
"若该问题本质上无法用现有数据回答（如刚才因超时/错误失败），可改用 {"mode":"chat","reply":"..."} 直接回复用户"。
解析失败轮不加（输出格式问题与数据能力无关）。

### few-shot 渲染（src/aigis/prompt.py）

新增 `render_fewshot()`：mode=chat 条目渲染为 `问：xx\n答（chat）：{"mode":"chat","reply":"..."}`（json.dumps 生成，可反解析）；SQL 条目维持 `问：/SQL：` 不变。

## 测试（197 → 200 passed）

- `test_prompt.py`
  - `test_load_fewshot_default`：按 mode 分支断言字段形态，chat 条目 ≥3
  - 新增 `test_build_messages_renders_chat_fewshot`：chat/SQL 两类条目共存的渲染断言
  - 新增 `test_render_fewshot_chat_json_is_valid`：渲染出的 chat JSON 可 json.loads 还原
  - 两个参数化回归（validator/真库执行）筛选 `"sql" in s`，跳过 chat 条目——参数化条目数仍为 15，与基线一致
- `test_repair.py` 新增 `test_validation_failure_retry_can_switch_to_chat`：BAD SQL（校验失败）→ 第 2 轮 user prompt 含"可改用 chat"提示 → 第 2 轮返回 mode=chat → ok=True、chat_mode、attempts=2、execute 未被调用

## 冒烟

`build_messages("交通不堵最方便的是哪个公园", ...)` 实际输出确认：规则 2 为 chat 优先判断条款；3 条 chat 示例以 `答（chat）：{"mode":"chat","reply":...}` 形态拼入 system。

## 验证步骤

```powershell
cd E:/aiGIS
uv run pytest -q   # 200 passed
```
