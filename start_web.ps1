# AI-GIS 一键启动：按需构建前端 → 起后端 → 打开浏览器
# 用法：pwsh ./start_web.ps1
#Requires -Version 7
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$dist = Join-Path $PSScriptRoot "web/dist"
if (-not (Test-Path -LiteralPath $dist)) {
    Write-Host "web/dist 不存在，先构建前端（npm run build）..."
    Push-Location (Join-Path $PSScriptRoot "web")
    try { npm run build }
    finally { Pop-Location }
}

Write-Host "启动后端 uvicorn（http://localhost:8000）..."
Start-Process -FilePath "uv" -ArgumentList @("run", "uvicorn", "aigis_web.app:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $PSScriptRoot

Start-Sleep -Seconds 2
Start-Process "http://localhost:8000"
Write-Host "完成：浏览器已打开 http://localhost:8000（关闭请结束 uvicorn 进程）"
