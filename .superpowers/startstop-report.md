# 一键启停实现报告（start_web / stop_web）

日期：2026-09-02　分支：main

## 改动内容

### 1. `start_web.ps1` 增强（PowerShell 7）
- **依赖自检与自愈**（顺序：端口检查 → .env → Docker daemon → PostGIS 容器 → 前端构建 → 后端 → 浏览器）：
  - Docker daemon：`docker info` 检查；未运行则 `Start-Process "C:/Program Files/Docker/Docker/Docker Desktop.exe"` 拉起，5s 间隔轮询，上限 120s，超时退出。
  - PostGIS 容器：`docker inspect` 查 `aigis-postgis` 状态，非 running（含不存在）则 `docker compose up -d` 拉起；再轮询 `{{.State.Health.Status}}` 等 healthy，上限 60s。
  - `.env`：缺失时若有 `.env.example` 模板提示复制；无模板提示按 README 配置后退出（本项目当前无模板，走后者）。
  - 容器自动拉起使 `stop_web.ps1 -All` → 下次 `start_web.ps1` 形成闭环。
- **PID 记录**：`Start-Process -PassThru` 起 uvicorn，PID 写入项目根 `.web-server.pid`（已加 `.gitignore`）。
- **保留**：端口 8000 已占用 → 提示已在运行 + 只开浏览器退出；`web/dist` 缺失时自动 `npm run build`。
- **体验**：开浏览器前轮询 8000 就绪（上限 15s，替代原固定 sleep 2，避免冷启动时浏览器先于后端打开）；各步骤中文状态输出。

### 2. 新增 `stop_web.ps1`
- 读 `.web-server.pid` → 进程存在则结束并输出"后端已停止 (PID x)"；随后删除 pid 文件。
- pid 文件缺失/进程已死 → 兜底 `Get-NetTCPConnection -LocalPort 8000 -State Listen` 取 `OwningProcess` 结束；找不到输出"后端未在运行"。
- `-All`：额外 `docker compose stop` 停 PostGIS 容器（默认不停，下次启动秒就绪）。
- **实现偏差（有意）**：结束进程用 `taskkill /PID x /T /F` 而非 `Stop-Process -Force`。实测 `Start-Process uv run uvicorn` 返回的 PID 是 uv.exe 父进程，真正监听 8000 的是其 python.exe 子进程；`Stop-Process` 只杀父进程会留 python 孤儿继续占 8000（已复现），`/T` 杀整棵进程树才能释放端口。

### 3. 文档
- `README.md`「Web 操作台」节：新增一键启动/关闭两条，替换原"生产模式手动 uvicorn"描述；开发模式说明保留。
- `.gitignore`：新增 `.web-server.pid`。

## 验证记录（实际执行环境：验证开始时 Docker daemon 未运行、8000 无监听）

| # | 步骤 | 结果 |
|---|---|---|
| 1 | `pwsh -NoProfile -File stop_web.ps1`（无服务在跑） | 输出"后端未在运行"，exit 0 |
| 2 | `pwsh -NoProfile -File start_web.ps1`（全新冷启动） | 自动拉起 Docker Desktop（约 20s）→"Docker daemon 已就绪"→"PostGIS 容器已 healthy"→ 后端 PID 20600 写入 pid 文件 → 浏览器打开；`curl http://localhost:8000/` = HTTP 200 |
| 3 | `pwsh -NoProfile -File stop_web.ps1`（pid 精确停） | "后端已停止 (PID 20600)"；netstat 8000 无 LISTEN（释放）；pid 文件已删除；进程树无残留 |
| 4 | 兜底分支：删除 pid 文件后 stop | "后端已停止 (按端口 8000 定位 PID 16300)"，8000 释放 |
| 5 | 再次 `start_web.ps1` 收尾（依赖已就绪） | 秒级启动，PID 19864，HTTP 200，服务留在运行状态 |
| 6 | `uv run pytest` | **192 passed**（25.26s，基线不变，服务运行中无冲突） |

## 备注
- `.env.example` 模板当前不存在，`.env` 检查的无模板分支已按设计实现（提示配置后退出），该分支未触发实机验证（`.env` 存在）。
- stop `-All` 分支未做实机验证（避免停掉用户数据库影响后续任务）；逻辑为单条 `docker compose stop`，与 start 的 `compose up -d` 对偶。
