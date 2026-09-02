# AI-GIS 一键启动：依赖自检与自愈（.env / Docker / PostGIS）→ 按需构建前端 → 起后端 → 打开浏览器
# 用法：pwsh ./start_web.ps1    （一键关闭：pwsh ./stop_web.ps1）
#Requires -Version 7
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

# ── 0. 端口占用检查：8000 已被监听则视为服务已在运行，直接打开页面退出 ──
$inUse = $false
try {
    $tcp = [System.Net.Sockets.TcpClient]::new("127.0.0.1", 8000)
    $tcp.Dispose()
    $inUse = $true
} catch { }
if ($inUse) {
    Write-Host "端口 8000 已被占用（服务可能已在运行），直接打开页面"
    Start-Process "http://localhost:8000"
    exit 0
}

# ── 1. .env 检查 ──
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot ".env"))) {
    $tpl = Join-Path $PSScriptRoot ".env.example"
    if (Test-Path -LiteralPath $tpl) {
        Write-Host "缺少 .env：请先复制模板并填写配置（Copy-Item .env.example .env）"
    } else {
        Write-Host "缺少 .env 且无 .env.example 模板：请参考 README「快速开始」配置环境变量后重试"
    }
    exit 1
}

# ── 2. Docker daemon 自检与自愈 ──
function Test-DockerDaemon {
    docker info *> $null
    return ($LASTEXITCODE -eq 0)
}
if (-not (Test-DockerDaemon)) {
    Write-Host "Docker daemon 未运行，启动 Docker Desktop..."
    Start-Process "C:/Program Files/Docker/Docker/Docker Desktop.exe"
    $deadline = (Get-Date).AddSeconds(120)
    while (-not (Test-DockerDaemon)) {
        if ((Get-Date) -gt $deadline) {
            Write-Host "等待 Docker daemon 超时（120s），请手动启动 Docker Desktop 后重试"
            exit 1
        }
        Start-Sleep -Seconds 5
        Write-Host "仍在等待 Docker daemon 就绪..."
    }
}
Write-Host "Docker daemon 已就绪"

# ── 3. PostGIS 容器：未运行则 docker compose 拉起，再等 healthy ──
$containerState = docker inspect --format "{{.State.Status}}" aigis-postgis 2> $null
if ($LASTEXITCODE -ne 0 -or $containerState -ne "running") {
    Write-Host "PostGIS 容器未运行，执行 docker compose up -d..."
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Host "docker compose up -d 失败（exit $LASTEXITCODE），已中止"
        exit 1
    }
}
$deadline = (Get-Date).AddSeconds(60)
while ($true) {
    $health = docker inspect --format "{{.State.Health.Status}}" aigis-postgis 2> $null
    if ($health -eq "healthy") { break }
    if ((Get-Date) -gt $deadline) {
        Write-Host "等待 PostGIS 容器 healthy 超时（60s，当前状态 $health），请检查 docker logs aigis-postgis"
        exit 1
    }
    Start-Sleep -Seconds 5
}
Write-Host "PostGIS 容器已 healthy"

# ── 4. 前端构建（按需）──
$dist = Join-Path $PSScriptRoot "web/dist"
if (-not (Test-Path -LiteralPath $dist)) {
    Write-Host "web/dist 不存在，先构建前端（npm run build）..."
    Push-Location (Join-Path $PSScriptRoot "web")
    try { npm run build }
    finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "前端构建失败（npm exit $LASTEXITCODE），已中止，不启动服务"
        exit 1
    }
}

# ── 5. 启动后端并记录 PID ──
$pidFile = Join-Path $PSScriptRoot ".web-server.pid"
Write-Host "启动后端 uvicorn（http://localhost:8000）..."
$proc = Start-Process -FilePath "uv" -ArgumentList @("run", "uvicorn", "aigis_web.app:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $PSScriptRoot -PassThru
$proc.Id | Set-Content -LiteralPath $pidFile
Write-Host "后端已启动 (PID $($proc.Id)，已记录到 .web-server.pid)"

# ── 6. 等端口就绪再开浏览器 ──
$deadline = (Get-Date).AddSeconds(15)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $tcp = [System.Net.Sockets.TcpClient]::new("127.0.0.1", 8000)
        $tcp.Dispose()
        $ready = $true
        break
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { Write-Host "提示：8000 尚未监听，后端可能仍在启动或启动失败（日志见上方）" }
Start-Process "http://localhost:8000"
Write-Host "完成：浏览器已打开 http://localhost:8000（一键关闭：pwsh ./stop_web.ps1）"
