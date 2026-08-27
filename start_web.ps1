# AI-GIS 一键启动：按需构建前端 → 起后端 → 打开浏览器
# 用法：pwsh ./start_web.ps1
#Requires -Version 7
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

# 端口占用检查：8000 已被监听则视为服务已在运行，直接打开页面退出
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

Write-Host "启动后端 uvicorn（http://localhost:8000）..."
Start-Process -FilePath "uv" -ArgumentList @("run", "uvicorn", "aigis_web.app:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $PSScriptRoot

Start-Sleep -Seconds 2
Start-Process "http://localhost:8000"
Write-Host "完成：浏览器已打开 http://localhost:8000（关闭请结束 uvicorn 进程）"
