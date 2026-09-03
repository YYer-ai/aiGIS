import React, { useEffect, useRef, useState } from "react";
import { fetchMessages, streamQuery } from "./api.js";

const EXAMPLES = [
  "三环内有多少个公园",
  "距天安门广场2公里内有哪些餐厅",
  "五环内面积最大的三个公园",
];

// 终态结果呈现：默认 answer 文本 + 单值大数字卡片 + meta 一行；SQL/推理/多行表格收进"详情"折叠。
// chat 模式（chat_mode=true，AI 直接回答不硬编 SQL）：只显示 answer 气泡（.msg-chat 与查询态区分），
// 曾尝试的 SQL（失败降级前的遗留）以小字折叠保留；无 SQL 则无任何折叠
function ResultBody({ res, answer }) {
  const [showDetail, setShowDetail] = useState(false);
  if (res.chat_mode) {
    return (
      <div className="msg-body">
        {answer && <div className="msg-answer msg-chat">{answer}</div>}
        {res.sql && (
          <>
            <button type="button" className="msg-detail-mini"
              onClick={() => setShowDetail((v) => !v)}>
              （曾尝试的 SQL 见详情 {showDetail ? "▴" : "▾"}）
            </button>
            {showDetail && <pre className="msg-code"><code>{res.sql}</code></pre>}
          </>
        )}
      </div>
    );
  }
  const single = res.ok && res.row_count === 1 && res.columns.length === 1;
  const table = res.ok && !single && res.sample_rows?.length > 0;
  // 隐藏 geometry 列（GeoJSON 长文本，几何已由地图承载）；全列均为 geometry 时退回原列防全空
  const geoIdx = res.columns.map((c, i) => (c === "geometry" ? i : -1)).filter((i) => i >= 0);
  const hidden = geoIdx.length > 0 && geoIdx.length < res.columns.length ? new Set(geoIdx) : null;
  const columns = hidden ? res.columns.filter((_, i) => !hidden.has(i)) : res.columns;
  return (
    <div className="msg-body">
      {answer && <div className="msg-answer">{answer}</div>}
      {single && (
        <div className="big-number-card">
          <div className="big-number-label">{res.columns[0]}</div>
          <div className="big-number-value">
            {res.sample_rows?.[0]?.[0] ?? res.row_count}
          </div>
        </div>
      )}
      <div className="msg-meta">
        查询成功 · {res.row_count} 行{res.attempts != null && <> · 尝试 {res.attempts} 次</>}
      </div>
      <button type="button" className="msg-detail-toggle" onClick={() => setShowDetail((v) => !v)}>
        详情 {showDetail ? "▴" : "▾"}
      </button>
      {showDetail && (
        <div className="msg-detail-body">
          {res.sql && (
            <>
              <div className="msg-detail-label">SQL</div>
              <pre className="msg-code"><code>{res.sql}</code></pre>
            </>
          )}
          {res.reasoning && (
            <>
              <div className="msg-detail-label">推理过程</div>
              <div className="msg-reasoning">{res.reasoning}</div>
            </>
          )}
          {table && (
            <div className="msg-table-wrap">
              <table className="msg-table">
                <thead>
                  <tr>{columns.map((c, i) => <th key={i}>{c}</th>)}</tr>
                </thead>
                <tbody>
                  {res.sample_rows.map((row, r) => (
                    <tr key={r}>{row.map((v, i) => hidden?.has(i) ? null : <td key={i}>{v}</td>)}</tr>
                  ))}
                </tbody>
              </table>
              {res.row_count > res.sample_rows.length && (
                <div className="msg-table-more">共 {res.row_count} 行，仅显示前 {res.sample_rows.length} 行</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// 后端持久化消息 → 前端消息形态：user 直取 content；assistant 由 content+meta
// 还原可渲染 res（列/行明细未持久化，详情只保留 SQL，行数进 meta 行）
function toRestored(m) {
  if (m.role === "user") return { role: "user", text: m.content };
  const meta = m.meta || {};
  if (meta.chat_mode) {
    return { role: "assistant", res: { chat_mode: true, answer: m.content, sql: meta.sql } };
  }
  if (meta.sql !== undefined && meta.row_count !== undefined) {
    return { role: "assistant", res: { ok: true, answer: m.content, sql: meta.sql,
      row_count: meta.row_count, columns: [], sample_rows: [] } };
  }
  return { role: "assistant", res: { ok: false, error: m.content } };
}

// 流式中的助手消息：阶段 + 已耗时 + SQL 浅色小字（总结阶段收起 SQL、显示 answer 逐字）。
// chat 模式的 delta 是 LLM 原始 JSON 碎片（后端以 {"mode":...} 结构决策），过程区不展示改"思考中…"；
// 耗时分级提示：>30s 慢生成说明，>90s 升级建议停止并给"停止"按钮
function StreamingMessage({ stage, sql, answer, elapsed, onStop }) {
  const summarizing = stage === "总结中" || Boolean(answer);
  const chatThinking = sql && sql.trimStart().startsWith("{") && sql.includes("mode");
  return (
    <div className="msg-body">
      <div className="msg-stage">
        {stage || "理解问题"}<span className="stage-spinner" />
      </div>
      <div className="msg-elapsed">
        {elapsed > 90 ? (
          <>
            已用时 {elapsed} 秒 · 仍在生成——建议停止后重试
            <button type="button" className="stop-btn" onClick={onStop}>停止</button>
          </>
        ) : elapsed > 30 ? (
          <>已用时 {elapsed} 秒 · 生成较慢（模型推理中），可稍候或换个更具体的问法</>
        ) : (
          <>已用时 {elapsed} 秒 · 通常 10-25 秒</>
        )}
      </div>
      {summarizing ? (
        answer ? <div className="msg-answer msg-answer-streaming">{answer}</div> : null
      ) : chatThinking ? (
        <div className="msg-sql-ghost">
          <span className="msg-sql-ghost-label">思考中…</span>
        </div>
      ) : sql ? (
        <div className="msg-sql-ghost">
          <span className="msg-sql-ghost-label">SQL 生成中…</span>
          <code>{sql}</code>
        </div>
      ) : null}
    </div>
  );
}

export default function ChatPanel({ onResult, sessionId, onEnsureSession }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const listRef = useRef(null);
  const abortRef = useRef(null); // 当前流式请求的 AbortController（>90s"停止"按钮用）
  const genRef = useRef(0);      // 会话代际：切换会话时 ++，使在途流式回调全部失效
  const skipLoadRef = useRef(false); // 首发自动建会话引起的 sessionId 变化不重载消息

  // 切换/恢复会话：sessionId 变化 → 重建消息流（null=新会话清空）；
  // 在途流式请求作废（abort + 代际失效，防止回调写进新会话消息）
  useEffect(() => {
    if (skipLoadRef.current) { skipLoadRef.current = false; return; }
    const gen = ++genRef.current;
    abortRef.current?.abort();
    if (sessionId) {
      fetchMessages(sessionId)
        .then((msgs) => { if (genRef.current === gen) setMessages(msgs.map(toRestored)); })
        .catch(() => { /* 恢复失败：保持空态，不阻塞新提问 */ });
    } else {
      setMessages([]);
    }
  }, [sessionId]);

  // 新消息/流式更新/计时 → 自动滚到底
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, elapsed]);

  // 更新最后一条（流式期间 loading 保护下必然是当前助手消息）
  const patchLast = (patch) =>
    setMessages((prev) =>
      prev.map((m, i) => (i === prev.length - 1 ? { ...m, ...patch } : m))
    );

  async function send(question) {
    question = (question ?? input).trim();
    if (!question || loading) return;
    setInput("");
    setMessages((m) => [
      ...m,
      { role: "user", text: question },
      { role: "assistant", streaming: true, stage: "理解问题", sql: "", answer: "", res: null },
    ]);
    setLoading(true);
    setElapsed(0);
    const start = Date.now();
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    let finalRes = null;
    const controller = new AbortController();
    abortRef.current = controller;
    const gen = genRef.current;
    const alive = () => genRef.current === gen; // 会话被切换 → 本轮流式回调全部作废

    // 未建会话时先自动创建（标题=问题前 20 字），后续 SSE 请求携带 session_id
    let sid = sessionId;
    if (onEnsureSession && !sid) {
      skipLoadRef.current = true; // App 更新 sessionId 触发的重载 effect 跳过（消息已在本地）
      try {
        sid = await onEnsureSession(question);
      } catch (err) {
        skipLoadRef.current = false; // 会话未建成、sessionId 不变：归还跳过标记
        clearInterval(timer);
        setLoading(false);
        abortRef.current = null;
        patchLast({ streaming: false, res: { ok: false, error: err.message } });
        return;
      }
      if (!alive()) { // 建会话期间用户切走了会话：放弃本轮流式
        clearInterval(timer);
        setLoading(false);
        abortRef.current = null;
        return;
      }
    }

    streamQuery(question, {
      onStatus: (stage) => {
        // 新一轮"生成SQL（第N次）"重置 SQL 块（按轮重置）
        if (stage.startsWith("生成SQL")) patchLast({ stage, sql: "" });
        else patchLast({ stage });
      },
      onDelta: (text) => {
        setMessages((prev) =>
          prev.map((m, i) => (i === prev.length - 1 ? { ...m, sql: (m.sql || "") + text } : m))
        );
      },
      onAnswerDelta: (text) => {
        setMessages((prev) =>
          prev.map((m, i) => (i === prev.length - 1 ? { ...m, answer: (m.answer || "") + text } : m))
        );
      },
      onResult: (res) => {
        finalRes = res;
        if (alive()) patchLast({ streaming: false, res });
      },
      onError: (err) => {
        finalRes = { ok: false, error: err.message };
        if (alive()) patchLast({ streaming: false, res: finalRes });
      },
      onAbort: () => {
        // 用户主动停止：终结为"已取消"（非报错样式）
        finalRes = { ok: false, cancelled: true };
        if (alive()) patchLast({ streaming: false, res: finalRes });
      },
    }, { signal: controller.signal, sessionId: sid }).finally(() => {
      clearInterval(timer);
      setLoading(false);
      abortRef.current = null;
      // 会话已切换：不再写消息（新会话消息由重载 effect 重建）
      if (!alive()) return;
      // 兜底：流结束但既无 result 也无 error（如代理截断），终结消息避免卡在流式态
      if (!finalRes) {
        finalRes = { ok: false, error: "连接中断，未收到结果，请重试" };
        patchLast({ streaming: false, res: finalRes });
      }
      if (finalRes.ok && onResult) onResult(finalRes, question);
    });
  }

  // 停止当前流式请求（停止按钮仅 >90s 时出现）
  const stop = () => abortRef.current?.abort();

  // Enter 发送；输入法 composing（中文回车选词）不触发
  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  const empty = messages.length === 0;

  return (
    <div className="chat-panel" id="chat-panel">
      <div className="chat-header">AI-GIS 查询助手</div>
      <div className="chat-messages" ref={listRef}>
        {empty && (
          <div className="chat-empty">
            <div className="chat-empty-title">用中文提问，AI 生成 SQL 并在地图上展示结果</div>
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="msg msg-user">{m.text}</div>
          ) : (
            <div key={i} className="msg msg-assistant">
              {m.streaming ? (
                <StreamingMessage stage={m.stage} sql={m.sql} answer={m.answer}
                  elapsed={elapsed} onStop={stop} />
              ) : m.res ? (
                m.res.cancelled ? (
                  <div className="msg-cancelled">已取消</div>
                ) : m.res.ok ? (
                  // answer 优先取 result 事件返回值，旧缓存缺字段时退回流式累积文本
                  <ResultBody res={m.res} answer={m.res.answer || m.answer} />
                ) : (
                  <div className="msg-error">{m.res.error || "查询失败"}</div>
                )
              ) : null}
            </div>
          )
        )}
      </div>
      {empty && (
        <div className="chat-examples">
          {EXAMPLES.map((q) => (
            <button key={q} type="button" className="example-chip" onClick={() => send(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={loading ? "思考中…" : "输入自然语言问题，例如：面积最大的省是哪个？"}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>发送</button>
      </form>
    </div>
  );
}
