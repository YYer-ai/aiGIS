import React, { useState } from "react";
import ChatPanel from "./ChatPanel.jsx";
import MapPanel from "./MapPanel.jsx";

export default function App() {
  const [layers, setLayers] = useState([]);

  // 查询成功且含几何要素时叠加为新图层（计数类无 geometry 由 ChatPanel 摘要展示，不加图层）；
  // 上限 10 层，超限静默截断最旧图层
  const handleResult = (res, question) => {
    if (res.ok && res.geojson?.features?.some((f) => f.geometry)) {
      setLayers((prev) =>
        [...prev, { id: Date.now(), name: question.slice(0, 12), geojson: res.geojson, visible: true }].slice(-10)
      );
    }
  };

  return (
    <div className="console">
      <ChatPanel onResult={handleResult} />
      <MapPanel
        layers={layers}
        onToggle={(id) =>
          setLayers((ls) => ls.map((l) => (l.id === id ? { ...l, visible: !l.visible } : l)))
        }
        onRemove={(id) => setLayers((ls) => ls.filter((l) => l.id !== id))}
      />
    </div>
  );
}
