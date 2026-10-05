# T-CUT-001c-4：裁剪单 E2E（三层建单 → 详情 → 编辑 → 删除）

| 项 | 内容 |
| --- | --- |
| 模块 | test |
| 状态 | `done` |
| 优先级 | P1 |
| 依赖 | T-CUT-001c-3a/3b/3c（三个页面）、T-BASE-007a/007b（候选端点）、T-BASE-006（`material_stocks`） |
| 被依赖 | T-CUT-001b-3 状态机端点落地后补一条「提交 → 审核」E2E |
| 关联设计 | [docs/10-测试规范.md](../10-测试规范.md) §4、[modules/02-裁剪.md](../modules/02-裁剪.md) §5 |
| 估算 | 0.5d |

## 目标

用**真浏览器 + 真后端 + 真库**跑通裁剪单主流程，并由此发现组件测试与 service 单测
都看不见的缺陷。

## 为什么这一卡值得单独立

E2E 验的是**跨层一致性**，组件测试验不了：

| 缺陷 | 为什么前面的测试看不见 |
| --- | --- |
| ★ **三条 PUT 的成功路径返回 500**（`MissingGreenlet`） | service 单测里属性访问在 **async** 函数中，greenlet 在，Core UPDATE `expire` 掉的列**惰性刷新能成功**；router 层当时唯一的 PUT 用例是 422 校验，**根本走不到 service** |
| ★ **表头汇总只算在内存里没落库** | service 单测断言**返回对象**（内存，正确）；集成测试断言三层**行数**；HTTP 响应又是重读来的 —— 只有直接查库看得见 |
| ★ **`PUT /lines` 省略 `size_line_no` → 500**（`NotNullViolation`） | `PUT /size-lines` 那条路径一直有分配，而「同一个字段在两个端点上语义不同」，契约只写了一次 |
| ★ **响应里混进软删的旧行** | 汇总列由 `recalc_*` 只按存活行算，**数字是对的**；前端把响应灌进编辑器 → 「保存一次，凭空多一行」 |
| **`fetch-options` 传了未绑定的方法** | `vi.spyOn(...).mockResolvedValue()` 返回的桩不需要 `this` —— 195 个前端用例全绿而浏览器里下拉是坏的 |
| **`**加粗**` 在用户可见文案里原样显示** | antd 的 `Modal.confirm` 与模板都不解析 markdown |

## 范围

**要做**：

- [x] `backend/tests/e2e_seed.py`：加**车间 / 物料 / 供应商 / 布批**（新建页的级联选料要它们）
- [x] seed 的三处健壮性：每次**重置 E2E 账号口令**、**清登录锁**、**先清裁剪单再清布批**
- [x] `frontend/e2e/p1-cutting-flow.spec.ts`：建单 → 详情 → 刷新 → 编辑再存 → 删除草稿
- [x] `backend` 四处修复 + 三条回归测试（HTTP 级 PUT 成功路径 / 汇总落库 ×2）

**不做**：状态机（提交 / 审核 / 驳回 / 撤回 / 反审核 / 作废）—— 后端端点还没有
（T-CUT-001b-3）；导出同理（`/exports` 未实现）。

## 环境（这台机器上）

```bash
export E2E_CHROMIUM_PATH="$HOME/.cache/ms-playwright/chromium-1148/chrome-linux/chrome"
# .env.e2e：DATABASE_URL / DATABASE_URL_MIGRATION 指向 127.0.0.1:15432/garment_erp_e2e
#           E2E_ADMIN_PASSWORD=E2e-Admin-2026、REDIS_URL 用 db 1
```

⚠️ `garment_erp_e2e` 曾停在迁移 0006，**必须**先升级到 head 并**重跑
`scripts/create-e2e-db.sh` 补 GRANT** —— 否则 `erp_app` 对后加的表没有权限，
接口会报 `permission denied for table material_stocks`，而报错完全看不出是「库没升」。

## 测试清单

| # | 用例 | 验的是什么 | 结果 |
| --- | --- | --- | --- |
| E2E-01 | 新建三层裁剪单 → 详情逐字对上 → **刷新后仍在** → 编辑改手数再存（版本号变） → 删除草稿 | 跨层一致性 + 刷新持久 + 版本链 + 删除 | ✅ |
| E2E-00 | （既有 4 条 P0 用例） | 款号 / 工序 / 单价 | ✅ |

## 抓到的六个坑（全部已修 + 留注释）

| # | 坑 | 为什么难发现 |
| --- | --- | --- |
| 1 | 三条 PUT 成功路径 500 | 见上表；修法是写完**重读**（`populate_existing`），与 `patch` 一致 |
| 2 | 表头汇总没落库 | 修法：`_recalc_all` 末尾 **flush**；顺带发现 `_recalc_all` 里那句 `order.version += 1` 与 `_bump_header` **重复 bump**，去掉后版本才正常 |
| 3 | `PUT /lines` 省略 `size_line_no` → 500 | 修法：按「已建最大值 + 1」分配；⚠️ **不能**调 `_next_size_line_no(color, ...)` —— 新 color 的 `size_lines` 集合没加载，惰性加载就是 `MissingGreenlet` |
| 4 | 响应混进软删旧行 | 修法：`with_loader_criteria` 三层各过滤一次（`selectinload(...).where` 在 2.1 **不存在**，`Load` 没有 `where`） |
| 5 | `fetch-options` 未绑定方法 | 修法：三处包箭头函数 |
| 6 | seed 三个坑 | ① 账号被上一次失败锁 15 分钟（`10004`）；② 口令与上一轮不同 → `11003`；③ **上一轮建的裁剪单引用着布批**，直接删布批撞外键 → seed 之后再也跑不起来 |

## E2E 本身的两个测试写法坑

| # | 坑 | 正确写法 |
| --- | --- | --- |
| 1 | antd 给**两个汉字**的按钮插空格 | `'登录'` / `'编辑'` / `'保存'` / `'删除'` 一律匹配不上，要用 `/登\s*录/` 这类正则 |
| 2 | 断言 `已保存 CT-…` 这类 message | message 3 秒消失而 Playwright 重试 10 秒 → **偶发失败**。断言**持久状态**（版本号 Tag）而不是提示 |

## 实际改动

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `backend/app/modules/cutting/service.py` | +55/-6 | 三处 PUT 写完重读；`_recalc_all` flush；去掉重复 bump |
| `backend/app/modules/cutting/repository.py` | +28 | 三层 `with_loader_criteria` 过滤软删 |
| `backend/app/modules/cutting/router.py` | +2 | PUT 明细的 `version` 说明补一句 |
| `backend/tests/modules/test_cutting_router.py` | +78 | TC-C01c-13：三条 PUT 成功路径经 HTTP |
| `backend/tests/modules/test_cutting_service2.py` | +62 | TC-C01b2-14/15：汇总真的落库（create + put_lines 各一条） |
| `backend/tests/e2e_seed.py` | +160 | 车间 / 物料 / 布批 + 三个健壮性修复 |
| `frontend/e2e/p1-cutting-flow.spec.ts` | +175 | E2E-01 |
| `frontend/packages/admin/src/views/cutting/**` | +40 | 三处未绑定 `fetch-options` + `**` 文案 + 删除改 `Modal.confirm` |
| `frontend/e2e/p0-main-flow.spec.ts` | +12 | 菜单用例不再假设「分组是折叠的」 |

**手写行数 ≈ 612**（ADR-0030 软上限 1200 内；无单文件超 400 行）。