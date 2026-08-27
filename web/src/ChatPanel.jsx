import React, { useEffect, useRef, useState } from "react";
import { streamQuery } from "./api.js";

const EXAMPLES = [
  "三环内有多少个公园",
  "距天安门广场2公里内有哪些餐厅",
  "五环内面积最大的三个公园",
];

// 终态结果呈现：默认 answer 文本 + 单值大数字卡片 + meta 一行；SQL/推理/多行表格收进"详情"折叠
function ResultBody({ res, answer }) {
  const [showDetail, setShowDetail] = useState(false);
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
        查询成功 · {res.row_count} 行 · 尝试 {res.attempts} 次
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

// 流式中的助手消息：阶段 + 已耗时 + SQL 浅色小字（总结阶段收起 SQL、显示 answer 逐字）
function StreamingMessage({ stage, sql, answer, elapsed }) {
  const summarizing = stage === "总结中" || Boolean(answer);
  return (
    <div className="msg-body">
      <div className="msg-stage">
        {stage || "理解问题"}<span className="stage-spinner" />
      </div>
      <div className="msg-elapsed">
        已用时 {elapsed} 秒 · 通常 10-25 秒
      </div>
      {summarizing ? (
        answer ? <div className="msg-answer msg-answer-streaming">{answer}</div> : null
      ) : sql ? (
        <div className="msg-sql-ghost">
          <span className="msg-sql-ghost-label">SQL 生成中…</span>
          <code>{sql}</code>
        </div>
      ) : null}
    </div>
  );
}

export default function ChatPanel({ onResult }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const listRef = useRef(null);

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

  function send(question) {
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
        patchLast({ streaming: false, res });
      },
      onError: (err) => {
        finalRes = { ok: false, error: err.message };
        patchLast({ streaming: false, res: finalRes });
      },
    }).finally(() => {
      clearInterval(timer);
      setLoading(false);
      // 兜底：流结束但既无 result 也无 error（如代理截断），终结消息避免卡在流式态
      if (!finalRes) {
        finalRes = { ok: false, error: "连接中断，未收到结果，请重试" };
        patchLast({ streaming: false, res: finalRes });
      }
      if (finalRes.ok && onResult) onResult(finalRes, question);
    });
  }

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
                <StreamingMessage stage={m.stage} sql={m.sql} answer={m.answer} elapsed={elapsed} />
              ) : m.res ? (
                m.res.ok ? (
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
