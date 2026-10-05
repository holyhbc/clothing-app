# T-CUT-001c-1：裁剪单接口层（router + 权限 + OpenAPI）

| 项 | 内容 |
| --- | --- |
| 模块 | cut |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-CUT-001b-2（`create`/`get`/`list`/`patch`/`delete`/三条 PUT/`suggest-lines`/`entry-mode` 已就位） |
| 被依赖 | T-CUT-001c-2（前端 API 层与类型生成）、T-CUT-001c-4（E2E） |
| 关联设计 | [modules/02-裁剪.md](../modules/02-裁剪.md) §6 接口清单 |
| 估算 | 0.5d |

## 目标

裁剪单的接口能被 HTTP 调用，且**权限、数据范围、OpenAPI 标签、响应包装**全部到位 ——
这是前端与 E2E 的前置条件（前端类型从 `openapi.json` 生成）。

## 范围

**要做**：

- [ ] `app/modules/cutting/router.py`：§6 里**草稿态与只读**的那些端点
- [ ] 每个端点 `x-permission` + 函数体显式 `_require`（`03 §2.1` 第 8 条）
- [ ] 挂到 `app/main.py::_register_module_routers`
- [ ] `httpx` 测试：正常路径 / 403 无权限 / 403 越权（数据范围）/ 422 参数 / 响应包装

**不做**（留给后续卡）：

- **状态机端点**（`submissions` / `approvals` / `rejections` / `withdrawals` /
  `reversals` / `cancellations` / `logs`）→ **T-CUT-001b-3**，service 里还没有
- **导入 / 导出 / 统计**（`/imports/*` `/exports` `/statistics`）→ T-CUT-001c-3
- **比例主数据接口**（`/style-color-size-ratios`）→ 属基础资料模块，T-BASE-002 的后续卡
- **任何前端代码** → T-CUT-001c-2

## 三条最容易做错的

| # | 陷阱 | 正确做法 |
| --- | --- | --- |
| 1 | **Router 里查库** | `03 §1.1` 第 6 条禁止。列表也必须经 service（数据范围过滤不能被绕过） |
| 2 | **前端隐藏当安全** | 每个端点都要 `_require`，`openapi_extra` 只是文档 |
| 3 | **`workshop_id` 传了能放大范围** | 不能。传了只是**再过滤一次**（`07 §3.2` 铁律 1），车间主管传别的车间 id 依然查不到 |

## 验收标准

- [ ] `pytest tests/modules/test_cutting_router.py -q` 全过
- [ ] `alembic check` 零漂移
- [ ] 闸门 1-5 全绿

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-C01c-01 | 建单 201 + 响应包装 `code=0` | `05 §3` | |
| TC-C01c-02 | 详情返回三层结构 | §6 GET 详情行 | |
| TC-C01c-03 | 列表分页 `data.items/total/page/page_size` | `05 §3` | |
| TC-C01c-04 | 无 `cutting:create` → **403** | `03 §2.1` 第 8 条 | |
| TC-C01c-05 | 越权按 ID 取单 → **403 `12002`** | `07 §3.2` 铁律 2 | |
| TC-C01c-06 | `version` 缺失 → 422 `10001` | `05 §4` | |
| TC-C01c-07 | 三条 PUT 都要求 `version` | §6 | |
| TC-C01c-08 | 路径参数非法 UUID → 422 | `05 §2` | |
| TC-C01c-09 | 每个端点都带 `tags=裁剪` 与 `summary` | `05 §6` | |
| TC-C01c-10 | 每个端点都带 `x-permission`，且值在 `07 §2.2` 登记过 | `docs/12 §9` | |
| TC-C01c-11 | 未实现的端点**不占位**（返回 404 而不是 500） | 见下 | |

> ⚠️ **TC-C01c-11 是个刻意的决定**：本卡**不**为状态机端点加「501 未实现」占位。
> 占位路由的危害是：前端会以为那个接口存在、OpenAPI 里有它、E2E 里会看到它返回
> 501 而不是 404 —— 而「这个功能还没做」的正确表达是**路由不存在**。

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/cutting/router.py` | +330 | 10 个端点 + `_require` + `x-permission` |
| `backend/app/modules/cutting/schemas.py` | +40 | `Str` 出参类型（pydantic v2 的 `str` 不接受 `Decimal`） |
| `backend/app/modules/cutting/service.py` | +30 | 重算后 flush + `create` 返回重载后的三层 |
| `backend/app/modules/cutting/repository.py` | +14 | `populate_existing` |
| `backend/app/main.py` | +2 | 挂 `cutting_router` |
| `backend/tests/modules/test_cutting_router.py` | +330 | 29 例 |
| `backend/tests/factories/cutting.py` | +25 | `make_style` / `make_stock` 幂等 |

**手写行数 ≈ 770**，一个提交即可（未超 `AGENTS §7.1` 的 800 行）。

## 抓到的四个问题

| # | 问题 | 为什么难发现 |
| --- | --- | --- |
| 1 | pydantic v2 的 `str` **不接受 `Decimal`** | 报错 `Input should be a valid string [input_value=Decimal('96.000')]`，完全看不出根因是「出参类型不能从 ORM 构造」 |
| 2 | Core `UPDATE` 会 **expire** identity map 里的行 | 只在 **HTTP 层**暴露（service 单测直接读内存对象）；报错是 `MissingGreenlet` |
| 3 | SQLAlchemy 默认**不覆盖**已加载对象的属性值 | 所以「重载」拿到的一直是旧值 —— 与 2 是两个独立的坑，缺一个都会错 |
| 4 | 重算后必须 **flush**，否则强制刷新读到旧值 | **service 单测里全对、HTTP 响应里全错**，两边数字互相矛盾 |

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| — | `/exports`、`/statistics` 会被 `/{order_id}` 匹配 → 422。加它们时**必须声明在 `/{order_id}` 之前** | T-CUT-001c-3（已在测试注释里留档） |
| — | 状态机端点、导入导出统计 | T-CUT-001b-3 / T-CUT-001c-3 |
| — | 前端页面与 E2E | T-CUT-001c-2 / 001c-4 |