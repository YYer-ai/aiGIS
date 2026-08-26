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
