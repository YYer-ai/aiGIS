export async function postQuery(question) {
  const r = await fetch("/api/query", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || `请求失败(${r.status})`);
  return body;
}
