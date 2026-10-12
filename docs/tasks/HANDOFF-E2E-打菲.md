# 交接：打菲 E2E 未跑通，请接着往下做

> **✅ 已闭环（2026-10-12，变更记录 0138）** —— `p1-bundling-flow.spec.ts` 连续 3 轮全绿，
> 全量 E2E（p0 + p1-bundling + p1-cutting）6/6。
> 本文件**只作为排查记录留存**，下方「已排除的 / 待排查方向 / 环境准备」均已过时，
> 真因见 [T-BUND-010](./T-BUND-010-标签打印前端与E2E.md) 的实际改动表与 `docs/12` 0138。
>
> **一句话答案**：POST **发出去了**，只是返回 422 —— `style_no` 与 `operation_no`
> 传成了 **UUID**（36 > `max_length=32`），而页面只弹一句「参数校验失败」。
> 之所以「日志里没有 POST」，是因为 `reuseExistingServer` 复用了别人起的旧实例。
> 详细排查手法见文末「事后补记」。

## 一句话现状（当时）

打菲全链路（后端 + 前端 + 单测）已全绿，唯一未完成的是
`frontend/e2e/p1-bundling-flow.spec.ts` —— 它写到「表单全部字段填对」为止，
**点「保存草稿」后没有任何 POST 发出**，原因未查清。

## 当前 git 状态

- 最新提交：`c76f665`（已推送 origin/main）
- 工作树**干净**，但有一个**故意不提交**的新文件：
  `frontend/e2e/p1-bundling-flow.spec.ts`（217 行，未跟踪）
  —— 提交它等于「声称完成但未验证」，所以留着。

## 任务：只做这一件事

### 现象

E2E 跑到这一步：

```ts
await expect(page.locator('.block-line')).toHaveCount(0)   // ✅ 无阻断项
await page.getByRole('button', { name: /保存草稿/ }).first().click()
await expect(page.getByText(/BD-\d{8}-\d{6}/).first()).toBeVisible({ timeout: 15_000 })
// ❌ Error: expect(locator).toBeVisible() failed —— 停在新建页
```

截图确认界面**完全正确**：

- 车间 `E2E-CUT E2E 裁剪车间`、款号 `E2E-2026-0001 E2E 全棉圆领 T 恤`
- 色码 `WHT`、色组 `WHT-GRP`、工序 `01 裁`
- 来源裁剪单 `CT-E2E-APPROVED-0001 · 2026-10-01`
- 明细 1 行：尺码 `XL`、来源明细 `XL · 手数:2 · 出数:120 · 每手:60`
- 「保存草稿」按钮**已启用**（蓝色，非 disabled）

### 已排除的

- ❌ 表单校验（`.block-line` 数为 0）
- ❌ 按钮 disabled
- ❌ 后端 500（旧实例日志里**没有** POST 记录，所以请求压根没发出）

### 待排查方向（**未验证，只是线索**）

1. `Form.vue` 的「保存草稿」按钮有 `v-can="PERM.BUNDLING_CREATE"` ——
   E2EADMIN 账号是否有该权限？若被 `v-can` 摘掉，点击不会发请求
   （但截图里按钮可见，所以更可能是别的原因）
2. `submitting` 状态：`PageLayout` 的 `#extra` 插槽里按钮 `:loading="submitting"`，
   若 `submitting` 意外为 true，点击可能被 loading 态吞掉
3. 点击是否真的命中了按钮 —— `getByRole('button', {name: /保存草稿/})` 可能匹配到
   别的同名元素（页面上「保存草稿」应只有一个）

### 建议的排查手段

```bash
cd /root/opencode/frontend
set -a && . /tmp/e2e.env && set +a
timeout 900 pnpm exec playwright test e2e/p1-bundling-flow.spec.ts --reporter=line
```

抓请求（最直接的办法）：

```ts
page.on('request', (r) => {
  if (r.url().includes('/bundling-orders')) console.log('REQ', r.method(), r.url())
})
page.on('response', async (r) => {
  if (r.url().includes('/bundling-orders')) console.log('RES', r.status(), await r.text())
})
```

后端日志：`playwright.config.ts` 的 `webServer` 用 `start-backend.sh 8099`；
注意 `reuseExistingServer: !process.env.CI` —— **本地若已有实例在跑会被复用，
日志不在你手里**。排查前先 `pkill -f "uvicorn app.main"`，或直接手动起并 tee 日志：

```bash
cd /root/opencode/backend
set -a && . /tmp/e2e.env && set +a
export DATABASE_URL_MIGRATION="postgresql+asyncpg://erp_ddl:ci-only-not-a-secret@127.0.0.1:15432/garment_erp_e2e"
export REDIS_URL="redis://127.0.0.1:16379/0"
bash ../frontend/e2e/start-backend.sh 8099 2>&1 | tee /tmp/be.log
```

## 环境准备（每次重跑 E2E 前）

```bash
# 1. 重建 E2E 库（每轮全清重建）
cd /root/opencode
set -a && . /tmp/e2e.env && set +a
export PATH="$HOME/.local/bin:$PATH" PGHOST=127.0.0.1 PGPORT=15432 PGUSER=postgres \
       E2E_DDL_ROLE=erp_ddl E2E_APP_ROLE=erp_app
PGPASSWORD=ci-only-not-a-secret bash scripts/create-e2e-db.sh /tmp/e2e.env

# 2. seed
cd backend && uv run python -m tests.e2e_seed
```

⚠️ `/tmp/e2e.env` 里 `APP_ENV` 必须是 `test`（`AppEnv` 只允许
`local/test/production`，`e2e` 是非法值，会让 Settings 校验失败）。
已改成 `test`，若被重置请再改。

## 已知的 E2E 踩坑（别重复踩）

| 坑 | 说明 |
| --- | --- |
| 候选文案不能照抄单测工厂 | E2E 库里车间是 `E2E-CUT E2E 裁剪车间`、工序是 `01 裁`；单测工厂里叫「一号车间」「拼前」，**E2E 库里根本不存在** |
| antd 4 的搜索框类名 | 是 `input.ant-select-selection-search-input`，在 `.ant-select-selector` **内部**，不在下拉面板里（那是 antd 5 的结构） |
| 候选是预加载的，**不要 `fill` 搜索框** | 填了会按 `option-filter-prop="label"` 过滤成「暂无数据」 |
| antd 收起的下拉仍在 DOM | 等隐藏要用 `.ant-select-dropdown:visible`，否则立刻返回、什么都没等到 |
| 两字中文按钮有空格 | `<Button>审核</Button>` 渲染成「审 核」，一律用正则 `/审\s*核/` |
| antd Table 有零高度测量行 | `.ant-table-tbody tr` 的第一个是隐藏测量行，要用 `tr.ant-table-row` |
| 字段定位用 `.form-label` | 不是 `.ant-form-item-label` |

## 跑通之后

1. 更新 `docs/tasks/T-BUND-010-标签打印前端与E2E.md` 的验收清单
2. `docs/12` 补变更记录（下一个可用编号 **0138**，0124/0125 的撞号已修过，
   最大是 0137）
3. 提交，格式参考 `c76f665`

## 当前整体状态（都已绿，不必重跑）

- 后端闸门 1–4：992 passed / 91.38% / 迁移零漂移
- 闸门 5：api-image + web-image 均构建并可启动
- 前端：lint ✅ / typecheck **0** / 单测 **233 passed** / build ✅
- 后端迁移已到 `0018`（视图授权），别再加重复迁移

## 本会话已挖出并修复的缺陷（供了解上下文）

1. 打印登记漏传必传 `hands_seq` → 标签打印 100% 失败
2. `#bodyCell` 用 `rowIndex`（antd 只给 `index`）→ 行号 NaN、编辑全无效
3. 新建页 3 个选择器没渲染（`<Combo>` 未 import + 2 处 kebab 标签）
4. `ExpandedRowRender` 与 `@expand` 签名混淆 → 展开行永远「加载中…」
5. `planned_qty: null` 误判 → 新建明细行恒红
6. `router.push` 传 ComputedRef
7. 视图 `v_cutting_output_available` 从未授权应用账号（迁移 0018）
8. 传 UUID 当款号 → 来源下拉永远空
9. 来源明细单元格零尺寸点不到

第 8、9 条是本次 E2E 直接挖出来的 —— **E2E 这条路径确实能挖出单测抓不到的东西**，
值得继续跑完。
---

## 事后补记：那次「POST 压根没发出」是怎么查出来的

**手法**（下次直接照抄，不要重新推）：

```ts
// 挂在 beforeEach 里，四行就够
page.on('console', (m) => console.log('[console]', m.type(), m.text()))
page.on('pageerror', (e) => console.log('[pageerror]', e.message))
page.on('request', (r) => console.log('[REQ]', r.method(), r.url()))
page.on('response', async (r) => console.log('[RES]', r.status(), await r.text()))
```

⚠️ **别加过滤条件**。最初只 `if (url.includes('/bundling-orders'))`，
结果 `/bundles`、`/labels` 两个关键请求被滤掉，白查一轮。
⚠️ **跑之前先 `pkill -f "uvicorn app.main"`**：`reuseExistingServer: !CI` 会复用
旧实例，日志不在你手里 —— 「后端日志里没有这条请求」这个观察本身就是错的
（POST 发出去了，只是打到了别人的实例上）。

**结论**：`.block-line` 为 0 ≠ 请求合法。表单自己的校验只管「必填有没有填」，
**不管值的形状对不对**；真正的形状校验在后端 422 里，于是页面上只剩一句
「参数校验失败，请检查标红字段」—— 而这个页面**一个字段都没标红**。
