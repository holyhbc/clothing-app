#!/usr/bin/env bash
# 数据库角色初始化（docs/04-数据库规范.md §6.2.1）
#
# 由 postgres 官方镜像的 /docker-entrypoint-initdb.d 在**首次初始化**时执行一次，
# 以容器引导超级用户（POSTGRES_USER，默认 postgres）的身份运行。
#
# 三个角色，职责不可混：
#   postgres（引导超管）  只用于本脚本，不进任何应用配置
#   erp_ddl（迁移账号）    建表改表、跑 alembic；不进应用常驻进程
#   erp_app（运行账号）    应用连接池；只有 SELECT/INSERT/UPDATE，没有 DELETE
#
# 为什么必须用独立的引导超管：若把 POSTGRES_USER 设成 erp_app，它就是超级用户，
# "应用账号不能硬删"这条保证在测试环境直接失效，权限测试全部失真
# （T-INFRA-004 TC-I17 的由来）。
#
# 铁律：
#   - 幂等：可重复执行（\gexec + WHERE NOT EXISTS）
#   - 密码只从环境变量取，脚本内不硬编码任何密钥（docs/11 §2）
#   - 生产库不给应用账号任何硬删能力
set -euo pipefail

: "${ERP_APP_PASSWORD:?ERP_APP_PASSWORD 未设置}"
: "${ERP_DDL_PASSWORD:?ERP_DDL_PASSWORD 未设置}"
ERP_APP_USER="${ERP_APP_USER:-erp_app}"
ERP_DDL_USER="${ERP_DDL_USER:-erp_ddl}"
ERP_DB="${ERP_DB:-garment_erp}"

echo "[init-roles] 初始化角色：迁移=${ERP_DDL_USER} 运行=${ERP_APP_USER} 库=${ERP_DB}"

# 密码通过 psql 变量传入并用 %L 转义，不拼进 SQL 文本，避免落进日志
psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
    --set=ON_ERROR_STOP=1 \
    --set=ddl_user="${ERP_DDL_USER}" \
    --set=ddl_password="${ERP_DDL_PASSWORD}" \
    --set=app_user="${ERP_APP_USER}" \
    --set=app_password="${ERP_APP_PASSWORD}" \
    --set=app_db="${ERP_DB}" <<'SQL'
-- 迁移账号：不存在才建
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'ddl_user', :'ddl_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ddl_user')
\gexec

-- 运行账号：不存在才建
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

GRANT CONNECT, TEMPORARY ON DATABASE :"app_db" TO :"app_user";

-- 迁移账号成为**库 owner**（不是超管）。两个原因缺一不可：
--   1) PostgreSQL 15 起 public schema 默认不对所有角色开放 CREATE，不显式授权
--      的话 alembic 建表会报 permission denied for schema public
--   2) pg_trgm 是 trusted 扩展（PG 13+），只有**库 owner 或超管**能装；
--      而 docs/04 §5.1 明确要求「迁移第一条建 pg_trgm」，所以迁移账号必须有这个权限
--      ——给 owner 而不是超管，避免迁移账号拥有系统级权限。
--   PG 15+ 的 public schema 归 pg_database_owner 所有，库 owner 自动获得其权限。
ALTER DATABASE :"app_db" OWNER TO :"ddl_user";

GRANT USAGE, CREATE ON SCHEMA public TO :"ddl_user";

-- 运行账号只要读元数据，不需要 CREATE
GRANT USAGE ON SCHEMA public TO :"app_user";

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";

-- 未来新建的表也要自动授权。必须写 FOR ROLE 迁移账号：alembic 以 erp_ddl 建表，
-- 而 ALTER DEFAULT PRIVILEGES 默认只作用于「当前角色」建的对象。
ALTER DEFAULT PRIVILEGES FOR ROLE :"ddl_user" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"ddl_user" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";

-- 运行账号不得具备任何硬删能力（docs/04 §6.2.1）
REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM :"app_user";

-- 就绪标记。注意：psql 变量只在 SQL 正文里插值，**不会**在 DO $$ ... $$ 这类
-- 美元引号内部替换（踩过这个坑），所以这里用 \echo 而不是 RAISE NOTICE。
\echo 'roles ready:' :ddl_user '(migration),' :app_user '(runtime)'
SQL

echo "[init-roles] 完成"
