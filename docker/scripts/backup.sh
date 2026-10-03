#!/usr/bin/env bash
# 数据库全量备份 + 校验 + 保留期清理（docs/11 §7）
#
# 铁律：
#   - 备份后必须用 `pg_restore -l` 校验，校验失败立即非零退出（备份失败当天必须处理）
#   - 只删除**本脚本自己生成的**过期文件；不使用通配符误删
#   - 禁止在本脚本里出现 dropdb / TRUNCATE / rm -rf 卷（docs/11 §10 红线 1）
set -euo pipefail

RETENTION_DAYS="${RETENTION_DAYS:-30}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
PREFIX="garment_erp"

# 运行位置兼容两种：
#   1) 在 postgres 容器内执行（docs/11 §7 的 docker compose exec 方式）→ PGHOST=localhost 走 socket
#   2) 在独立客户端容器/主机执行 → 传 PGHOST=postgres
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"

: "${POSTGRES_USER:?POSTGRES_USER 未设置}"
: "${POSTGRES_DB:?POSTGRES_DB 未设置}"

# 密码只从环境来（PGPASSWORD / PGPASSFILE），脚本自身**不持有任何密钥**（docs/11 §2）。
# 连非本地实例时若没有凭据，立即失败而不是交互式索要（cron/容器里会直接挂住）。
if [[ "${PGHOST}" != "localhost" && "${PGHOST}" != "127.0.0.1" && -z "${PGPASSWORD:-}" && -z "${PGPASSFILE:-}" ]]; then
    echo "FATAL: PGHOST=${PGHOST} 但未提供 PGPASSWORD 或 PGPASSFILE（密钥只走环境变量，docs/11 §2）"
    exit 1
fi

TS="$(date +%Y%m%d_%H%M%S)"
FILE="${BACKUP_DIR}/${PREFIX}_${TS}.dump"

mkdir -p "${BACKUP_DIR}"

# -Fc 自定义格式，支持 pg_restore 增量恢复；单事务保证一致性
if ! pg_dump -w -h "${PGHOST}" -p "${PGPORT}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc --no-owner --no-acl > "${FILE}.part"; then
    log_error="pg_dump 失败"
    echo "BACKUP FAILED: ${log_error}"
    rm -f "${FILE}.part"
    exit 1
fi

if [[ ! -s "${FILE}.part" ]]; then
    echo "BACKUP EMPTY: ${FILE}.part"
    rm -f "${FILE}.part"
    exit 1
fi

mv "${FILE}.part" "${FILE}"

# 校验：能列出归档目录才说明备份可读（docs/11 §7）
if ! pg_restore -l "${FILE}" > /dev/null; then
    echo "BACKUP CORRUPT: ${FILE}"
    exit 1
fi

SIZE="$(du -h "${FILE}" | cut -f1)"
echo "OK ${FILE} ${SIZE}"

# 保留期清理：只匹配本前缀的 .dump，且必须早于保留期
find "${BACKUP_DIR}" -maxdepth 1 -type f -name "${PREFIX}_*.dump" -mtime "+${RETENTION_DAYS}" -print -delete

# WAL 归档检查：有主从或 PHO 目标时必须开启（docs/11 §7，阶段二开启）
ARCHIVE_MODE="$(psql -w -h "${PGHOST}" -p "${PGPORT}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -tAc "SHOW archive_mode;" 2>/dev/null || echo "off")"
if [[ "${ARCHIVE_MODE}" != "on" ]]; then
    echo "WARN: archive_mode=${ARCHIVE_MODE}，WAL 归档未开启，RPO 目标（1 小时）未达成（docs/11 §7 阶段二）"
fi
