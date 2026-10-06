# T-BUND-007：打菲 router 与权限/数据范围

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `todo` |
| 优先级 | P1 |
| 依赖 | T-BUND-003/004/005a/005b/006 |
| 被依赖 | T-BUND-008、T-BUND-009（契约生成） |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §6 接口清单、§10 权限矩阵；`docs/05`、`docs/07` |
| 关联 ADR | — |
| 估算 | 0.5d |

## 目标

按 `03 §6` 暴露打菲单与码的 HTTP 接口，挂权限点与数据范围，产出 OpenAPI（供前端生成类型）。

## 范围

**要做**：
- [ ] `backend/app/modules/bundling/router.py`（>400 行再按 ADR-0031 拆包）
  - 打菲单：列表 / 详情 / 新建 / PATCH / submissions / approvals / rejections /
    withdrawals / reversals / cancellations / logs / available-outputs / split / hands /
    labels / label-prints / statistics / exports
  - 码：`GET /bundles`、`GET /bundles/{bundle_no}`、`POST /bundles/{bundle_no}/voids`
- [ ] 每个端点：`x-permission` 声明 + 函数体显式 `_require`；`tags="打菲"` + `summary`
- [ ] `main.py` 挂载 router
- [ ] 数据范围：表头按 `workshop_id`，码按 `data_scope`（`core/scope.py`）
- [ ] `backend/tests/modules/test_bundling_router.py`：路径/权限/范围/错误码 + OpenAPI

**不做**：
- 不新增权限点（12 个 `bundling:*` 已在 `07 §2.2` + `app/common/permissions/perm_bundling.py`）
- 不新增错误码（全部引用 `05 §4`）
- `void_code`（`bundling:code:void`）端点可随本卡一并暴露；其 service 若未实现则**路由不注册**
  （`03` 的前端约定：未实现不注册，不返回 501）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/router.py` | 新增 | 端点 |
| `backend/app/main.py` | 修改 | 挂载 router |
| `backend/tests/modules/test_bundling_router.py` | 新增 | httpx 测试 |

## 实现要点（必读规范）

- [ ] `docs/03 §2.1` 第 8 条：前端隐藏不是安全，函数体必须 `_require`
- [ ] `docs/05 §3`：统一响应包装；数量金额一律字符串
- [ ] `docs/05 §5`：`Idempotency-Key` 用于 approvals / voids / label-prints
- [ ] 路由顺序陷阱（T-CUT-001c-1 教训）：`/exports`、`/statistics` 必须声明在 `/{order_id}` **之前**
- [ ] 权限矩阵照 `03 §10`；`split`/`hands` 用 `bundling:read`，`voids` 用 `bundling:code:void`

## 验收标准

- [ ] `uv run pytest tests/modules/test_bundling_router.py -q` 全部通过
- [ ] 未实现的 service 路径**路由不存在**（不返回 501）
- [ ] 无权限 `12001`、越权数据范围 `12002`
- [ ] OpenAPI 含 `tags="打菲"`，`approvals` 描述写明六步与「码数=手数」断言
- [ ] 单文件 ≤400 行；闸门 1-4 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-RT-01 | 列表/详情 | 200 + 范围过滤 | |
| TC-RT-02 | 无权限调任意端点 | `12001` | |
| TC-RT-03 | 越权车间 | `12002` | |
| TC-RT-04 | 路由顺序 | `/exports` 不被 `/{id}` 吞 | |
| TC-RT-05 | OpenAPI | tag/summary 齐 | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(bundling): router 与权限/数据范围

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| | | `docs/12` §遗留问题清单 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：按 03 §6 暴露接口，复用 12 权限点与数据范围 | AI |
