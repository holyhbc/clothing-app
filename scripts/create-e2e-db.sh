#!/usr/bin/env bash
# 建 E2E 库：建库 → 授权 → 迁移 → 基线 seed。
#
# ## 为什么 E2E 需要自己的库（docs/10 §4「每个 E2E 用独立库」）
#
# E2E 会往库里写款号、工序、单价历史。如果跑在 `garment_erp_test` 上：
# - 那库是**单测**的库，单测断言 `total == 3` 之类的会因为 E2E 多出来的数据而随机失败；
# - 反过来 E2E 的「刷新后数据还在」也会被单测的清理逻辑干扰。
# 两个库共用一份数据时，失败现场是「谁写的这一行」根本查不出来。
#
# ## ⚠️ 授权必须与生产一致，否则 E2E 测不出权限问题
#
# `erp_app` 的权限刻意比 owner 少（**没有 DELETE**，ADR-0025/0029）。如果 E2E 图省事
# 全用 `erp_ddl` 跑，那么「应用账号删不掉单价历史」这条永远测不到 ——
# 等到生产上才发现就晚了。所以这里**照抄生产的授权口径**。
#
# 用法：
#   scripts/create-e2e-db.sh                 # 用环境变量里的连接串
#   scripts/create-e2e-db.sh /tmp/e2e.env    # 从环境变量文件读
set -euo pipefail

ENV_FILE="${1:-/tmp/e2e.env}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
E2E_DB="${E2E_DB_NAME:-garment_erp_e2e}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

: "${PGHOST:?缺少 PGHOST（见 .env.example）}"
: "${PGUSER:?缺少 PGUSER}"
: "${PGPASSWORD:?缺少 PGPASSWORD}"
: "${E2E_APP_ROLE:?缺少 E2E_APP_ROLE（默认 erp_app）}"
: "${E2E_DDL_ROLE:?缺少 E2E_DDL_ROLE（默认 erp_ddl）}"

export PGHOST PGPASSWORD
export PGUSER="${E2E_SUPERUSER:-$PGUSER}"

echo "=== 1/4 + 2/4 建库与授权（应用账号刻意不给 DELETE，对齐 ADR-0025/0029） ==="
# ⚠️ 用 Python 而不是 `psql`：宿主机不一定装了客户端工具，而 backend 的 venv 里
#    一定有 asyncpg。写死 `psql` 的结果是「在开发机上能跑、在 CI 上跑不了」。
cd "$ROOT/backend"
E2E_DB="$E2E_DB" E2E_APP_ROLE="$E2E_APP_ROLE" .venv/bin/python - <<'PYTHON'
import asyncio
import os

import asyncpg


async def main() -> None:
    db = os.environ["E2E_DB"]
    app_role = os.environ["E2E_APP_ROLE"]
    admin = await asyncpg.connect(
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        database="postgres",
    )
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db)
        if not exists:
            # ⚠️ CREATE DATABASE 不能在事务里跑，必须走 autocommit 连接
            await admin.execute(f'CREATE DATABASE "{db}"')
            print(f"   已创建库 {db}")
        else:
            print(f"   复用已有库 {db}")
    finally:
        await admin.close()

    conn = await asyncpg.connect(
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        database=db,
    )
    try:
        # ⚠️ 先 REVOKE 再 GRANT：库里可能残留上一轮给的宽权限，那样 E2E 是在
        #   「假权限」下跑绿的 —— 全绿但生产会 403
        tables = [
            row["tablename"]
            for row in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        ]
        await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{app_role}"')
        for table in tables:
            await conn.execute(f'REVOKE ALL ON TABLE "{table}" FROM "{app_role}"')
            await conn.execute(
                f'GRANT SELECT, INSERT, UPDATE ON TABLE "{table}" TO "{app_role}"'
            )
        # ADR-0029 白名单：只有这几张业务表允许应用账号 DELETE
        for table in (
            "colors",
            "sizes",
            "size_groups",
            "size_group_items",
            "product_categories",
            "user_roles",
            "role_permissions",
        ):
            await conn.execute(f'GRANT DELETE ON TABLE "{table}" TO "{app_role}"')
        print(f"   已授权 {len(tables)} 张表（DELETE 仅 7 张白名单）")
    finally:
        await conn.close()


asyncio.run(main())
PYTHON

echo "=== 3/4 迁移（用 owner 账号） ==="
DATABASE_URL_MIGRATION="postgresql+asyncpg://${E2E_DDL_ROLE}:${PGPASSWORD}@${PGHOST}:${PGPORT:-5432}/${E2E_DB}" \
  .venv/bin/python -m alembic upgrade head >/dev/null
echo "迁移完成"

echo "=== 4/4 基线 seed（权限点 / 角色 / 内置字典） ==="
DATABASE_URL_MIGRATION="postgresql+asyncpg://${E2E_DDL_ROLE}:${PGPASSWORD}@${PGHOST}:${PGPORT:-5432}/${E2E_DB}" \
  ERP_INITIAL_ADMIN_PASSWORD="${ERP_INITIAL_ADMIN_PASSWORD:-}" \
  .venv/bin/python -m app.cli.seed_baseline | tail -2

echo "OK: E2E 库 ${E2E_DB} 就绪（业务数据由 tests/e2e_seed.py 每次跑之前准备）"