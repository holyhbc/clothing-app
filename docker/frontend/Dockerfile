# syntax=docker/dockerfile:1
# ---- 多阶段：node 只用于 build，最终镜像用 nginx（docs/11 §4）----

# ============ builder：安装依赖并构建静态资源 ============
FROM node:22-alpine AS builder

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=true

RUN corepack enable && corepack prepare pnpm@12.8.1 --activate

WORKDIR /build

# 先只拷依赖清单，改源码不会让安装层失效
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# ============ runtime：nginx 静态托管 + 反向代理 ============
FROM nginx:alpine AS runtime

RUN apk add --no-cache curl \
    && rm -rf /etc/nginx/conf.d/default.conf

COPY docker/nginx/nginx.conf /etc/nginx/conf.d/erp.conf
COPY --from=builder /build/packages/admin/dist /usr/share/nginx/html
COPY --from=builder /build/packages/mobile/dist /usr/share/nginx/mobile

# 构建产物由 root 拥有，运行期降权（docs/11 §4 非 root）
RUN chown -R nginx:nginx /usr/share/nginx \
    && touch /var/run/nginx.pid \
    && chown -R nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
