# fix: 地图显示跟随图层状态

- App.jsx：新增 `showMap = layers.length > 0`，容器类名 `console has-map` 由 showMap 驱动（视觉开关）；MapPanel 挂载仍用 mapOpened 闩锁（首图层建 map 实例后保留，防重建闪烁）
- styles.css：`.map-panel` 改 transition 驱动（flex-basis/opacity .4s；出现时 visibility 0s 立即生效）；新增 `.console:not(.has-map) .map-panel` 收起态（flex-basis 0%、opacity 0、visibility 延迟 .4s 隐藏、pointer-events none）；保留首挂载滑入 keyframes
- MapPanel.jsx：图层同步 effect 末尾 `layers.length > 0` 时调 `map.resize()`（幂等），修复收起期间容器尺寸 0 导致重新显示画布不重算
- 验证：`npm run build` 通过；`uv run pytest` 230 passed（后端未动）
