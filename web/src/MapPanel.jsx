import React, { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";

// 底图瓦片：默认高德（OSM 国内不可达）；海外环境可把下方 tiles 换成 [OSM_TILE_URL]
const AMAP_TILE_URL =
  "https://webrd04.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}";
const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

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
   * 坐标 Point 直取、线/面取首坐标对。sig（列|可见|数据引用）不变则跳过重建 */
  const syncLabelMarkers = (map, srcId, l) => {
    const col = l.style?.label?.column;
    const want = (col && !columnIsAscii(l.geojson, col))
      ? { column: col, visible: l.visible !== false, data: l.geojson } : null;
    const prev = markerSigRef.current[srcId];
    if (prev?.column === want?.column && prev?.visible === want?.visible
        && prev?.data === want?.data) return;
    clearLabelMarkers(srcId); // 标注关闭/切列/数据变/隐藏：先清再按需重建
    markerSigRef.current[srcId] = want;
    if (!want?.visible) return;
    const markers = [];
    for (const f of (l.geojson?.features || []).slice(0, LABEL_MARKER_LIMIT)) {
      const pos = firstCoord(f?.geometry);
      const v = f?.properties?.[col];
      if (!pos || v == null || v === "") continue;
      const el = document.createElement("div");
      el.className = "map-label";
      el.textContent = String(v);
      markers.push(new maplibregl.Marker({ element: el, anchor: "top" })
        .setLngLat(pos).addTo(map));
    }
    labelMarkersRef.current[srcId] = markers;
  };

  // Step 1: 初始化地图（useRef 防严格模式重复初始化；cleanup 移除实例）；
  // preserveDrawingBuffer 供 PNG 导出读取画布，glyphs 供标注 symbol 层渲染文字
  useEffect(() => {
    if (mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        glyphs: GLYPHS_URL,
        sources: { basemap: { type: "raster", tiles: [AMAP_TILE_URL], tileSize: 256 } },
        layers: [{ id: "basemap", type: "raster", source: "basemap" }],
      },
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

  // Step 2: 图层同步——新增加 source/子图层+fitBounds；数据替换 setData（如 make 层
  // 采样→全量补全）；样式变化 setPaintProperty（classify/gradient/颜色/透明度/半径/线宽）
  // + 标注增删（ASCII 列走 symbol 子层，含中文走 DOM Marker）；visible 切 setLayoutProperty；删除移除
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
      delete colorsRef.current[srcId.slice(4)];
      delete layerStateRef.current[srcId];
    }

    for (const l of layers) {
      const srcId = `lyr-${l.id}`;
      const expr = colorExpression(l.style, l.geojson, colorOf(l));
      const sig = JSON.stringify(l.style ?? null);
      const st = layerStateRef.current[srcId];
      if (!map.getSource(srcId)) {
        map.addSource(srcId, { type: "geojson", data: l.geojson });
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
        layerStateRef.current[srcId] = { data: l.geojson, sig };
        // 新增图层飞行到其范围
        const bbox = bboxOf(l.geojson);
        if (bbox) map.fitBounds(bbox, { padding: 48, maxZoom: 15, duration: 600 });
      } else if (st && st.data !== l.geojson) {
        map.getSource(srcId).setData(l.geojson); // 数据替换（引用不同即变）
        applyStyle(srcId, l, expr);              // 设色表达式依赖数据，一并重刷
        layerStateRef.current[srcId] = { data: l.geojson, sig };
      } else if (!st || st.sig !== sig) {
        // 样式变化：paint 逐属性更新（无结构变化，事件绑定保持有效）；标注层单独增删/改
        applyStyle(srcId, l, expr);
        layerStateRef.current[srcId] = { data: l.geojson, sig };
      }
      // 可见性开关（对全部子图层生效，含标注）；中文标注 Marker 单独同步
      const vis = l.visible === false ? "none" : "visible";
      for (const sub of subLayerIds(srcId)) {
        if (map.getLayer(sub)) map.setLayoutProperty(sub, "visibility", vis);
      }
      syncLabelMarkers(map, srcId, l);
    }
  }, [layers, mapReady]);

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
        <button type="button" className="tool-btn" onClick={toggleLib} title="浏览/加载/删除持久图层">
          ♻ 图层库
        </button>
        <button type="button" className="tool-btn" onClick={exportPng} title="把当前地图导出为 PNG">
          📷 导出PNG
        </button>
      </div>
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
