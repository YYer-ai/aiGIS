export async function postQuery(question) {
  const r = await fetch("/api/query", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  }).catch(() => {
    throw new Error("服务不可用，请确认后端已启动");
  });
  let body;
  try {
    body = await r.json();
  } catch {
    throw new Error("服务不可用，请确认后端已启动");
  }
  if (!r.ok) throw new Error(body.error ?? (typeof body.detail === "string" ? body.detail : "请求参数无效"));
  return body;
}

// 解析单个 SSE 帧（event:/data: 行），data 行 JSON.parse；解析失败静默跳帧不炸
function dispatchFrame(frame, h) {
  let event = "message";
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue; // 心跳注释行
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  if (!dataLines.length) return;
  let data;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
  }
  if (event === "status") h.onStatus?.(data.stage);
  else if (event === "delta") h.onDelta?.(data.text);
  else if (event === "result") h.onResult?.(data);
  else if (event === "error") h.onError?.(new Error(data.error || "查询失败"));
}

/**
 * SSE 流式提问：GET /api/query/stream?q=...
 * handlers: { onStatus(stage), onDelta(text), onResult(res), onError(err) }
 */
export async function streamQuery(question, handlers = {}) {
  let r;
  try {
    r = await fetch(`/api/query/stream?q=${encodeURIComponent(question)}`);
  } catch {
    handlers.onError?.(new Error("服务不可用，请确认后端已启动"));
    return;
  }
  if (!r.ok) {
    let msg = `请求失败（${r.status}）`;
    try {
      const body = await r.json();
      if (body?.error) msg = body.error;
    } catch { /* 非 JSON 错误体，用默认文案 */ }
    handlers.onError?.(new Error(msg));
    return;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        dispatchFrame(frame, handlers);
      }
    }
  } catch {
    handlers.onError?.(new Error("连接中断，请重试"));
  }
}
