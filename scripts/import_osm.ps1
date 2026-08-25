# scripts/import_osm.ps1：osm2pgsql flex 导入北京 OSM 数据 → PostGIS（aigis 库 4 张 osm_* 表）+ 中文 COMMENT + 只读授权
# 用法: pwsh scripts/import_osm.ps1  （读取 data/beijing-latest.osm.pbf，写入容器 aigis-postgis 的 aigis 库）
# 前置: 容器 aigis-postgis 已运行且 5432 端口已映射；data/beijing-latest.osm.pbf 已就绪。

$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent

# Windows 版 osm2pgsql 走 TCP 连 PostgreSQL 需要密码（本地开发环境默认密码，调用者可用环境变量覆盖）
if (-not $env:PGPASSWORD) { $env:PGPASSWORD = "aigis_dev_2026" }

# 1. osm2pgsql flex 导入（--drop 清理 slim 中间表，只留 4 张业务表）
& "$root/tools/osm2pgsql/osm2pgsql-bin/osm2pgsql.exe" `
  --create --slim --drop --output=flex --style "$PSScriptRoot/osm2pgsql-flex.lua" `
  --database aigis --host localhost --port 5432 --username aigis --prefix planet `
  "$root/data/beijing-latest.osm.pbf"
if ($LASTEXITCODE -ne 0) { throw "osm2pgsql 导入失败" }

# 2. 写入中文 COMMENT（表 + 全部列）并授权只读账号、刷新统计信息
Get-Content "$PSScriptRoot/add_comments.sql" -Raw | docker exec -i aigis-postgis psql -U aigis -d aigis -v ON_ERROR_STOP=1
if ($LASTEXITCODE -ne 0) { throw "COMMENT/GRANT/ANALYZE 执行失败" }

# 3. 验证：输出 4 张表行数
docker exec aigis-postgis psql -U aigis -d aigis -c `
  "SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE relname LIKE 'osm_%' ORDER BY relname;"
