# syntax=docker/dockerfile:1
# ---- 多阶段：依赖层先于源码层，保证改代码不重装依赖（docs/11 §4 层缓存）----

# ============ builder：装依赖并预编译字节码 ============
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /usr/local/bin/

WORKDIR /app

# 只先拷依赖声明，改代码不会让这一层失效
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# ============ dev：开发与闸门 1-4 用（带 dev 依赖与源码）============
FROM builder AS dev
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app
RUN uv sync --frozen
COPY backend/ /app/
CMD ["bash"]

# ============ runtime：生产最终镜像（docs/11 §4）============
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    TZ=Asia/Shanghai

# curl 供 healthcheck 用；libpq5 供 asyncpg；其余不留编译工具链
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.4 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY backend/ /app/
COPY docker/scripts/entrypoint.sh /usr/local/bin/entrypoint.sh

# 非 root（docs/11 §4）
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && chmod +x /usr/local/bin/entrypoint.sh \
    && chown -R app:app /app
USER app

EXPOSE 8000

# 只等待 DB 就绪；迁移由发布流程显式执行（docs/11 §5），不在启动时自动跑
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# 存活与就绪分两个端点（docs/05 §1：/healthz 存活、/readyz 依赖就绪）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/readyz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
