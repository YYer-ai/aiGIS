import React, { useEffect, useState } from "react";
import {
  activateProvider, deleteProviderApi, fetchSettings, saveLLMSettings,
  saveProvider, verifyProvider,
} from "./api.js";

const THINKING_OPTS = [
  ["off", "关闭"], ["low", "低"], ["medium", "中"], ["high", "高"],
];

/**
 * 模型配置（运维界面）：供应商管理 + config.toml 运行参数。
 * 供应商为 cc-switch 式多套存档（name/base_url/api_key/model），一套激活——
 * 激活即写后端 .env 热生效；api_key 后端只回脱敏值，编辑留空表示沿用。
 */
export default function SettingsPanel() {
  const [providers, setProviders] = useState(null);
  const [activeId, setActiveId] = useState(null);
  const [llm, setLlm] = useState(null);
  const [loadErr, setLoadErr] = useState("");

  const [form, setForm] = useState(null);   // null=收起；{id?, name, base_url, api_key, model}
  const [formBusy, setFormBusy] = useState("");
  const [formErr, setFormErr] = useState("");
  const [busy, setBusy] = useState("");     // 卡片操作中的 provider id
  const [msg, setMsg] = useState(null);     // {type:'ok'|'err', text}

  const refresh = async () => {
    try {
      const s = await fetchSettings();
      setProviders(s.providers);
      setActiveId(s.active_id);
      setLlm(s.llm);
      setLoadErr("");
    } catch (e) {
      setLoadErr(e.message);
    }
  };

  useEffect(() => { refresh(); }, []);

  const flash = (type, text) => setMsg({ type, text });

  const startAdd = () => {
    setForm({ id: null, name: "", base_url: "", api_key: "", model: "" });
    setFormErr("");
    setMsg(null);
  };

  const startEdit = (p) => {
    setForm({ id: p.id, name: p.name, base_url: p.base_url, api_key: "", model: p.model });
    setFormErr("");
    setMsg(null);
  };

  /** 表单提交：action='verify' 先验证再保存（失败不落盘）；'save' 仅保存 */
  const submitForm = async (action) => {
    setFormBusy(action);
    setFormErr("");
    try {
      const saved = await saveProvider({
        ...form, api_key: form.api_key.trim(), verify: action === "verify",
      });
      setForm(null);
      await refresh();
      flash("ok", `供应商「${saved.name}」已保存`);
    } catch (e) {
      setFormErr(e.message);
    } finally {
      setFormBusy("");
    }
  };

  const doActivate = async (p) => {
    setBusy(p.id);
    setMsg(null);
    try {
      await activateProvider(p.id);
      await refresh();
      flash("ok", `已切换到「${p.name}」，新对话即用该模型`);
    } catch (e) {
      flash("err", e.message);
    } finally {
      setBusy("");
    }
  };

  const doVerify = async (p) => {
    setBusy(p.id);
    setMsg(null);
    try {
      const r = await verifyProvider({ id: p.id });
      flash(r.ok ? "ok" : "err", `「${p.name}」${r.message}`);
    } catch (e) {
      flash("err", e.message);
    } finally {
      setBusy("");
    }
  };

  const doVerifyActive = async () => {
    const p = providers?.find((x) => x.id === activeId);
    if (p) return doVerify(p);
    if (providers?.length) return doVerify(providers[0]);
    flash("err", "还没有已保存的供应商，请先在下方添加");
  };

  const doDelete = async (p) => {
    if (!window.confirm(`确定删除供应商「${p.name}」？`)) return;
    setBusy(p.id);
    setMsg(null);
    try {
      await deleteProviderApi(p.id);
      await refresh();
      if (form?.id === p.id) setForm(null);
    } catch (e) {
      flash("err", e.message);
    } finally {
      setBusy("");
    }
  };

  const saveLlm = async () => {
    setMsg(null);
    try {
      await saveLLMSettings({
        thinking: llm.thinking,
        temperature: Number(llm.temperature),
        timeout_seconds: Number(llm.timeout_seconds),
        history_rounds: Number(llm.history_rounds),
        history_char_limit: Number(llm.history_char_limit),
      });
      flash("ok", "运行参数已写入 config.toml，下一请求生效");
    } catch (e) {
      flash("err", e.message);
    }
  };

  const numField = (label, key, min, max, step = 1) => (
    <label className="settings-field">
      <span>{label}</span>
      <input type="number" min={min} max={max} step={step} value={llm[key]}
        onChange={(e) => setLlm({ ...llm, [key]: e.target.value })} />
    </label>
  );

  return (
    <div className="settings-panel">
      <div className="settings-head">
        <h2>模型配置</h2>
        <button type="button" className="settings-primary" onClick={doVerifyActive}>
          🔌 测试当前连接
        </button>
      </div>
      {msg && <div className={`settings-msg ${msg.type}`}>{msg.text}</div>}
      {loadErr && <div className="settings-msg err">{loadErr}</div>}

      {/* —— 供应商（cc-switch 式多套存档，一套激活） —— */}
      <section className="settings-card">
        <div className="settings-card-head">
          <h3>供应商</h3>
          <span className="settings-hint">激活 = 写入 .env 立即生效，无需重启服务</span>
        </div>
        {providers === null ? (
          <div className="settings-empty">加载中…</div>
        ) : providers.length === 0 && !form ? (
          <div className="settings-empty">还没有供应商——点击下方「添加供应商」填入 Base URL / API Key / 模型</div>
        ) : (
          providers.map((p) => (
            <div key={p.id} className={`provider-row${p.id === activeId ? " active" : ""}`}>
              <div className="provider-info">
                <span className="provider-name">
                  {p.name}
                  {p.id === activeId && <span className="provider-badge">使用中</span>}
                </span>
                <span className="provider-meta" title={p.base_url}>
                  {p.base_url} · {p.model} · {p.api_key || "无 key"}
                </span>
              </div>
              <div className="provider-acts">
                {p.id !== activeId && (
                  <button type="button" disabled={busy === p.id} onClick={() => doActivate(p)}>
                    使用
                  </button>
                )}
                <button type="button" disabled={busy === p.id} onClick={() => doVerify(p)}>
                  {busy === p.id ? "…" : "测试"}
                </button>
                <button type="button" onClick={() => startEdit(p)}>编辑</button>
                <button type="button" className="provider-del" disabled={busy === p.id}
                  onClick={() => doDelete(p)}>
                  删除
                </button>
              </div>
            </div>
          ))
        )}
        {form ? (
          <div className="provider-form">
            <div className="settings-field">
              <span>名称</span>
              <input value={form.name} maxLength={40} placeholder="如：生产-Qwen / 备用-GPT"
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="settings-field">
              <span>Base URL</span>
              <input value={form.base_url} placeholder="https://api.example.com/v1"
                onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            </div>
            <div className="settings-field">
              <span>API Key</span>
              <input type="password" value={form.api_key} autoComplete="new-password"
                placeholder={form.id ? "留空沿用已存 key" : "sk-…（本地无鉴权可留空）"}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
            </div>
            <div className="settings-field">
              <span>模型</span>
              <input value={form.model} placeholder="如 qwen3827b / gpt-4o"
                onChange={(e) => setForm({ ...form, model: e.target.value })} />
            </div>
            {formErr && <div className="settings-msg err">{formErr}</div>}
            <div className="provider-form-acts">
              <button type="button" className="settings-primary" disabled={!!formBusy}
                onClick={() => submitForm("verify")}>
                {formBusy === "verify" ? "验证中…" : "保存并验证"}
              </button>
              <button type="button" disabled={!!formBusy} onClick={() => submitForm("save")}>
                {formBusy === "save" ? "保存中…" : "仅保存"}
              </button>
              <button type="button" disabled={!!formBusy} onClick={() => setForm(null)}>
                取消
              </button>
            </div>
          </div>
        ) : (
          <button type="button" className="provider-add" onClick={startAdd}>＋ 添加供应商</button>
        )}
      </section>

      {/* —— 运行参数（config.toml） —— */}
      <section className="settings-card">
        <div className="settings-card-head">
          <h3>运行参数</h3>
          <span className="settings-hint">持久化到项目根 config.toml</span>
        </div>
        {llm === null ? (
          <div className="settings-empty">加载中…</div>
        ) : (
          <>
            <div className="settings-grid">
              <label className="settings-field">
                <span>思考程度</span>
                <select value={llm.thinking}
                  onChange={(e) => setLlm({ ...llm, thinking: e.target.value })}>
                  {THINKING_OPTS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                </select>
              </label>
              {numField("温度", "temperature", 0, 2, 0.1)}
              {numField("超时（秒）", "timeout_seconds", 10, 1200, 10)}
              {numField("上下文·历史轮数", "history_rounds", 1, 20)}
              {numField("上下文·单条截断（字）", "history_char_limit", 50, 2000, 50)}
            </div>
            <div className="settings-hint">
              思考程度非「关闭」时以 reasoning_effort 附加请求——若供应商不支持该参数
              （对话报 4xx），请切回「关闭」。上下文两项控制会话记忆注入的对话量。
            </div>
            <div className="provider-form-acts">
              <button type="button" className="settings-primary" onClick={saveLlm}>
                保存参数
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
