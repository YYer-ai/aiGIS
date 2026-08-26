import React from "react";
import ChatPanel from "./ChatPanel.jsx";

export default function App() {
  return (
    <div className="console">
      <ChatPanel onResult={() => {/* Task 4: 地图渲染 */}} />
      <div className="map-panel" id="map-panel">MapPanel 占位</div>
    </div>
  );
}
