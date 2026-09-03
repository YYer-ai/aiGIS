const SVC_MSG = "服务不可用——请运行项目根目录的 start_web.ps1 重启后端（机器重启后需重新启动）";

export async function postQuery(question) {
  const r = await fetch("/api/query", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  }).catch(() => {
    throw new Error(SVC_MSG);
  });
  let body;
  try {
    body = await r.json();
  } catch {
    throw new Error(SVC_MSG);
  }
  if (!r.ok) throw new Error(body.error ?? (typeof body.detail === "string" ? body.detail : "请求参数无效"));
  return body;
}

// 统一响应处理：非 2xx 抛后端 error 字段（缺省用 fallback 文案）
async function reqJson(r, fallback) {
  let body = null;
  try { body = await r.json(); } catch { /* 非 JSON 体 */ }
  if (!r.ok) throw new Error(body?.error ?? fallback);
  return body;
}

// ---------- 会话管理（多会话侧栏） ----------

export async function fetchSessions() {
  return reqJson(await fetch("/api/sessions").catch(() => { throw new Error(SVC_MSG); }),
    "会话列表读取失败");
}

export async function createSession(title = "") {
  return reqJson(await fetch("/api/sessions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  }).catch(() => { throw new Error(SVC_MSG); }), "会话创建失败");
}

export async function deleteSession(id) {
  return reqJson(
    await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" })
      .catch(() => { throw new Error(SVC_MSG); }),
    "会话删除失败");
}

export async function renameSession(id, title) {
  return reqJson(
    await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).catch(() => { throw new Error(SVC_MSG); }),
    "会话重命名失败");
}

export async function fetchMessages(id) {
  return reqJson(
    await fetch(`/api/sessions/${encodeURIComponent(id)}/messages`)
      .catch(() => { throw new Error(SVC_MSG); }),
    "会话消息读取失败");
}

// ---------- 图层库（M5） ----------

export async function fetchLayers() {
  return reqJson(await fetch("/api/layers").catch(() => { throw new Error(SVC_MSG); }),
    "图层库读取失败");
}

export async function fetchLayerGeojson(name) {
  return reqJson(
    await fetch(`/api/layers/${encodeURIComponent(name)}/geojson`)
      .catch(() => { throw new Error(SVC_MSG); }),
    "图层加载失败");
}

export async function deleteLayer(name) {
  return reqJson(
    await fetch(`/api/layers/${encodeURIComponent(name)}`, { method: "DELETE" })
      .catch(() => { throw new Error(SVC_MSG); }),
    "图层删除失败");
}

export async function saveLayer({ name, label, geojson }) {
  return reqJson(await fetch("/api/layers/save", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, label, geojson }),
  }).catch(() => { throw new Error(SVC_MSG); }), "图层保存失败");
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
  else if (event === "answer_delta") h.onAnswerDelta?.(data.text);
  else if (event === "result") h.onResult?.(data);
  else if (event === "error") h.onError?.(new Error(data.error || "查询失败"));
}

/**
 * SSE 流式提问：GET /api/query/stream?q=...&session_id=...
 * handlers: { onStatus(stage), onDelta(text), onAnswerDelta(text), onResult(res), onError(err), onAbort() }
 * opts: { signal?: AbortSignal——abort 后调 onAbort（而非 onError 连接中断）,
 *         sessionId?: 会话 id——后端据此组装会话记忆并落库 }
 */
export async function streamQuery(question, handlers = {}, opts = {}) {
  const { signal, sessionId } = opts;
  const qs = `q=${encodeURIComponent(question)}` +
    (sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : "");
  let r;
  try {
    r = await fetch(`/api/query/stream?${qs}`, { signal });
  } catch {
    if (signal?.aborted) { handlers.onAbort?.(); return; }
    handlers.onError?.(new Error("服务不可用——请运行项目根目录的 start_web.ps1 重启后端（机器重启后需重新启动）"));
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
    if (signal?.aborted) { handlers.onAbort?.(); return; }
    handlers.onError?.(new Error("连接中断，请重试"));
  }
}
