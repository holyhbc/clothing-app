#!/bin/sh
# E2E 后端启动（Playwright 的 webServer 调它）。
#
# ## 为什么单独一个脚本而不是把命令写进 playwright.config.ts
#
# 1. **环境变量有 5 个**（两套连接串 + Redis + JWT + APP_ENV）。写在 config 里会
#    把「跑 E2E 需要哪些环境变量」这件事藏进一段 TS 字符串里，出错时看不到。
# 2. **CI 与本地要用同一份**。脚本读同一个 `.env.e2e`（不存在就用环境变量），
#    于是「本地能跑 CI 跑不了」这类差异只剩一个来源。
#
# ⚠️ **端口写在参数里而不是脚本里**：脚本被复用时端口要能被覆盖，
#    而写死的话并行跑两条 E2E 会抢同一个端口（症状是后一条的后端连到前一条的库）。
#
# ⚠️ `--workers 1`：E2E 是低并发（一条主流程），多 worker 只会多占内存。
#
# ⚠️ 数据库必须是**独立库**（`garment_erp_e2e`），见 playwright.config.ts 的说明。
set -eu

PORT="${1:-8099}"
BACKEND_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/../../backend" && pwd)"

# ⚠️ `-a`：`.env.e2e` 里的值**不覆盖**已存在的环境变量 —— CI 注入的变量优先，
#    否则本地为了调端口临时 export 的值会被文件里的旧值顶掉
[ -f "${BACKEND_DIR}/../.env.e2e" ] && . "${BACKEND_DIR}/../.env.e2e"

: "${E2E_DATABASE_URL:?缺少 E2E_DATABASE_URL（见 .env.example）}"
: "${REDIS_URL:=redis://127.0.0.1:6379/9}"
: "${JWT_SECRET:=e2e-only-not-a-secret-0123456789abcdef}"
: "${APP_ENV:=local}"

# ⚠️ 应用用 `erp_app`（无 DELETE 权限）；迁移用 `erp_ddl`。**不要反**：
#    反了的话 E2E 会因为「测试数据删不掉」而每轮累积
export DATABASE_URL="${E2E_DATABASE_URL}"
export DATABASE_URL_MIGRATION="${DATABASE_URL_MIGRATION:-$E2E_DATABASE_URL}"

cd "${BACKEND_DIR}"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" --workers 1 --log-level warning