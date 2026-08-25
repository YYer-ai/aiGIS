# 发现与环境事实

## 环境（2026-08-25 核实）

- Windows 10 19045 / Git Bash；winget v1.28.220；无 scoop/choco
- Docker Desktop 29.7.2（用户级，2026-07 曾卸载过一次，现重装正常）
- 容器 aigis-postgis：PostgreSQL 16.9 + PostGIS 3.5.2，端口 5432，healthy
- 账号：aigis/aigis_dev_2026（管理）、aigis_readonly/aigis_readonly（引擎执行，已验证 SELECT 可/CREATE 拒）
- osm2pgsql 2.3.1：`tools/osm2pgsql/osm2pgsql-bin/osm2pgsql.exe`
- uv 0.12.5（Python 3.13.12 / 3.12.14 托管）；Node v24.14.1
- `~/.docker/daemon.json` 已配 daocloud/1panel/rat.dev 镜像源
- 无本地代理；Ollama 未装（Task 2 装）

## 项目结构现状

```
E:/aiGIS/
├── README.md                    # 项目总体架构与路线
├── docker-compose.yml           # PostGIS 服务（已验证）
├── .env                         # DB 配置 + LLM_PROVIDER（DEEPSEEK_API_KEY 待填）
├── db/init/01-readonly-user.sql # 只读账号（已生效）
├── pyproject.toml               # psycopg/dotenv 已装；Task 1 扩充
├── docs/superpowers/specs/      # 设计 spec（用户已批准）
├── docs/superpowers/plans/      # 实现计划（15 任务 TDD）
├── tools/osm2pgsql/             # 便携版
└── data/                        # （Task 2）北京 pbf
```

## 技术要点

- osm2pgsql flex API：`osm2pgsql.define_table` + `osm_type/osm_id`、`geom_column projection=4326`；2.x 用 `o:as_point()/as_linestring()/as_polygon()/as_multipolygon()`
- ST_Polygonize 需闭合线：环路用 ST_LineMerge(ST_Collect) 后 ST_IsClosed 检查，不闭合降级 ST_Buffer(300m)
- 绿化覆盖率定义（SQL 口径）：leisure IN (park,garden) OR landuse IN (grass,forest,meadow) OR natural=wood 与街区面交集面积占比
- OSM 北京 admin_level：4=北京市、6=区县、8/9=街道/乡镇（覆盖度待导入后验证）
- sqlglot 校验：白名单类型 Select/Union；Anonymous 函数取 str(f.this) 为名
- DeepSeek/OpenAI 兼容：base_url https://api.deepseek.com，model deepseek-chat，temperature=0
