# T2+T3 实施报告：SQLite 会话存储 + 引擎上下文注入

日期：2026-09-02 ｜ 测试：`uv run pytest` 214 passed（含 integration 真库）

## T2 会话存储 `src/aigis_web/store.py`

- **连接策略**：每请求短连接（open → 用 → close），无跨线程共享连接，天然适配 FastAPI 线程池；每连接 `PRAGMA foreign_keys = ON`（SQLite 级联删除默认关闭，必须显式开）。
- **库文件**：默认仓库根 `data/chat.db`（`data/*` 已 gitignore，AIGIS_ROOT 环境变量优先）；连接时幂等建表（`CREATE TABLE IF NOT EXISTS`），首次连接即初始化，无部署顺序依赖。测试通过改写模块级 `_DB_PATH` 指向 tmp 文件实现隔离。
- **表结构**：
  - `sessions(id TEXT PK, title, summary DEFAULT '', created_at, updated_at)`
  - `messages(id INTEGER PK AUTOINCREMENT, session_id REFERENCES sessions(id) ON DELETE CASCADE, role, content, meta TEXT DEFAULT '{}', summarized INTEGER DEFAULT 0, created_at)`；meta 为 JSON 文本（assistant 消息存 sql/answer/chat_mode/row_count 等）。
  - 索引 `idx_messages_session(session_id, id)`；时间戳统一 `isoformat(timespec="microseconds")` 恒定长度，字符串排序 = 时间排序（`ORDER BY updated_at DESC` 依赖此性质）。
- **API**：`create_session(title)`、`get_session(id)`、`list_sessions()`（updated_at 倒序）、`rename_session(id,title)`、`delete_session(id)`（级联删消息）、`set_summary(id,summary)`、`append_message(sid,role,content,meta)`（touch updated_at；首条消息且 title 为空时自动取 content 前 20 字为标题）、`get_messages(sid,limit)`（子查询 `ORDER BY id DESC LIMIT` 外层再 ASC，正序返回最近 limit 条）、`mark_summarized(sid,ids)`（summarized=0 才标记，rowcount 即新增标记数）、`summarized_count(sid)`。
- rename 不 touch updated_at（该字段语义为"最后有消息活动"）。

## T3 引擎上下文注入

- `src/aigis/prompt.py` `build_messages(question, schema_text, history=None)`：history 非 None/空时在 user 消息 `问题：` 行之前插入 `对话上下文（最近对话，供指代消解）：\n{history}\n\n`；无 history 输出与旧格式逐字节一致（现测试零改动通过）。
- `src/aigis/repair.py`：`run_query` / `run_query_stream` 尾部新增 `history: str | None = None` 关键字参数，经 `_run` 透传 build_messages；与回喂 feedback 拼接正交（feedback 拼在 question 后，history 独立成段）。
- `src/aigis/make.py`：`run_make_task` 同样加 `history` 参数，`_make_messages` 在 `制作指令：` 前插入同格式上下文（"把刚才的结果做成缓冲图层"类指代消解）。
- chat 流式逐字：裁定维持现状不改——chat 判定发生在 JSON 解析后才有 reply，而 generate_stream 的 delta 是完整 LLM 输出 JSON，无法在流中只取 reply 字段；现有"reply 一次整段推送"配合前端 answer 呈现已足够。
- web 层（app.py）本任务未动：调用方 history 缺省为 None，行为完全不变；history 的组装（近期消息 + summary 拼接）属后续任务。

## 测试

新增 14 个用例，全套 214 passed（`uv run pytest`，含 integration）：

- `tests/test_store.py`（9）：create/get、updated_at 倒序、rename/delete、首条消息自动标题（含 20 字截断与显式标题不覆盖）、meta JSON 落库读回、touch updated_at、limit 正序取最近 N 条、级联删除、mark/重复标记幂等/跨会话隔离/summarized_count。
- `tests/test_prompt.py`（2）：history 插入位置断言（上下文段在 `问题：` 之前）；None/空串输出与旧行为一致。
- `tests/test_repair.py`（2）：run_query / run_query_stream 透传（mock provider 捕获 user 消息含上下文段；无 history 不含）。
- `tests/test_make.py`（1）：run_make_task 透传（mock `_existing_layers_text` 避免真连库，解析失败路径即返回）。

## 疑虑与后续

1. `mark_summarized` 按 message_ids 显式传参；若后续摘要任务按"前 N 条"批量并入，可能更宜提供 `mark_summarized_upto(sid, message_id)` 一类的便利封装（暂未加，避免推测性实现）。
2. 每请求短连接在并发下每个连接各跑一次 `executescript(_SCHEMA)`（幂等 DDL），开销为一次 schema 查询，可接受；若日后有性能问题可改为启动时初始化。
3. history 为调用方拼接的纯文本（非 messages 数组），token 用量不可精确控制——后续组装方（web 层）需自行按条数/字数截断。
