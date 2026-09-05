import React, { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";

// —— 底图库（工具条"底图"菜单切换；preview 为菜单缩略块的 CSS 渐变） ——
// crs: 底图坐标系。Carto/Esri/OpenFreeMap 均为 WGS-84，与 GeoJSON 数据天然对齐；
// 高德系为 GCJ-02（火星坐标），选中时叠加图层坐标经 wgs2gcj 实时纠偏对齐（见 dataFor）
const CARTO_TILES = (style) => ["a", "b", "c", "d"].map(
  (s) => `https://${s}.basemaps.cartocdn.com/rastertiles/${style}/{z}/{x}/{y}@2x.png`);
const ESRI_SAT_TILES =
  ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"];
const AMAP_SAT_TILES = [1, 2, 3, 4].map(
  (n) => `https://webst0${n}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}`);
const AMAP_STREET_TILES = [1, 2, 3, 4].map(
  (n) => `https://webrd0${n}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}`);
// 地形 DEM（Mapzen/AWS Open Data terrarium 高程瓦片）与 3D 建筑矢量瓦片（OpenFreeMap）
const TERRAIN_DEM_TILES = ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"];
const OPENFREEMAP_VEC_URL = "https://tiles.openfreemap.org/planet";

const BASEMAPS = [
  { id: "voyager", label: "街区图", kind: "raster", tiles: CARTO_TILES("voyager"), crs: "wgs84",
    preview: "linear-gradient(135deg,#f8f4ec,#cfe3c9 55%,#f0d8a8)",
    attribution: "© OpenStreetMap contributors © CARTO" },
  { id: "positron", label: "浅色图", kind: "raster", tiles: CARTO_TILES("positron"), crs: "wgs84",
    preview: "linear-gradient(135deg,#fafaf7,#e8e8e2 55%,#d8d8d2)",
    attribution: "© OpenStreetMap contributors © CARTO" },
  { id: "dark", label: "暗色图", kind: "raster", tiles: CARTO_TILES("dark_all"), crs: "wgs84",
    preview: "linear-gradient(135deg,#17171c,#20222b 55%,#0f1218)",
    attribution: "© OpenStreetMap contributors © CARTO" },
  { id: "satellite", label: "卫星图", kind: "raster", tiles: ESRI_SAT_TILES, crs: "wgs84",
    preview: "linear-gradient(135deg,#1e3a1c,#2e5426 55%,#12325a)",
    attribution: "Esri, Maxar, Earthstar Geographics" },
  { id: "terrain3d", label: "3D 地形", kind: "terrain", tiles: ESRI_SAT_TILES, crs: "wgs84",
    preview: "linear-gradient(160deg,#3a4a2e 20%,#6b6350 55%,#241f1a)",
    attribution: "Esri, Maxar；地形: Mapzen/AWS Open Data" },
  { id: "buildings3d", label: "3D 建筑", kind: "buildings", tiles: ESRI_SAT_TILES, crs: "wgs84",
    preview: "linear-gradient(180deg,#2b3038 40%,#8f8a80 41%,#454a52)",
    attribution: "Esri, Maxar；建筑: OpenFreeMap/OSM" },
  { id: "amap", label: "高德街道", kind: "raster", tiles: AMAP_STREET_TILES, crs: "gcj02",
    preview: "linear-gradient(135deg,#f2efe9,#cfe0c8 55%,#f7e8c8)",
    attribution: "© 高德" },
  { id: "amap-sat", label: "高德卫星", kind: "raster", tiles: AMAP_SAT_TILES, crs: "gcj02",
    preview: "linear-gradient(135deg,#22401f,#33562a 55%,#14304f)",
    attribution: "© 高德" },
];
const DEFAULT_BASEMAP = "amap"; // 高德街道：国内可达免 key；CARTO 免费瓦片 2026-09 起要求
// API key（无 key 大面积黑块水印），数据叠加经 wgs2gcj 自动纠偏对齐
const BASEMAP_STORAGE_KEY = "aigis:basemapId";

// —— WGS-84 → GCJ-02（国测局加密坐标）公开近似算法：中国境内偏差 <2m，境外原样返回 ——
const GCJ_A = 6378245.0;
const GCJ_EE = 0.00669342162296594323;

const gcjOffsetLat = (x, y) => {
  let r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  r += (20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2 / 3;
  r += (20 * Math.sin(y * Math.PI) + 40 * Math.sin(y / 3 * Math.PI)) * 2 / 3;
  r += (160 * Math.sin(y / 12 * Math.PI) + 320 * Math.sin(y * Math.PI / 30)) * 2 / 3;
  return r;
};
const gcjOffsetLng = (x, y) => {
  let r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  r += (20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2 / 3;
  r += (20 * Math.sin(x * Math.PI) + 40 * Math.sin(x / 3 * Math.PI)) * 2 / 3;
  r += (150 * Math.sin(x / 12 * Math.PI) + 300 * Math.sin(x / 30 * Math.PI)) * 2 / 3;
  return r;
};

/** [lng, lat]（WGS-84）→ [lng, lat]（GCJ-02）；境外坐标不受加密影响，直接原值返回 */
export function wgs2gcj(lng, lat) {
  if (lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271) return [lng, lat];
  const dLat = gcjOffsetLat(lng - 105, lat - 35);
  const dLng = gcjOffsetLng(lng - 105, lat - 35);
  const radLat = (lat / 180) * Math.PI;
  let magic = 1 - GCJ_EE * Math.sin(radLat) ** 2;
  const sqrtMagic = Math.sqrt(magic);
  magic = (dLat * 180) / ((GCJ_A * (1 - GCJ_EE)) / (magic * sqrtMagic) * Math.PI);
  const d = (dLng * 180) / ((GCJ_A / sqrtMagic) * Math.cos(radLat) * Math.PI);
  return [lng + d, lat + magic];
}

/** GeoJSON 整体坐标平移（浅克隆，不改原对象）：fn([lng,lat])→[lng,lat]，
 * 保留坐标第 3 位起的高程等附加值；供 GCJ-02 底图下纠偏叠加数据 */
export function shiftGeojson(geojson, fn) {
  const walk = (c) => {
    if (typeof c[0] === "number" && typeof c[1] === "number") {
      const t = fn(c);
      return [t[0], t[1], ...c.slice(2)];
    }
    return c.map(walk);
  };
  if (!geojson?.features) return geojson;
  return {
    ...geojson,
    features: geojson.features.map((f) => (f?.geometry
      ? { ...f, geometry: { ...f.geometry, coordinates: walk(f.geometry.coordinates) } }
      : f)),
  };
}

/** 底图应用：清旧 bm-* 图层/source（含地形）→ 按底图类型加栅格/DEM/矢量建筑层；
 * 全部插到第一个数据图层（lyr-*）之下，保证叠加结果始终在底图之上；幂等可重入 */
function applyBasemap(map, bm) {
  map.setTerrain(null);
  for (const id of ["bm-buildings", "bm-hillshade", "bm-base"]) {
    if (map.getLayer(id)) map.removeLayer(id);
  }
  for (const id of ["bm-base", "bm-dem", "bm-vec"]) {
    if (map.getSource(id)) map.removeSource(id);
  }
  if (!bm) return;
  const before = map.getStyle().layers.find((l) => l.id.startsWith("lyr-"))?.id;
  if (bm.tiles) {
    map.addSource("bm-base",
      { type: "raster", tiles: bm.tiles, tileSize: 256, attribution: bm.attribution });
    map.addLayer({ id: "bm-base", type: "raster", source: "bm-base" }, before);
  }
  if (bm.kind === "terrain") {
    map.addSource("bm-dem", {
      type: "raster-dem", tiles: TERRAIN_DEM_TILES, encoding: "terrarium",
      tileSize: 256, maxzoom: 15, attribution: "Terrain: Mapzen, AWS Open Data",
    });
    map.setTerrain({ source: "bm-dem", exaggeration: 1.25 });
    map.addLayer({
      id: "bm-hillshade", type: "hillshade", source: "bm-dem",
      paint: { "hillshade-exaggeration": 0.35, "hillshade-shadow-color": "#22242c" },
    }, before);
  }
  if (bm.kind === "buildings") {
    map.addSource("bm-vec", { type: "vector", url: OPENFREEMAP_VEC_URL });
    map.addLayer({
      id: "bm-buildings", type: "fill-extrusion", source: "bm-vec",
      "source-layer": "building", minzoom: 14,
      paint: {
        "fill-extrusion-color": "#cfc9c2",
        "fill-extrusion-height": ["coalesce", ["get", "render_height"], 6],
        "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
        "fill-extrusion-opacity": 0.92,
      },
    }, before);
  }
}

// 标注字形（SDF pbf）：OpenFreeMap 公共字体服务（Noto Sans，覆盖拉丁/数字）。
// 公共服务暂无 CJK pbf——中文列标注走 maplibregl.Marker DOM 方案（见 syncLabelMarkers）
const GLYPHS_URL = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const LABEL_FONT = ["Noto Sans Regular"];
const LABEL_MARKER_LIMIT = 80; // 中文标注 Marker 上限（DOM 元素较多，采样标注即可）

// 分类设色色板（前 10 个唯一值各配一色，超出回落基础色）；数值渐变两端色（蓝→红）
const CATEGORICAL_10 = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
  "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"];
const GRADIENT_STOPS = ["#313695", "#a50026"];

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

/** 列值是否全 ASCII（采样前 sample 个非空值）：true 走 symbol 子层（性能优），
 * false（含中文等）走 DOM Marker 标注——公共 pbf 字形服务无 CJK，symbol 渲染不出 */
export function columnIsAscii(geojson, column, sample = 20) {
  let n = 0;
  for (const f of geojson?.features || []) {
    const v = f?.properties?.[column];
    if (v == null) continue;
    if (!/^[\x00-\x7F]*$/.test(String(v))) return false;
    if (++n >= sample) break;
  }
  return true;
}

/** 几何首个坐标对 [lng, lat]：Point 直取；线/面/Multi* 递归剥壳取第一对数字 */
export function firstCoord(geometry) {
  let c = geometry?.coordinates;
  while (Array.isArray(c) && typeof c[0] !== "number") c = c[0];
  return (Array.isArray(c) && typeof c[0] === "number" && typeof c[1] === "number")
    ? [c[0], c[1]] : null;
}

/** 属性列清单：features 中至少出现过一次非空值的键（保持出现顺序），供设色/标注下拉 */
export function propColumns(geojson) {
  const keys = [];
  const seen = new Set();
  for (const f of geojson?.features || []) {
    for (const [k, v] of Object.entries(f?.properties || {})) {
      if (v == null || seen.has(k)) continue;
      seen.add(k);
      keys.push(k);
    }
  }
  return keys;
}

/** 列的唯一值（前 max 个，仅基础类型；按 typeof+串值去重，防 1 与 "1" 混入同一键） */
export function uniqueValues(geojson, column, max = 10) {
  const out = [];
  const seen = new Set();
  for (const f of geojson?.features || []) {
    const v = f?.properties?.[column];
    const t = typeof v;
    if (v == null || (t !== "string" && t !== "number" && t !== "boolean")) continue;
    const key = `${t}:${String(v)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(String(v)); // match 表达式标签统一为字符串
    if (out.length >= max) break;
  }
  return out;
}

/** 列的数值范围（可 Number() 化的值），无数值返回 null */
export function numericRange(geojson, column) {
  let min = Infinity, max = -Infinity;
  for (const f of geojson?.features || []) {
    const n = Number(f?.properties?.[column]);
    if (Number.isFinite(n)) {
      if (n < min) min = n;
      if (n > max) max = n;
    }
  }
  return min === Infinity ? null : { min, max };
}

/** style → 颜色数据驱动表达式：classify（match 唯一值分色）优先于
 * gradient（数值线性插值），都未启用时回落纯色 fallback */
export function colorExpression(style, geojson, fallback) {
  const col = style?.classify?.column;
  if (col) {
    const uniq = uniqueValues(geojson, col, CATEGORICAL_10.length);
    if (uniq.length) {
      const stops = [];
      uniq.forEach((v, i) => stops.push(v, CATEGORICAL_10[i]));
      return ["match", ["to-string", ["get", col]], ...stops, fallback];
    }
  }
  const gcol = style?.gradient?.column;
  if (gcol) {
    const r = numericRange(geojson, gcol);
    if (r && r.max > r.min) {
      return ["interpolate", ["linear"], ["to-number", ["get", gcol]],
        r.min, GRADIENT_STOPS[0], r.max, GRADIENT_STOPS[1]];
    }
    if (r) return GRADIENT_STOPS[1]; // min==max：单色（插值停点不可重复）
  }
  return fallback;
}

/** 图层 id（`lyr-${id}`）对应的全部 MapLibre 子图层 id（含标注 symbol 层） */
const subLayerIds = (srcId) => [
  `${srcId}-pt`,
  `${srcId}-ln`,
  `${srcId}-pg`,
  `${srcId}-pg-outline`,
  `${srcId}-label`,
];

const TYPE_FILTER = {
  pt: ["==", "$type", "Point"],
  ln: ["==", "$type", "LineString"],
  pg: ["==", "$type", "Polygon"],
};

/** style → 各子层受管 paint 属性（固定项如 circle-stroke 不在此列，避免整对象覆盖） */
function paintFor(kind, style, expr) {
  const s = style || {};
  switch (kind) {
    case "pt":
      return { "circle-color": expr, "circle-radius": s.radius ?? 5,
               "circle-opacity": s.opacity ?? 1 };
    case "ln":
      return { "line-color": expr, "line-width": s.width ?? 2,
               "line-opacity": s.opacity ?? 1 };
    case "pg":
      return { "fill-color": expr, "fill-opacity": s.opacity ?? 0.3 };
    case "pgln":
      return { "line-color": expr, "line-width": 1.5 };
  }
}

/** 标注 symbol 子层随 style.label 同步：无列或列含非 ASCII（无 CJK 字形，走 Marker）则删；
 * 全 ASCII 则加/改 text-field（text-field 属 layout——无结构变化走 setLayoutProperty，
 * 避免整层重建） */
function syncLabelLayer(map, srcId, style, geojson) {
  const id = `${srcId}-label`;
  const col = style?.label?.column;
  if (col && columnIsAscii(geojson, col)) {
    if (!map.getLayer(id)) {
      map.addLayer({
        id, type: "symbol", source: srcId, minzoom: 9,
        layout: {
          "text-field": ["to-string", ["get", col]], "text-font": LABEL_FONT,
          "text-size": 12, "text-offset": [0, 1.1], "text-anchor": "top",
        },
        paint: {
          "text-color": "#1b1d23", "text-halo-color": "#fff", "text-halo-width": 1.3,
        },
      });
    } else {
      map.setLayoutProperty(id, "text-field", ["to-string", ["get", col]]);
    }
  } else if (map.getLayer(id)) {
    map.removeLayer(id);
  }
}

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

/** hover 手型（pt/ln/pg 子层共用；具名以便删除图层时成对 off，防监听器积累） */
const onFeatureEnter = (e) => { e.target.getCanvas().style.cursor = "pointer"; };
const onFeatureLeave = (e) => { e.target.getCanvas().style.cursor = ""; };

// —— 属性列下拉（分类设色/渐变/标注共用；"关闭"值为空串） ——
function ColumnSelect({ cols, value, onChange }) {
  return (
    <select className="style-select" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">关闭</option>
      {cols.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}

// —— 行内样式编辑抽屉：基础样式 + 数据驱动设色/标注 + 重置 + 保存为图层 ——
function StyleEditor({ layer, onStyle, onPersist }) {
  const s = layer.style || {};
  const cols = propColumns(layer.geojson);
  const set = (patch) => onStyle(layer.id, { ...(layer.style || {}), ...patch });
  const [saveForm, setSaveForm] = useState(false);
  const [saveLabel, setSaveLabel] = useState(layer.name);
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveError, setSaveError] = useState("");

  const doSave = async () => {
    setSaveBusy(true);
    setSaveError("");
    try {
      await onPersist(layer, saveLabel.trim() || layer.name);
      setSaveForm(false); // 成功后卡片随 persistent 置位转为 ♻ 标记
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaveBusy(false);
    }
  };

  return (
    <div className="layer-style-editor">
      <div className="style-row">
        <label>颜色</label>
        <input type="color"
          value={/^#[0-9a-fA-F]{6}$/.test(s.color || "") ? s.color : "#ff7f0e"}
          onChange={(e) => set({ color: e.target.value })} />
        {(s.classify || s.gradient) && <span className="style-hint">数据设色生效中</span>}
      </div>
      <div className="style-row">
        <label>透明度</label>
        <input type="range" min="0" max="1" step="0.05" value={s.opacity ?? 1}
          onChange={(e) => set({ opacity: +e.target.value })} />
      </div>
      <div className="style-row">
        <label>点半径</label>
        <input type="range" min="1" max="15" step="1" value={s.radius ?? 5}
          onChange={(e) => set({ radius: +e.target.value })} />
      </div>
      <div className="style-row">
        <label>线宽</label>
        <input type="range" min="0.5" max="8" step="0.5" value={s.width ?? 2}
          onChange={(e) => set({ width: +e.target.value })} />
      </div>
      <div className="style-row">
        <label>分类设色</label>
        <ColumnSelect cols={cols} value={s.classify?.column || ""}
          onChange={(v) => set(v ? { classify: { column: v }, gradient: null } : { classify: null })} />
      </div>
      <div className="style-row">
        <label>数值渐变</label>
        <ColumnSelect cols={cols} value={s.gradient?.column || ""}
          onChange={(v) => set(v ? { gradient: { column: v }, classify: null } : { gradient: null })} />
      </div>
      <div className="style-row">
        <label>标注</label>
        <ColumnSelect cols={cols} value={s.label?.column || ""}
          onChange={(v) => set(v ? { label: { column: v } } : { label: null })} />
      </div>
      <div className="style-actions">
        <button type="button" className="style-reset" onClick={() => onStyle(layer.id, null)}>
          重置样式
        </button>
        {layer.persistent ? (
          <span className="style-persisted" title={layer.layerName ?? ""}>♻ 已在图层库</span>
        ) : saveForm ? (
          <span className="style-save-form">
            <input value={saveLabel} maxLength={40} onChange={(e) => setSaveLabel(e.target.value)}
              placeholder="图层标签" />
            <button type="button" className="style-save-btn" disabled={saveBusy} onClick={doSave}>
              {saveBusy ? "保存中…" : "保存"}
            </button>
            <button type="button" className="style-cancel" disabled={saveBusy}
              onClick={() => { setSaveForm(false); setSaveError(""); }}>
              取消
            </button>
          </span>
        ) : (
          <button type="button" className="style-save-btn" onClick={() => setSaveForm(true)}>
            保存为图层
          </button>
        )}
      </div>
      {saveError && <div className="style-error">{saveError}</div>}
    </div>
  );
}

/**
 * 地图画布：MapLibre 底图 + 查询结果 GeoJSON 图层叠加与管理（M5 制图工作台）。
 * props: layers=[{id, name, geojson, visible, persistent, layerName?, style?}],
 *   onToggle(id), onRemove(id), onStyle(id, style),
 *   onPersist(layer, label)→Promise, listLibrary()→Promise<rows>,
 *   onLoadLibrary(layerName, label)→Promise, onDeleteLibrary(layerName)→Promise
 */
export default function MapPanel({
  layers = [], onToggle, onRemove, onStyle, onPersist, listLibrary,
  onLoadLibrary, onDeleteLibrary,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const [mapReady, setMapReady] = useState(false);
  const colorsRef = useRef({});        // 图层 id -> 颜色，跨渲染/开关保持稳定
  const layerStateRef = useRef({});    // srcId -> {data, sig}：检测数据/样式变化
  const labelMarkersRef = useRef({});  // srcId -> Marker[]：中文列标注（DOM 方案）
  const markerSigRef = useRef({});     // srcId -> {column, visible, data}：变化才重建

  const [expandedId, setExpandedId] = useState(null); // 样式编辑抽屉展开的图层
  const [libOpen, setLibOpen] = useState(false);      // 图层库抽屉
  const [lib, setLib] = useState({ loading: false, error: "", rows: null });
  const [libBusy, setLibBusy] = useState("");         // 正在加载/删除的 layer_name
  const [bmOpen, setBmOpen] = useState(false);        // 底图菜单
  const [basemapId, setBasemapId] = useState(() => {  // 持久化底图选择，非法值回落默认
    const saved = localStorage.getItem(BASEMAP_STORAGE_KEY);
    return BASEMAPS.some((b) => b.id === saved) ? saved : DEFAULT_BASEMAP;
  });

  const gcjCacheRef = useRef({});       // srcId -> {src, out}：wgs→gcj 转换结果缓存（同数据复用）
  const basemap = BASEMAPS.find((b) => b.id === basemapId) || BASEMAPS[0];
  const inGCJ = basemap.crs === "gcj02";

  // 随机色存 ref（父组件未传 color 时兜底），避免每次渲染变色
  const colorOf = (l) =>
    l.color ??
    (colorsRef.current[l.id] ??= `hsl(${Math.floor(Math.random() * 360)}, 70%, 60%)`);

  // —— 中文标注 Marker（列含非 ASCII 时替代 symbol 子层） ——

  const clearLabelMarkers = (srcId) => {
    for (const m of labelMarkersRef.current[srcId] || []) m.remove();
    delete labelMarkersRef.current[srcId];
    delete markerSigRef.current[srcId];
  };

  /** 标注列含非 ASCII → 前 80 个要素建 div Marker（class map-label，白字描边 CSS）；
   * 坐标 Point 直取、线/面取首坐标对（GCJ-02 底图下同步纠偏）。
   * sig（列|可见|数据引用|坐标系）不变则跳过重建 */
  const syncLabelMarkers = (map, srcId, l, crs) => {
    const col = l.style?.label?.column;
    const want = (col && !columnIsAscii(l.geojson, col))
      ? { column: col, visible: l.visible !== false, data: l.geojson, crs } : null;
    const prev = markerSigRef.current[srcId];
    if (prev?.column === want?.column && prev?.visible === want?.visible
        && prev?.data === want?.data && prev?.crs === want?.crs) return;
    clearLabelMarkers(srcId); // 标注关闭/切列/数据变/隐藏/换坐标系：先清再按需重建
    markerSigRef.current[srcId] = want;
    if (!want?.visible) return;
    const markers = [];
    for (const f of (l.geojson?.features || []).slice(0, LABEL_MARKER_LIMIT)) {
      let pos = firstCoord(f?.geometry);
      const v = f?.properties?.[col];
      if (!pos || v == null || v === "") continue;
      if (crs === "gcj02") pos = wgs2gcj(pos[0], pos[1]);
      const el = document.createElement("div");
      el.className = "map-label";
      el.textContent = String(v);
      markers.push(new maplibregl.Marker({ element: el, anchor: "top" })
        .setLngLat(pos).addTo(map));
    }
    labelMarkersRef.current[srcId] = markers;
  };

  // Step 1: 初始化地图（useRef 防严格模式重复初始化；cleanup 移除实例）；
  // preserveDrawingBuffer 供 PNG 导出读取画布，glyphs 供标注 symbol 层渲染文字；
  // 底图不在此写死——由 Step 1.5 的 basemap effect 统一应用（含 localStorage 记忆）
  useEffect(() => {
    if (mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: { version: 8, glyphs: GLYPHS_URL, sources: {}, layers: [] },
      center: [116.4, 39.9],
      zoom: 10,
      preserveDrawingBuffer: true,
    });
    map.on("load", () => setMapReady(true));
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      setMapReady(false);
    };
  }, []);

  // Step 1.5: 底图切换——重建 bm-* 底图层；3D 类底图抬 pitch 展示立体效果，平面类回正。
  // 选择写入 localStorage；幂等，初次 load 与后续切换走同一路径
  useEffect(() => {
    if (!mapReady) return;
    const map = mapRef.current;
    applyBasemap(map, basemap);
    const pitch = (basemap.kind === "terrain" || basemap.kind === "buildings") ? 58 : 0;
    if (map.getPitch() !== pitch) map.easeTo({ pitch, duration: 500 });
  }, [basemapId, mapReady]); // basemap 由 basemapId 派生，此处仅作意图标注

  const chooseBasemap = (id) => {
    setBasemapId(id);
    localStorage.setItem(BASEMAP_STORAGE_KEY, id);
  };

  /** 当前底图下的渲染数据：GCJ-02 底图（高德）返回 wgs→gcj 纠偏副本（按源数据引用缓存），
   *  WGS-84 底图返回原数据——底图与数据坐标系一致，叠加层不再偏移 */
  const dataFor = (srcId, l) => {
    if (!inGCJ) return l.geojson;
    let c = gcjCacheRef.current[srcId];
    if (c?.src === l.geojson) return c.out;
    c = { src: l.geojson, out: shiftGeojson(l.geojson, (p) => wgs2gcj(p[0], p[1])) };
    gcjCacheRef.current[srcId] = c;
    return c.out;
  };

  // Step 2: 图层同步——新增加 source/子图层+fitBounds；数据替换 setData（如 make 层
  // 采样→全量补全）；样式变化 setPaintProperty（classify/gradient/颜色/透明度/半径/线宽）
  // + 标注增删（ASCII 列走 symbol 子层，含中文走 DOM Marker）；visible 切 setLayoutProperty；删除移除。
  // 底图坐标系变化（wgs84↔gcj02）时经 dataFor 重取数据触发 setData 重刷
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;

    const wanted = new Set(layers.map((l) => `lyr-${l.id}`));

    // 样式应用到已存在子层（数据替换后表达式依赖 min/max/唯一值，也需重刷）
    const applyStyle = (srcId, l, expr) => {
      for (const [suffix, kind] of [["pt", "pt"], ["ln", "ln"], ["pg", "pg"], ["pg-outline", "pgln"]]) {
        const id = `${srcId}-${suffix}`;
        if (!map.getLayer(id)) continue;
        for (const [k, v] of Object.entries(paintFor(kind, l.style, expr)))
          map.setPaintProperty(id, k, v);
      }
      syncLabelLayer(map, srcId, l.style, l.geojson);
    };

    // 移除已不存在的图层（先解绑监听器再删子图层和 source；中文标注 Marker 一并清理）
    for (const srcId of Object.keys(map.getStyle().sources)) {
      if (!srcId.startsWith("lyr-") || wanted.has(srcId)) continue;
      for (const sub of subLayerIds(srcId)) {
        map.off("click", sub, onFeatureClick);
        map.off("mouseenter", sub, onFeatureEnter);
        map.off("mouseleave", sub, onFeatureLeave);
        if (map.getLayer(sub)) map.removeLayer(sub);
      }
      map.removeSource(srcId);
      clearLabelMarkers(srcId);
      delete gcjCacheRef.current[srcId];
      delete colorsRef.current[srcId.slice(4)];
      delete layerStateRef.current[srcId];
    }

    for (const l of layers) {
      const srcId = `lyr-${l.id}`;
      const data = dataFor(srcId, l);
      const expr = colorExpression(l.style, l.geojson, colorOf(l));
      const sig = JSON.stringify(l.style ?? null);
      if (!map.getSource(srcId)) {
        map.addSource(srcId, { type: "geojson", data });
        const kinds = geometryKinds(l.geojson);
        if (kinds.has("pt")) {
          map.addLayer({
            id: `${srcId}-pt`, type: "circle", source: srcId, filter: TYPE_FILTER.pt,
            paint: { ...paintFor("pt", l.style, expr),
                     "circle-stroke-color": "#fff", "circle-stroke-width": 1 },
          });
        }
        if (kinds.has("ln")) {
          map.addLayer({
            id: `${srcId}-ln`, type: "line", source: srcId, filter: TYPE_FILTER.ln,
            paint: paintFor("ln", l.style, expr),
          });
        }
        if (kinds.has("pg")) {
          map.addLayer({
            id: `${srcId}-pg`, type: "fill", source: srcId, filter: TYPE_FILTER.pg,
            paint: paintFor("pg", l.style, expr),
          });
          map.addLayer({
            id: `${srcId}-pg-outline`, type: "line", source: srcId, filter: TYPE_FILTER.pg,
            paint: paintFor("pgln", l.style, expr),
          });
        }
        // 要素交互（仅新图层绑一次，只挂 pt/ln/pg——pg-outline 不挂避免边界处重复弹窗）：
        // 点击属性表 popup + hover 手型
        for (const sub of [`${srcId}-pt`, `${srcId}-ln`, `${srcId}-pg`]) {
          if (!map.getLayer(sub)) continue;
          map.on("click", sub, onFeatureClick);
          map.on("mouseenter", sub, onFeatureEnter);
          map.on("mouseleave", sub, onFeatureLeave);
        }
        syncLabelLayer(map, srcId, l.style, l.geojson);
        layerStateRef.current[srcId] = { data, sig };
        // 新增图层飞行到其范围（用纠偏后数据，保证与当前底图对齐）
        const bbox = bboxOf(data);
        if (bbox) map.fitBounds(bbox, { padding: 48, maxZoom: 15, duration: 600 });
      } else {
        const st = layerStateRef.current[srcId];
        const dataChanged = !st || st.data !== data;
        const styleChanged = !st || st.sig !== sig;
        if (dataChanged) map.getSource(srcId).setData(data);
        if (dataChanged || styleChanged) {
          applyStyle(srcId, l, expr); // 设色表达式依赖数据，数据变也重刷
          layerStateRef.current[srcId] = { data, sig };
        }
      }
      // 可见性开关（对全部子图层生效，含标注）；中文标注 Marker 单独同步
      const vis = l.visible === false ? "none" : "visible";
      for (const sub of subLayerIds(srcId)) {
        if (map.getLayer(sub)) map.setLayoutProperty(sub, "visibility", vis);
      }
      syncLabelMarkers(map, srcId, l, basemap.crs);
    }

    // 面板从收起态（容器尺寸 0）重新显示时重算画布尺寸；幂等，正常显隐无副作用
    if (layers.length > 0) map.resize();
  }, [layers, mapReady, inGCJ]);

  // PNG 导出：preserveDrawingBuffer 画布 → toDataURL → a[download] 落盘
  const exportPng = () => {
    const map = mapRef.current;
    if (!map) return;
    const a = document.createElement("a");
    a.href = map.getCanvas().toDataURL("image/png");
    a.download = `aigis-map-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.png`;
    a.click();
  };

  // —— 图层库抽屉 ——
  const refreshLib = async () => {
    setLib({ loading: true, error: "", rows: null });
    try {
      setLib({ loading: false, error: "", rows: await listLibrary() });
    } catch (e) {
      setLib({ loading: false, error: e.message, rows: null });
    }
  };

  const toggleLib = () => {
    if (libOpen) { setLibOpen(false); return; }
    setLibOpen(true);
    setBmOpen(false);
    if (!lib.rows) refreshLib();
  };

  const loadLibraryRow = async (row) => {
    setLibBusy(row.layer_name);
    try {
      await onLoadLibrary(row.layer_name, row.label);
      setLibOpen(false); // 加载成功收起抽屉，直接看图层
    } catch (e) {
      setLib({ loading: false, error: e.message, rows: lib.rows });
    } finally {
      setLibBusy("");
    }
  };

  const deleteLibraryRow = async (row) => {
    if (!window.confirm(`确定删除图层「${row.label || row.layer_name}」？删除后不可恢复。`)) return;
    setLibBusy(row.layer_name);
    try {
      await onDeleteLibrary(row.layer_name);
      await refreshLib();
    } catch (e) {
      setLib({ loading: false, error: e.message, rows: lib.rows });
    } finally {
      setLibBusy("");
    }
  };

  // Step 3: 工具条（图层库/导出PNG）+ 图层库抽屉 + 图层卡片列表（右上角浮层；上限 10 由父组件控制）
  return (
    <div className="map-panel" id="map-panel">
      <div className="map-canvas" ref={containerRef} />
      <div className="map-toolbar">
        <button type="button" className="tool-btn" onClick={() => { setBmOpen(!bmOpen); setLibOpen(false); }}
          title="切换底图：街区/卫星/3D 地形/3D 建筑等">
          🗺 底图
        </button>
        <button type="button" className="tool-btn" onClick={toggleLib} title="浏览/加载/删除持久图层">
          ♻ 图层库
        </button>
        <button type="button" className="tool-btn" onClick={exportPng} title="把当前地图导出为 PNG">
          📷 导出PNG
        </button>
      </div>
      {bmOpen && (
        <div className="basemap-menu">
          <div className="library-head">
            底图
            <button type="button" className="library-close" onClick={() => setBmOpen(false)}>×</button>
          </div>
          {BASEMAPS.map((b) => (
            <button type="button" key={b.id}
              className={`basemap-row${b.id === basemapId ? " active" : ""}`}
              onClick={() => { chooseBasemap(b.id); setBmOpen(false); }}>
              <span className="basemap-preview" style={{ background: b.preview }} />
              <span className="basemap-label">{b.label}</span>
              {b.crs === "gcj02" && <span className="basemap-tag" title="GCJ-02 底图：叠加数据已自动纠偏对齐">纠偏</span>}
              {b.id === basemapId && <span className="basemap-check">✓</span>}
            </button>
          ))}
          <div className="basemap-hint">3D 建筑需放大到 14 级以上；高德系底图为 GCJ-02，叠加数据已自动纠偏对齐</div>
        </div>
      )}
      {libOpen && (
        <div className="layer-library">
          <div className="library-head">
            图层库（服务端持久）
            <button type="button" className="library-close" onClick={() => setLibOpen(false)}>×</button>
          </div>
          {lib.loading ? (
            <div className="library-hint">加载中…</div>
          ) : lib.error ? (
            <div className="library-error">
              {lib.error}
              <button type="button" onClick={refreshLib}>重试</button>
            </div>
          ) : !lib.rows?.length ? (
            <div className="library-hint">暂无持久图层——制作指令或"保存为图层"后会出现在这里</div>
          ) : (
            lib.rows.map((row) => (
              <div key={row.layer_name} className="library-row">
                <div className="library-info">
                  <span className="library-name" title={row.layer_name}>
                    {row.label || row.layer_name}
                  </span>
                  <span className="library-meta">
                    {row.feature_count} 要素 · {String(row.created_at || "").slice(0, 16)}
                  </span>
                </div>
                <button type="button" className="library-act" disabled={libBusy === row.layer_name}
                  onClick={() => loadLibraryRow(row)}>
                  {libBusy === row.layer_name ? "…" : "加载"}
                </button>
                <button type="button" className="library-act library-del"
                  disabled={libBusy === row.layer_name} onClick={() => deleteLibraryRow(row)}>
                  删除
                </button>
              </div>
            ))
          )}
        </div>
      )}
      <div className="layer-cards">
        {layers.length === 0 ? (
          <div className="layer-empty">查询结果将显示在这里</div>
        ) : (
          layers.map((l) => (
            <div key={l.id} className="layer-item">
              <div className="layer-card"
                onClick={() => setExpandedId(expandedId === l.id ? null : l.id)}>
                <span className="layer-dot" style={{ background: l.style?.color || colorOf(l) }} />
                {l.persistent && (
                  <span className="layer-persist" title="持久图层（已存图层库）">♻</span>
                )}
                <span className="layer-name" title={l.name}>{l.name}</span>
                <span className="layer-count">
                  {l.geojson?.features?.length ?? 0} {l.persistent ? "要素" : "行"}
                </span>
                <span className="layer-expand">{expandedId === l.id ? "▴" : "▾"}</span>
                <label className="layer-eye" title={l.visible === false ? "显示图层" : "隐藏图层"}
                  onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={l.visible !== false}
                    onChange={() => onToggle && onToggle(l.id)}
                  />
                  <span className="layer-eye-track" />
                </label>
                <button
                  className="layer-del"
                  title="从地图移除（持久图层仍保留在图层库）"
                  onClick={(e) => { e.stopPropagation(); onRemove && onRemove(l.id); }}
                >
                  ×
                </button>
              </div>
              {expandedId === l.id && onStyle && (
                <StyleEditor layer={l} onStyle={onStyle} onPersist={onPersist} />
              )}
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
