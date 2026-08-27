-- AI 地图制作写通道（spec §2 安全模型）：
-- maker 仅可写 user_layers schema；public 业务表只授读（CTAS 数据源），零写权限。
CREATE ROLE aigis_maker WITH LOGIN PASSWORD 'aigis_maker';
GRANT CONNECT ON DATABASE aigis TO aigis_maker;

CREATE SCHEMA IF NOT EXISTS user_layers;
GRANT USAGE, CREATE ON SCHEMA user_layers TO aigis_maker;

-- registry 表由管理账号建，maker 仅 INSERT/SELECT（删图层走管理账号端点）
CREATE TABLE IF NOT EXISTS user_layers.registry(
  layer_name text PRIMARY KEY,
  label text NOT NULL,
  sql text NOT NULL,
  feature_count int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON SCHEMA user_layers IS 'AI 地图制作通道：仅此 schema 可写，业务数据（public.osm_* 等）对 maker 不可写';
GRANT INSERT, SELECT ON user_layers.registry TO aigis_maker;

-- maker 对自己新建的表：schema 内 CREATE 即 owner（全部 DML）。
-- 默认权限须 FOR ROLE aigis_maker（否则只对管理账号未来建的表生效），
-- 使 aigis_readonly 能 SELECT maker 建的图层表（渲染走只读账号）；
-- schema USAGE 是表级 SELECT 的前置（01 只授过 public）。
GRANT USAGE ON SCHEMA user_layers TO aigis_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE aigis_maker IN SCHEMA user_layers
  GRANT SELECT ON TABLES TO aigis_readonly;

-- 制作 CTAS 的数据源：public 只读（spec：业务表对 maker 无任何写权限）
GRANT USAGE ON SCHEMA public TO aigis_maker;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO aigis_maker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO aigis_maker;
