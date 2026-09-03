import React, { useState } from "react";

// 会话时间显示：今天 HH:mm；今年 M月d日；更早 YYYY-MM-DD（后端存 UTC isoformat）
function fmtTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (sameDay) return hm;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// 左侧会话窄栏：列表（点击切换/当前高亮/hover 删）＋新建＋底部折叠；
// 折叠后只剩竖条展开钮
export default function SessionSidebar({ sessions, currentId, onSelect, onNew, onDelete }) {
  const [collapsed, setCollapsed] = useState(false);

  if (collapsed) {
    return (
      <aside className="session-sidebar sidebar-collapsed">
        <button type="button" className="sidebar-expand"
          onClick={() => setCollapsed(false)} title="展开会话栏">⟩</button>
      </aside>
    );
  }

  return (
    <aside className="session-sidebar">
      <div className="sidebar-head">
        <span className="sidebar-title">会话</span>
        <button type="button" className="sidebar-new" onClick={onNew} title="新建会话">＋</button>
      </div>
      <div className="session-list">
        {sessions.length === 0 && (
          <div className="session-empty">开始对话自动创建</div>
        )}
        {sessions.map((s) => (
          <div key={s.id}
            className={`session-item${s.id === currentId ? " active" : ""}`}
            onClick={() => onSelect(s.id)}>
            <div className="session-item-title">{s.title || "新会话"}</div>
            <div className="session-item-time">{fmtTime(s.updated_at)}</div>
            <button type="button" className="session-del" title="删除会话"
              onClick={(e) => {
                e.stopPropagation();
                if (window.confirm(`删除会话「${s.title || "新会话"}」？删除后不可恢复。`)) onDelete(s.id);
              }}>×</button>
          </div>
        ))}
      </div>
      <button type="button" className="sidebar-collapse" onClick={() => setCollapsed(true)}>
        ⟨ 收起
      </button>
    </aside>
  );
}
