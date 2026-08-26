import React, { useEffect, useRef, useState } from "react";
import { postQuery } from "./api.js";

function AssistantMessage({ res }) {
  if (!res.ok) {
    return <div className="msg-error">{res.error || "查询失败"}</div>;
  }
  return (
    <div className="msg-body">
      <div className="msg-meta">
        查询成功 · {res.row_count} 行 · 尝试 {res.attempts} 次
      </div>
      {res.sql && (
        <details className="msg-details" open>
          <summary>SQL</summary>
          <pre className="msg-code"><code>{res.sql}</code></pre>
        </details>
      )}
      {res.reasoning && (
        <details className="msg-details">
          <summary>推理过程</summary>
          <div className="msg-reasoning">{res.reasoning}</div>
        </details>
      )}
    </div>
  );
}

export default function ChatPanel({ onResult }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const listRef = useRef(null);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading]);

  async function send(e) {
    e.preventDefault();
    const question = input.trim();
    if (!question || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: question }]);
    setLoading(true);
    let res;
    try {
      res = await postQuery(question);
    } catch (err) {
      res = { ok: false, error: err.message };
    } finally {
      setLoading(false);
    }
    setMessages((m) => [...m, { role: "assistant", text: "", res }]);
    if (res.ok && onResult) onResult(res);
  }

  return (
    <div className="chat-panel" id="chat-panel">
      <div className="chat-header">AI-GIS 查询助手</div>
      <div className="chat-messages" ref={listRef}>
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="msg msg-user">{m.text}</div>
          ) : (
            <div key={i} className="msg msg-assistant">
              <AssistantMessage res={m.res} />
            </div>
          )
        )}
        {loading && <div className="msg msg-assistant msg-loading">思考中…</div>}
      </div>
      <form className="chat-input" onSubmit={send}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={loading ? "思考中…" : "输入自然语言问题，例如：面积最大的省是哪个？"}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>发送</button>
      </form>
    </div>
  );
}
