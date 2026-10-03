# syntax=docker/dockerfile:1
# ---- 多阶段：node 只用于 build，最终镜像用 nginx（docs/11 §4）----

# ============ builder：安装依赖并构建静态资源 ============
FROM node:22-alpine AS builder

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=true

RUN corepack enable && corepack prepare pnpm@12.8.1 --activate

WORKDIR /build

# 先只拷**清单**（各 package 的 package.json），改源码不会让安装层失效
# —— docs/11 §4「层缓存：依赖层先于源码层」。
#
# ⚠️ **pnpm workspace 的包清单必须一起拷**：锁文件里记着每个 importer
# （`importers["packages/admin"]` 等），少拷一个就直接
# `ERR_PNPM_OUTDATED_LOCKFILE: the lockfile records importers["packages/x"],
# but that project's directory or package.json is missing` —— `--frozen-lockfile`
# 必失败。**新增 package 时必须在这里加一行**，
# `backend/tests/test_frontend_dockerfile.py` 会在漏加时报红。
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/packages/shared/package.json ./packages/shared/
COPY frontend/packages/admin/package.json ./packages/admin/
COPY frontend/packages/mobile/package.json ./packages/mobile/
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
#
# ⚠️ `/var/cache/nginx` 与 `/var/log/nginx` **必须一起改属主**：nginx 以非 root 跑时
#    需要往 client_temp/proxy_temp 写临时文件，默认属主是 root，于是启动时
#    `mkdir() "/var/cache/nginx/client_temp" failed (13: Permission denied)`，
#    容器起来但一个请求都服务不了。静态资源不用改（只读即可），
#    但 pid 文件所在目录要能新建文件。
RUN chown -R nginx:nginx /usr/share/nginx /var/cache/nginx /var/log/nginx \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
