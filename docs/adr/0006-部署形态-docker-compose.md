# ADR-0006：部署形态：单机 Docker Compose，不上 K8s

| 项 | 内容 |
| --- | --- |
| 状态 | `Accepted` |
| 日期 | 2026-10-01 |
| 决策人 | 规范维护者 |
| 影响范围 | 部署 / 迁移 |
| 关联 | [01-技术选型与架构.md](../01-技术选型与架构.md) §3、[11-部署运维与发布规范.md](../11-部署运维与发布规范.md) |

---

## 背景

只有 1 台 VPS，2 核 12G。要在上面跑 nginx、api、worker、postgres、redis 五个服务，外加每日全量备份 + WAL 归档，保留 30 天。

- 现状：`01` §3.1 已给出内存预算表（合计 ≈6G），但部署形态（Compose vs K8s）未落 ADR；CI 尚未建。
- 痛点/风险：
  1. 2 核 12G 上再放 K8s 控制面（kubelet + apiserver + etcd + controller-manager + scheduler + CNI）本身要吃 1-1.5G 内存，实际业务可用内存从 12G 掉到 10.5G，收益为负。
  2. 单厂多车间（当前规划个位数车间），没有水平扩展需求；K8s 的自愈/滚动发布优势在单副本场景不成立。
  3. `pnpm build` 瞬时吃 2-3G 内存，在生产机构建会 OOM 并拖垮线上服务。
  4. `docker compose down -v` 一条命令就能删掉数据卷，误操作代价是全量数据丢失。
  5. 工资、成本、凭证数据必须不可篡改，光靠应用层 `deleted_at` 不够，要在 DB 权限层堵死。
  6. 无专职运维，备份必须验证过，不能只写"已备份"。
- 硬约束：单厂规模（用户数十人、日活操作工约 50-200）、无高可用要求（工作时间 99.5% 即可）。

---

## 决策

**我们决定：单机 Docker Compose 部署，镜像在 CI 构建并推私有 registry，生产只 pull 不 build；生产数据库账号无 `DELETE`/`TRUNCATE` 权限，`document_logs` 表额外 `REVOKE UPDATE, DELETE`。**

```yaml
# docker-compose.yml（生产，节选）
services:
  api:
    image: registry.internal/erp-api:${TAG}     # 只 pull，不 build
    deploy: { resources: { limits: { memory: 1.5G, cpus: '0.8' } } }
  worker:
    image: registry.internal/erp-api:${TAG}
    deploy: { resources: { limits: { memory: 800M, cpus: '0.4' } } }
  postgres:
    image: postgres:16
    command:
      - postgres -c shared_buffers=1GB -c work_mem=16MB
               -c max_connections=60 -c shared_preload_libraries=pg_stat_statements
    deploy: { resources: { limits: { memory: 3G, cpus: '0.6' } } }
  redis:
    image: redis:7
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    deploy: { resources: { limits: { memory: 384M, cpus: '0.2' } } }
  nginx:
    image: nginx:alpine
    deploy: { resources: { limits: { memory: 256M, cpus: '0.3' } } }
```

```bash
# 生产库权限：应用账号不给 DELETE/TRUNCATE
CREATE ROLE app_rw LOGIN;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_rw;
-- 硬删禁令表（04 §6.1）只读
GRANT SELECT ON users, roles, permissions, accounts, alembic_version TO app_rw;

-- 审计日志只增不改不删
REVOKE UPDATE, DELETE ON document_logs FROM app_rw;
```

```bash
# 每日备份 + 保留 30 天（禁止 docker compose down -v）
docker compose exec -T postgres pg_dump -Fc erp > /backup/erp-$(date +%F).dump
find /backup -name 'erp-*.dump' -mtime +30 -delete
# 恢复演练（每月）：建临时库恢复并跑 v_voucher_balance 对账视图
```

---

## 理由

| # | 理由 |
| --- | --- |
| 1 | K8s 控制面占 1-1.5G，2C12G 上等于把 12% 内存换成用不上的编排能力，收益为负。 |
| 2 | 单厂多车间规模无需水平扩展；`api` 2 worker 已能覆盖 50 QPS 计件 + 日常单据操作。 |
| 3 | 镜像在 CI 构建后 push，生产 `docker compose pull` 只需几十秒，回滚 = 换 tag + `up -d`，比 K8s 回滚还简单。 |
| 4 | 资源上限用 compose 的 `deploy.resources.limits` 声明，超限即 OOM-kill 单一服务而不是拖垮整机。 |
| 5 | 应用账号无 `DELETE`/`TRUNCATE`，把 INV-7 软删与"禁止物理删除"从规范变成数据库强制。 |
| 6 | `document_logs` 的 `REVOKE UPDATE, DELETE` 让"单据变更全量留痕、日志不可删"不可绕过。 |
| 7 | `pg_dump -Fc` + 30 天保留 + 每月恢复演练，RPO 24h（配合 WAL 归档可到分钟级）、RTO < 2h，单机可接受。 |
| 8 | 禁止在生产机 build 显式写进规范，避免 `pnpm build` 瞬时 2-3G 把线上拖 OOM。 |

---

## 备选方案与否决原因

| 方案 | 是否考虑 | 否决原因 |
| --- | --- | --- |
| Kubernetes（自建集群） | ✅ | 控制面吃 1-1.5G 内存、2C 的控制面调度抖动明显；单副本无自愈收益；需专人维护 etcd/证书/网络策略，无人可管。 |
| K3s（轻量版） | ✅ | 比 K8s 轻，但 SQLite etcd + 内嵌组件仍吃约 1G，且同样需要额外运维知识；收益仍不足以覆盖成本。 |
| 裸机部署（无容器） | ✅ | Python 依赖、PostgreSQL 版本、uvicorn 进程守护全靠人肉维护；升级要停机，无镜像可回滚。 |
| 传统虚拟机 + Systemd | ✅ | 同上，且单机没有故障域分离，多服务互相影响（同机 OOM 全挂）。 |
| docker compose + 保留 2 个应用副本做蓝绿 | ✅ | **采纳折中**：需要回滚能力时用两个 compose project 交替，资源够（2×1.5G 可承受），比 K8s 轻。 |

---

## 影响

| 项 | 说明 |
| --- | --- |
| 正面 | 部署文件一套搞定；回滚 = 换 tag；资源上限明确；数据层强制不可删。 |
| 负面/代价 | 单机无高可用，主机故障需人工恢复（依赖备份演练速度）；没有自动扩缩容；Compose 无内置滚动发布（需脚本）。 |
| 风险 | ① 误执行 `docker compose down -v` 删卷 → 规范明令禁止，脚本里加保护；② 磁盘被备份/WAL 撑满 → 监控 `df` 使用率 < 60% 告警；③ 生产 `app_rw` 权限收得过紧导致某个迁移失败 → 迁移用 `app_ddl` 账号，应用只读 app_rw。 |
| 迁移路径 | 阶段一：`compose up -d` + 迁移 + 初始化数据。阶段二：接 CI（构建/推送/拉取/健康检查/冒烟）三段流水线。无需停机窗口。 |
| 回滚方式 | 镜像 tag 回到上一稳定版（保留最近 10 个 tag），`compose up -d` 重建；数据库用 `alembic downgrade`（**必须保证每个迁移 down 可执行**，见 `04` §6.2）。 |
| 对规范的影响 | `01` §3/§4、`11` 全文（部署、备份、发布、监控）、`04` §6（硬删禁令）。新增权限点：无。 |
| 对测试的影响 | 必测：① 部署冒烟（健康检查 + 登录 + 建单）；② 备份文件可恢复且 `v_voucher_balance` 0 行；③ `app_rw` 执行 DELETE 报权限错误；④ `document_logs` UPDATE 被拒。 |

---

## 验证方式

| 指标 | 目标值 | 观测方式 |
| --- | --- | --- |
| 内存峰值 | < 7G（预算 6G + 系统余量） | `docker stats` 日报 + `free -h` 峰值 |
| 磁盘使用率 | < 60% | `df -h /` 监控，> 50% 告警 |
| 每日备份成功率 | 100%，`pg_dump` 退出码 0 | 备份任务日志 + 文件非空校验 |
| 恢复演练 | 每月 ≥ 1 次成功 | 演练记录（时间、耗时、验证项） |
| 回滚耗时 | < 10 分钟 | 发布记录实测 |
| 应用账号越权删除 | 100% 被拒 | 权限探针用例 |

---

## 关联

- 规范：[01-技术选型与架构.md](../01-技术选型与架构.md) §3/§4、[11-部署运维与发布规范.md](../11-部署运维与发布规范.md)、[04-数据库规范.md](../04-数据库规范.md) §6
- 模块：全模块（部署面）
- 关联 ADR：ADR-0001（技术栈）、ADR-0002（数据库）
