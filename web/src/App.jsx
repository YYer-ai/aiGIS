import React, { useEffect, useState } from "react";
import ChatPanel from "./ChatPanel.jsx";
import MapPanel from "./MapPanel.jsx";
import { deleteLayer, fetchLayerGeojson, fetchLayers, saveLayer } from "./api.js";

export default function App() {
  const [layers, setLayers] = useState([]);
  // layer: {id, name, geojson, visible, persistent, layerName?, style?}
  //   persistent: ♻ 持久图层（make 流产物 / 图层库加载 / 已保存），layerName 为 registry 表名
  //   style: {color, opacity, radius, width, classify:{column}, gradient:{column}, label:{column}}

  // 地图按需出现：首个图层到达后 MapPanel 挂载（首次建 map）；此后图层删空也保留地图，
  // 避免收回/再滑入闪烁——刷新页面才回到无地图全宽对话态
  const [mapOpened, setMapOpened] = useState(false);
  useEffect(() => {
    if (layers.length > 0) setMapOpened(true);
  }, [layers.length]);

  // 查询成功且含几何要素时叠加为新图层（计数类无 geometry 由 ChatPanel 摘要展示，不加图层）；
  // make 流结果（columns=["layer_name","label"] 约定）已是持久图层直接标 ♻，
  // 并异步取全量 geojson（含属性列）替换 500 条采样，供分类设色/标注选择属性；
  // 上限 10 层，超限静默截断最旧图层
  const handleResult = (res, question) => {
    if (!res.ok || !res.geojson?.features?.some((f) => f.geometry)) return;
    const isMake = res.columns?.length === 2 && res.columns[0] === "layer_name" &&
      res.columns[1] === "label" && Array.isArray(res.sample_rows?.[0]);
    const id = Date.now();
    const layer = isMake
      ? {
          id, name: res.sample_rows[0][1] || res.sample_rows[0][0],
          geojson: res.geojson, visible: true, persistent: true,
          layerName: res.sample_rows[0][0], style: null,
        }
      : {
          id, name: question.slice(0, 12), geojson: res.geojson,
          visible: true, persistent: false, style: null,
        };
    setLayers((prev) => [...prev, layer].slice(-10));
    if (isMake) {
      fetchLayerGeojson(layer.layerName)
        .then((gj) => setLayers((ls) => ls.map((l) => (l.id === id ? { ...l, geojson: gj } : l))))
        .catch(() => { /* 全量补全失败：保留采样展示，不影响主流程 */ });
    }
  };

  // 样式编辑：整体替换 style（null=重置回随机默认色）
  const handleStyle = (id, style) =>
    setLayers((ls) => ls.map((l) => (l.id === id ? { ...l, style } : l)));

  // 临时层 → 持久层：表名前端生成（合法字符集），标签可编辑；成功后卡片转 ♻
  const handlePersist = async (layer, label) => {
    const body = await saveLayer({
      name: `saved_${Date.now().toString(36)}`,
      label: label || layer.name,
      geojson: layer.geojson,
    });
    setLayers((ls) => ls.map((l) => (l.id === layer.id
      ? { ...l, persistent: true, layerName: body.layer_name, name: body.label || l.name }
      : l)));
    return body;
  };

  // 图层库加载：registry geojson → persistent 层入列（与查询结果同管线渲染）
  const handleLoadLibrary = async (layerName, label) => {
    const geojson = await fetchLayerGeojson(layerName);
    setLayers((prev) => [...prev, {
      id: Date.now(), name: label || layerName, geojson, visible: true,
      persistent: true, layerName, style: null,
    }].slice(-10));
  };

  // 图层库删除：DROP + registry 注销；同时移除画布上引用它的图层卡片
  const handleDeleteLibrary = async (layerName) => {
    await deleteLayer(layerName);
    setLayers((ls) => ls.filter((l) => l.layerName !== layerName));
  };

  return (
    <div className={`console${mapOpened ? " has-map" : ""}`}>
      <ChatPanel onResult={handleResult} />
      {mapOpened && (
        <MapPanel
          layers={layers}
          onToggle={(id) =>
            setLayers((ls) => ls.map((l) => (l.id === id ? { ...l, visible: !l.visible } : l)))
          }
          onRemove={(id) => setLayers((ls) => ls.filter((l) => l.id !== id))}
          onStyle={handleStyle}
          onPersist={handlePersist}
          listLibrary={fetchLayers}
          onLoadLibrary={handleLoadLibrary}
          onDeleteLibrary={handleDeleteLibrary}
        />
      )}
    </div>
  );
}
