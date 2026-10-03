#!/usr/bin/env bash
# 依次跑 docs/02 §6 定义的 5 道闸门。
#
# 为什么要有这个脚本：docs/02 §6 要求 5 道闸门全过，但手工敲 6 条命令时很容易
# 漏掉开头的 build —— 一旦漏了，闸门跑的是**上一次的镜像**，失败信息长得像代码
# bug（真实踩过：刚加的迁移 0003 被报成 "Can't locate revision identified by
# '0003'"）。这个脚本把"构建一次 + 顺序执行 + 失败即停"固定下来。
#
# 用法：
#   scripts/gate.sh                    # 全部 5 道（容器内）
#   scripts/gate.sh --host             # 只在宿主机跑（不用 docker）
#   scripts/gate.sh --env-file FILE    # 宿主机模式加载环境变量（默认 /tmp/test.env 若存在）
#   scripts/gate.sh docker --no-build  # 跳过 build（确认镜像是最新的时）
set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-docker}"
BUILD=1
if [[ "${2:-}" == "--no-build" ]]; then
  BUILD=0
fi

if [[ "$MODE" == "--host" ]]; then
  export PATH="$HOME/.local/bin:$PATH"

  # 加载测试环境变量。⚠️ 不加载的后果很隐蔽：db_session 夹具检测不到 DATABASE_URL
  # 就把 113 个数据库用例全部 skip，单测"通过"但覆盖率掉到 66%，闸门 3 报错的原因
  # 与真实缺陷毫无关系，排查成本极高。
  ENV_FILE="/tmp/test.env"
  if [[ "${2:-}" == "--env-file" ]]; then
    ENV_FILE="$3"
  elif [[ -f "$ENV_FILE" ]]; then
    :
  else
    echo "警告：未找到 $ENV_FILE，数据库用例会被 skip。" >&2
    echo "  生成方式见下方提示，或用 --env-file 指定。" >&2
  fi
  if [[ -f "$ENV_FILE" ]]; then
    echo "加载环境变量：$ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi

  cd backend

  echo "=== 闸门 1：lint ==="
  uv run ruff check .
  uv run ruff format --check .

  echo "=== 闸门 2：typecheck ==="
  uv run mypy app

  echo "=== 闸门 3：单测 + 覆盖率 ==="
  # 需要测试库；未连库时用例会静默 skip，先把它变成硬失败。
  # ⚠️ 不要写成 `pytest --co | grep -q`：grep -q 命中即退出，pytest 收到 SIGPIPE
  # 返回非零，pipefail 会把整条管道判为失败 —— 于是"收集正常"也被报成"没收集到"。
  COLLECTED="$(uv run pytest -q --co 2>/dev/null || true)"
  if ! grep -q "test_login_success" <<<"$COLLECTED"; then
    echo "错误：测试没有被收集到，检查 pytest 配置" >&2
    exit 1
  fi
  SKIPPED="$(uv run pytest -q -rs 2>&1 | grep -cE '^SKIPPED' || true)"
  if [[ "$SKIPPED" -gt 5 ]]; then
    echo "错误：有 $SKIPPED 个用例被跳过（阈值 5）。" >&2
    echo "  最常见原因：DATABASE_URL / DATABASE_URL_MIGRATION 未设置，或数据库没起。" >&2
    echo "  起库：docker compose -f docker-compose.ci.yml up -d postgres redis" >&2
    echo "  环境变量：见 $ENV_FILE" >&2
    uv run pytest -q -rs 2>&1 | grep -E '^SKIPPED' | head -5 >&2
    exit 1
  fi
  uv run pytest -q --cov=app --cov-fail-under=80

  echo "=== 闸门 4：迁移往返 + 漂移 ==="
  uv run alembic upgrade head
  uv run alembic downgrade -1
  uv run alembic upgrade head
  uv run alembic current
  # 漂移守卫：模型与数据库不一致时，--autogenerate 会生成危险操作
  # （历史上真的生成过 drop_table('document_logs')），必须为 No new operations
  uv run alembic check
  # ⚠️ 闸门 4 的 downgrade → upgrade 会把 0004 的表**重建**，字典内置库随之消失
  #    —— 字典数据来自 seed 命令而不在迁移里（部署顺序是「迁移 → seed」）。
  #    不重跑 seed 的话，本地库就停在"有表没数据"的状态，后续手工验证会误判成
  #    "seed 坏了"。实测踩过：闸门跑完后 colors 表 0 行。
  echo "=== 重建内置库（闸门 4 清掉了表数据）==="
  uv run python -m app.cli.seed_baseline
  uv run python -m app.cli.seed_baseline --check

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