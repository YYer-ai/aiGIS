# AI-GIS 一键关闭：停后端（PID 文件优先定位，8000 端口兜底）；-All 连 PostGIS 容器一起停
# 用法：pwsh ./stop_web.ps1 [-All]
#Requires -Version 7
param([switch]$All)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$pidFile = Join-Path $PSScriptRoot ".web-server.pid"
$stopped = $false

# ── 1. PID 文件优先：进程存在则连同子进程一并结束（uv.exe → python/uvicorn，只杀父进程会留孤儿占 8000）──
if (Test-Path -LiteralPath $pidFile) {
    $procId = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($procId -match '^\d+$' -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
        taskkill /PID $procId /T /F *> $null
        Write-Host "后端已停止 (PID $procId)"
        $stopped = $true
    }
    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
}

# ── 2. 兜底：pid 文件缺失/进程已死时，按 8000 监听端口定位结束 ──
if (-not $stopped) {
    $conns = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)
    if ($conns.Count -gt 0) {
        $owners = @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($owner in $owners) { taskkill /PID $owner /T /F *> $null }
        Write-Host "后端已停止 (按端口 8000 定位 PID $($owners -join ', '))"
        $stopped = $true
    }
}
if (-not $stopped) { Write-Host "后端未在运行" }

# ── 3. -All：连数据库容器一起停（默认不停，下次启动秒就绪）──
if ($All) {
    docker compose stop
    if ($LASTEXITCODE -eq 0) {
        Write-Host "数据库容器已停止（docker compose stop）"
    } else {
        Write-Host "docker compose stop 失败（exit $LASTEXITCODE，Docker daemon 是否在运行？）"
    }
}
