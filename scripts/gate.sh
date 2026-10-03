#!/usr/bin/env bash
# 依次跑 docs/02 §6 定义的 5 道闸门。
#
# 为什么要有这个脚本：docs/02 §6 要求 5 道闸门全过，但手工敲 6 条命令时很容易
# 漏掉开头的 build —— 一旦漏了，闸门跑的是**上一次的镜像**，失败信息长得像代码
# bug（真实踩过：刚加的迁移 0003 被报成 "Can't locate revision identified by
# '0003'"）。这个脚本把"构建一次 + 顺序执行 + 失败即停"固定下来。
#
# 用法：
#   scripts/gate.sh              # 全部 5 道
#   scripts/gate.sh --host       # 只在宿主机跑（不用 docker）
#   scripts/gate.sh --no-build   # 跳过 build（确认镜像是最新的时）
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-docker}"
BUILD=1
if [[ "${2:-}" == "--no-build" ]]; then
  BUILD=0
fi

if [[ "$MODE" == "--host" ]]; then
  export PATH="$HOME/.local/bin:$PATH"
  cd backend

  echo "=== 闸门 1：lint ==="
  uv run ruff check .
  uv run ruff format --check .

  echo "=== 闸门 2：typecheck ==="
  uv run mypy app

  echo "=== 闸门 3：单测 + 覆盖率 ==="
  # 需要测试库；未连库时用例会自动 skip，务必先起 compose 的 postgres/redis
  uv run pytest -q --cov=app --cov-fail-under=80

  echo "=== 闸门 4：迁移往返 + 漂移 ==="
  uv run alembic upgrade head
  uv run alembic downgrade -1
  uv run alembic upgrade head
  uv run alembic current
  # 漂移守卫：模型与数据库不一致时，--autogenerate 会生成危险操作
  # （历史上真的生成过 drop_table('document_logs')），必须为 No new operations
  uv run alembic check

  echo "=== 宿主机 4 道闸门通过（闸门 5 只在容器内验证）==="
  exit 0
fi

COMPOSE=(docker compose -f docker-compose.ci.yml)

if [[ "$BUILD" == "1" ]]; then
  # 闸门 1-4 共用同一个镜像 tag，一次构建即可（见 docker-compose.ci.yml 注释）
  echo "=== 构建闸门镜像（一次性）==="
  "${COMPOSE[@]}" build lint
fi

echo "=== 闸门 1 + 2：lint + typecheck ==="
"${COMPOSE[@]}" run --rm lint

echo "=== 闸门 3：单测 + 覆盖率 ==="
"${COMPOSE[@]}" run --rm test

echo "=== 闸门 4：迁移往返 ==="
"${COMPOSE[@]}" run --rm migrate-check

echo "=== 闸门 5：生产镜像可构建且可导入 ==="
"${COMPOSE[@]}" build api-image
"${COMPOSE[@]}" run --rm api-image

echo "=== 5 道闸门全部通过 ==="