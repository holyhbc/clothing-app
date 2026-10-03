#!/usr/bin/env bash
# 重置测试库：迁移回 base → 升到 head → 重新 seed。
#
# 为什么需要：手工跑 CLI（seed_baseline / restore_builtin）会**永久**改动测试库。
# 它们是运维命令，不在任何测试事务的回滚范围内。实测踩过：手工验证 TC-B21 时在
# 测试库写下 2 条 RESTORE 记录，之后
# test_restore_builtin_is_noop_when_nothing_missing 一直报 "assert 2 == 0"，
# 而代码完全没问题 —— 这种"测试库脏了"的失败最容易被误判成代码缺陷。
#
# 用法：scripts/reset-test-db.sh
set -euo pipefail

ENV_FILE="${1:-/tmp/test.env}"
cd "$(dirname "$0")/../backend"
export PATH="$HOME/.local/bin:$PATH"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "FATAL: 找不到环境变量文件 $ENV_FILE" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

echo "=== 回滚到 base ==="
uv run alembic downgrade base
echo "=== 升到 head ==="
uv run alembic upgrade head
echo "=== 重新 seed ==="
uv run python -m app.cli.seed_baseline
echo "=== 校验 ==="
uv run python -m app.cli.seed_baseline --check
echo "OK: 测试库已重置"
