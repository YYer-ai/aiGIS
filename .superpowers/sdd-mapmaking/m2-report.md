# M2 报告：数据库写通道（maker 角色 + user_layers schema + registry）

日期：2026-08-27 · 状态：完成 · 分支：main

## 交付物

1. `db/init/02-maker-channel.sql`（新库初始化自动执行；本次已对运行中的 aigis-postgis 手动应用，11 条语句全部成功）
2. `src/aigis/config.py`：`Config` 新增 `maker_user`/`maker_password` 字段（默认 `aigis_maker`/`aigis_maker`），`load_config` 支持 `MAKER_USER`/`MAKER_PASSWORD` 环境变量覆盖
3. `tests/test_config.py`：默认值断言 + `test_maker_env_override`（env 覆盖与回落）
4. 测试：`uv run pytest` **120 passed**（119 基线 + 1 新增）

## 权限矩阵实测输出（运行库 aigis-postgis，PG16.9/PostGIS3.5.2）

| # | 操作 | 账号 | 实测结果 | 预期 |
|---|------|------|----------|------|
| 1 | `CREATE TABLE user_layers._probe(i int)` + INSERT | aigis_maker | `CREATE TABLE` / `INSERT 0 1` | 许 |
| 2 | `INSERT INTO public.osm_pois(osm_id) VALUES (999999)` | aigis_maker | `ERROR: permission denied for table osm_pois` | **拒** |
| 3 | `SELECT count(*) FROM public.osm_pois` | aigis_maker | `28074`（读成功） | 许（CTAS 数据源，只授读） |
| 4 | `INSERT INTO user_layers.registry(...)` | aigis_maker | `INSERT 0 1` | 许 |
| 5 | `DELETE FROM user_layers.registry WHERE ...` | aigis_maker | `ERROR: permission denied for table registry` | **拒** |
| 6 | `TRUNCATE user_layers.registry` | aigis_maker | `ERROR: permission denied for table registry` | **拒** |
| 7 | `DROP TABLE user_layers.registry` | aigis_maker | `ERROR: must be owner of table registry` | **拒** |
| 8 | `SELECT ... FROM user_layers._probe`（maker 新建的表） | aigis_readonly | `readonly_probe_select_ok | 1 | 42`（默认权限生效） | 许 |
| 9 | `DROP TABLE user_layers._probe`（自建表） | aigis_maker | `DROP TABLE` | 许 |

测试残留已清理（_probe 表已 DROP、registry 测试行已删，当前 registry 0 行）。

## 对任务模板的两处必要修正（已验证必要）

1. **`ALTER DEFAULT PRIVILEGES` 加 `FOR ROLE aigis_maker`**：任务模板不带 FOR ROLE 时该语句挂在管理账号名下，只影响管理账号未来建的表；而 maker 建的图层表 owner 是 aigis_maker，readonly 将永远读不到（第 8 项会失败）。aigis 是 superuser，可执行 FOR ROLE 子句，实测通过。
2. **补 `GRANT USAGE ON SCHEMA user_layers TO aigis_readonly`**：schema USAGE 是跨 schema 访问表的前置（01 只授过 public），缺它时默认权限的表级 SELECT 仍报 `permission denied for schema user_layers`（实测复现后补授，复测通过）。

## 设计补充（spec §2 要求但模板未覆盖）

- **maker 对 public 只授 `USAGE` + `SELECT`（含 default privileges）**：制作 CTAS 的内层 SELECT 需读 osm_*；与 spec "public 业务表对 maker 无任何写权限"一致——第 2 项实测写被拒、第 3 项实测只读可达。
- 显式 `GRANT CONNECT ON DATABASE aigis TO aigis_maker`（当前 PUBLIC 亦有 CONNECT，显式化与 01 风格一致、防未来 REVOKE）。

## 凭据与环境

- maker 默认 `aigis_maker`/`aigis_maker`（开发环境弱口令，与 readonly 同策略）；生产部署前应替换并由 `MAKER_USER`/`MAKER_PASSWORD` 注入。
- registry 删图层（DELETE/DROP）仅管理账号可执行，对应 spec §3 的 `DELETE /api/layers/{name}` 管理端点（M5 落地）。
