-- LLM 生成 SQL 的执行账号：只读，天然沙箱（README 安全设计）
CREATE ROLE aigis_readonly WITH LOGIN PASSWORD 'aigis_readonly';
GRANT CONNECT ON DATABASE aigis TO aigis_readonly;
GRANT USAGE ON SCHEMA public TO aigis_readonly;

-- 新导入表的默认授权
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO aigis_readonly;
-- 已存在表的授权（数据导入后可重复执行）
GRANT SELECT ON ALL TABLES IN SCHEMA public TO aigis_readonly;
