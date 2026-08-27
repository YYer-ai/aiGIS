import React, { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";

// 底图瓦片：默认高德（OSM 国内不可达）；海外环境可把下方 tiles 换成 [OSM_TILE_URL]
const AMAP_TILE_URL =
  "https://webrd04.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}";
const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

// —— 纯工具（导出便于复用/测试） ——

/** 遍历全部 features 统计出现的几何类型（Point/LineString/Polygon，含 Multi*），支持混合类型 */
export function geometryKinds(geojson) {
  const kinds = new Set();
  for (const f of geojson?.features || []) {
    const t = f?.geometry?.type;
    if (t === "Point" || t === "MultiPoint") kinds.add("pt");
    else if (t === "LineString" || t === "MultiLineString") kinds.add("ln");
    else if (t === "Polygon" || t === "MultiPolygon") kinds.add("pg");
  }
  return kinds;
}

/** 递归遍历 coordinates 求 bbox：[[minX,minY],[maxX,maxY]]，无有效坐标返回 null */
export function bboxOf(geojson) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const walk = (c) => {
    if (typeof c[0] === "number" && typeof c[1] === "number") {
      if (c[0] < minX) minX = c[0];
      if (c[0] > maxX) maxX = c[0];
      if (c[1] < minY) minY = c[1];
      if (c[1] > maxY) maxY = c[1];
      return;
    }
    for (const sub of c) walk(sub);
  };
  for (const f of geojson?.features || []) {
    if (f?.geometry?.coordinates) walk(f.geometry.coordinates);
  }
  return minX === Infinity ? null : [[minX, minY], [maxX, maxY]];
}

/** 图层 id（`lyr-${id}`）对应的全部 MapLibre 子图层 id */
const subLayerIds = (srcId) => [
  `${srcId}-pt`,
  `${srcId}-ln`,
  `${srcId}-pg`,
  `${srcId}-pg-outline`,
];

const TYPE_FILTER = {
  pt: ["==", "$type", "Point"],
  ln: ["==", "$type", "LineString"],
  pg: ["==", "$type", "Polygon"],
};

/** popup HTML 转义（属性键值均来自 DB，防注入） */
const escapeHtml = (v) =>
  String(v).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** 要素点击 → 属性键值表 popup（pt/ln/pg 子层共用） */
const onFeatureClick = (e) => {
  const f = e.features?.[0];
  if (!f) return;
  const rows = Object.entries(f.properties || {})
    .map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(v)}</td></tr>`)
    .join("");
  const html = rows ? `<table class="popup-table">${rows}</table>` : "<i>无属性</i>";
  new maplibregl.Popup({ closeButton: false, maxWidth: "300px" })
    .setLngLat(e.lngLat)
    .setHTML(html)
    .addTo(e.target);
};

/**
 * 地图画布：MapLibre 底图 + 查询结果 GeoJSON 图层叠加与管理。
 * props: layers=[{id, name, geojson, visible, color?}], onToggle(id), onRemove(id)
 */
export default function MapPanel({ layers = [], onToggle, onRemove }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const [mapReady, setMapReady] = useState(false);
  const colorsRef = useRef({}); // 图层 id -> 颜色，跨渲染/开关保持稳定

  // 随机色存 ref（父组件未传 color 时兜底），避免每次渲染变色
  const colorOf = (l) =>
    l.color ??
    (colorsRef.current[l.id] ??= `hsl(${Math.floor(Math.random() * 360)}, 70%, 60%)`);

  // Step 1: 初始化地图（useRef 防严格模式重复初始化；cleanup 移除实例）
  useEffect(() => {
    if (mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: { basemap: { type: "raster", tiles: [AMAP_TILE_URL], tileSize: 256 } },
        layers: [{ id: "basemap", type: "raster", source: "basemap" }],
      },
      center: [116.4, 39.9],
      zoom: 10,
    });
    map.on("load", () => setMapReady(true));
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      setMapReady(false);
    };
  }, []);

  // Step 2: 图层同步——新增加 source/子图层+fitBounds；visible 切 setLayoutProperty；删除移除
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;

    const wanted = new Set(layers.map((l) => `lyr-${l.id}`));

    // 移除已不存在的图层（先删子图层再删 source）
    for (const srcId of Object.keys(map.getStyle().sources)) {
      if (!srcId.startsWith("lyr-") || wanted.has(srcId)) continue;
      for (const sub of subLayerIds(srcId)) {
        if (map.getLayer(sub)) map.removeLayer(sub);
      }
      map.removeSource(srcId);
      delete colorsRef.current[srcId.slice(4)];
    }

    for (const l of layers) {
      const srcId = `lyr-${l.id}`;
      const color = colorOf(l);
      const isNew = !map.getSource(srcId);
      if (isNew) {
        map.addSource(srcId, { type: "geojson", data: l.geojson });
        const kinds = geometryKinds(l.geojson);
        if (kinds.has("pt")) {
          map.addLayer({
            id: `${srcId}-pt`, type: "circle", source: srcId, filter: TYPE_FILTER.pt,
            paint: {
              "circle-color": color, "circle-radius": 5,
              "circle-stroke-color": "#fff", "circle-stroke-width": 1,
            },
          });
        }
        if (kinds.has("ln")) {
          map.addLayer({
            id: `${srcId}-ln`, type: "line", source: srcId, filter: TYPE_FILTER.ln,
            paint: { "line-color": color, "line-width": 2 },
          });
        }
        if (kinds.has("pg")) {
          map.addLayer({
            id: `${srcId}-pg`, type: "fill", source: srcId, filter: TYPE_FILTER.pg,
            paint: { "fill-color": color, "fill-opacity": 0.3 },
          });
          map.addLayer({
            id: `${srcId}-pg-outline`, type: "line", source: srcId, filter: TYPE_FILTER.pg,
            paint: { "line-color": color, "line-width": 1.5 },
          });
        }
        // 要素交互（仅新图层绑一次，只挂 pt/ln/pg——pg-outline 不挂避免边界处重复弹窗）：
        // 点击属性表 popup + hover 手型
        for (const sub of [`${srcId}-pt`, `${srcId}-ln`, `${srcId}-pg`]) {
          if (!map.getLayer(sub)) continue;
          map.on("click", sub, onFeatureClick);
          map.on("mouseenter", sub, () => { map.getCanvas().style.cursor = "pointer"; });
          map.on("mouseleave", sub, () => { map.getCanvas().style.cursor = ""; });
        }
      }
      // 可见性开关（对全部子图层生效）
      const vis = l.visible === false ? "none" : "visible";
      for (const sub of subLayerIds(srcId)) {
        if (map.getLayer(sub)) map.setLayoutProperty(sub, "visibility", vis);
      }
      // 新增图层飞行到其范围
      if (isNew) {
        const bbox = bboxOf(l.geojson);
        if (bbox) map.fitBounds(bbox, { padding: 48, maxZoom: 15, duration: 600 });
      }
    }
  }, [layers, mapReady]);

  // Step 3: 图层卡片列表（右上角浮层；上限 10 由父组件控制）
  return (
    <div className="map-panel" id="map-panel">
      <div className="map-canvas" ref={containerRef} />
      <div className="layer-cards">
        {layers.length === 0 ? (
          <div className="layer-empty">查询结果将显示在这里</div>
        ) : (
          layers.map((l) => (
            <div key={l.id} className="layer-card">
              <span className="layer-dot" style={{ background: colorOf(l) }} />
              <span className="layer-name" title={l.name}>{l.name}</span>
              <span className="layer-count">{l.geojson?.features?.length ?? 0} 行</span>
              <label className="layer-eye" title={l.visible === false ? "显示图层" : "隐藏图层"}>
                <input
                  type="checkbox"
                  checked={l.visible !== false}
                  onChange={() => onToggle && onToggle(l.id)}
                />
                <span className="layer-eye-track" />
              </label>
              <button
                className="layer-del"
                title="删除图层"
                onClick={() => onRemove && onRemove(l.id)}
              >
                ×
              </button>
            </div>
          ))
        )}
        {layers.length >= 10 && (
          <div className="layer-cap">已达 10 层上限，最早的图层已被移除</div>
        )}
      </div>
    </div>
  );
}
