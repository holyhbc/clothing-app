#!/usr/bin/env bash
# API 容器入口：等待数据库就绪 → 交还控制权给 CMD（docs/11 §3）
#
# 设计要点（刻意不做自动迁移）：
#   docs/11 §5 要求迁移是发布流程里的**显式一步**（`compose run --rm api alembic upgrade head`），
#   顺序是 备份 → 演练迁移 → 拉镜像 → 执行迁移 → 滚动重启。若在容器启动时自动迁移：
#     1) 无法在备份后单独演练；
#     2) 迁移失败会让服务直接起不来，而不是停在发布流程里可控地中止。
#   因此本脚本**只**等待 DB 就绪并 exec CMD；迁移由发布流程显式执行。
#   本地开发如需自动迁移，显式设 RUN_MIGRATIONS=1（默认关闭）。
set -euo pipefail

DB_WAIT_TIMEOUT="${DB_WAIT_TIMEOUT:-60}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-0}"

log() { printf '[entrypoint] %s\n' "$*"; }

if [[ -z "${DATABASE_URL:-}" ]]; then
    log "FATAL: DATABASE_URL 未设置（.env 缺失或键名错误）"
    exit 1
fi

# 用镜像内已有的 asyncpg 探活，不额外安装 postgresql-client
wait_for_db() {
    local waited=0
    while true; do
        if python -c "
import asyncio, os, sys
import asyncpg

async def main() -> None:
    url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(url, timeout=3)
    await conn.close()

try:
    asyncio.run(main())
except Exception as exc:  # noqa: BLE001
    print(exc, file=sys.stderr)
    sys.exit(1)
" >/dev/null 2>&1; then
            return 0
        fi
        if (( waited >= DB_WAIT_TIMEOUT )); then
            log "FATAL: 等待数据库超时（${DB_WAIT_TIMEOUT}s）"
            return 1
        fi
        (( waited == 0 )) || log "等待数据库… ${waited}s"
        sleep 2
        waited=$(( waited + 2 ))
    done
}

wait_for_db || exit 1
log "数据库已就绪"

# 仅本地开发用；生产发布走 docs/11 §5 的显式迁移步骤
if [[ "${RUN_MIGRATIONS}" == "1" ]]; then
    if [[ -z "${DATABASE_URL_MIGRATION:-}" ]]; then
        log "RUN_MIGRATIONS=1 但 DATABASE_URL_MIGRATION 未设置，拒绝用应用账号迁移"
        exit 1
    fi
    log "本地模式：执行 alembic upgrade head"
    DATABASE_URL="${DATABASE_URL_MIGRATION}" alembic upgrade head
fi

exec "$@"
