import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

/**
 * E2E-01：裁剪单主流程 —— 建单（三层明细）→ 详情 → 编辑 → 删除草稿。
 *
 * ## 这条用例验的是**跨层一致性**，不是「每一步点得动」
 *
 * 组件测试（T-CUT-001c-3a/3b/3c）已经守住了「点得动」；这里验的是：
 *
 * 1. **浏览器里录的三层，落库后逐字对得上**（后端 C6 重算的汇总与前端算的一致）；
 * 2. **刷新后还在** —— 少了这条，「页面显示的是内存里的临时状态」测不出来，
 *    而那正是「我明明填了保存，刷新就没了」的成因；
 * 3. 编辑一次再保存，**版本链能连上**（`PATCH` → `PUT /lines` 各自 bump version）。
 *
 * ⚠️ **布批数据由 API seed 准备**（`backend/tests/e2e_seed.py::_seed_fabric`）：
 *   新建页的布批行只接受 `stock_id`（必填 UUID，ADR-0022「不允许自由输入缸号」），
 *   而布批**没有 UI 入口**（它由到货登记产生）。
 *
 * ⚠️ **Combo 是自定义组件**（`Combo.vue`，不是 antd `Select`）：结构是
 *   `input.combo-input[role=combobox]` + `.combo-panel[role=listbox]` +
 *   `.combo-option[role=option]`。候选项**不是**原生 option 元素，所以只能按
 *   `.combo-option` 的文本点。候选有 300ms 防抖 → 每次选完必须等输入框回显。
 */

const STYLE_NO = 'E2E-2026-0001'
const DYE_LOT_NO = 'E2E-LOT-0001'
const EMPLOYEE_NO = process.env.E2E_EMPLOYEE_NO ?? 'E2EADMIN'
const PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? ''

async function login(page: Page): Promise<void> {
  await page.goto('/login')
  await page.getByPlaceholder('请输入工号').fill(EMPLOYEE_NO)
  await page.getByPlaceholder('请输入口令').fill(PASSWORD)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page).not.toHaveURL(/\/login/)
}

/**
 * 在 Combo 里选一项（按 placeholder 定位那个输入框）。
 *
 * @param keyword 打进输入框的关键字
 * @param expectText 候选里应当出现的文本（布批候选的 label 是「缸号/匹号」，
 *   款号候选的 label 是「款号 款名」—— 两者不完全等于关键字，所以单独传）
 */
/**
 * 点「加尺码行 / 加颜色」这类**在嵌套表格里**的按钮。
 *
 * ⚠️ 必须 `force: true`：这些按钮在**三层嵌套的 antd 表格**里（行表 → 色表 →
 *   尺码表），外层还带 `:scroll="{x}"` 的横向滚动容器。加一行会让内层表格高度变化，
 *   外层滚动容器随之微调 —— Playwright 的 stability 检查要求 bounding box
 *   **连续两帧不变**，而它永远做不到，于是报 `element is not stable`。
 *   那个报错的措辞完全看不出「是布局在动，不是按钮点不到」。
 *
 * ⚠️ 只对这两处用 `force`：其余点击仍走完整可操作性检查（可见 / 可用 / 收到事件），
 *   否则会真的盖住「有个浮层挡住了按钮」这类问题。
 */
async function clickNested(page: Page, name: string): Promise<void> {
  await page.getByRole('button', { name }).first().click({ force: true })
}

async function pickCombo(
  page: Page,
  placeholder: string,
  keyword: string,
  expectText: string | RegExp = keyword,
): Promise<void> {
  const input = page.locator(`input.combo-input[placeholder="${placeholder}"]`).first()
  await input.click()
  await input.fill(keyword)
  const option = page.locator('.combo-option').filter({ hasText: expectText }).first()
  // ⚠️ 必须先 `waitFor` 再点：候选是防抖出来的，直接点会报「元素不存在」，
  //   而页面上明明有输入框 —— 报错完全看不出是「还没加载出来」。
  await option.waitFor({ state: 'visible', timeout: 10_000 })
  await option.click()
  // ⚠️ ★ 等下拉**收起**：Combo 的面板是绝对定位的，展开/收起会改变附近元素的
  //   位置，于是下一步点「加尺码行」时 Playwright 报 `element is not stable`
  //   （等 bounding box 连续两帧不变）—— 而页面上那个按钮明明可见可点。
  //   这个报错的措辞完全看不出「是上一步的下拉还在动」。
  await page.locator('.combo-panel').first().waitFor({ state: 'detached', timeout: 10_000 })
}

test.describe('E2E-01 裁剪单主流程', () => {
  test.beforeEach(async ({ page }) => {
    if (PASSWORD === '') {
      throw new Error('缺少 E2E_ADMIN_PASSWORD：E2E 需要一个**不用强制改密**的专用账号')
    }
    await login(page)
  })

  test('新建三层裁剪单 → 详情逐字对上 → 刷新仍在 → 编辑再存 → 删除草稿', async ({ page }) => {
    await page.goto('/cutting/orders')
    await expect(page.getByRole('heading', { name: '裁剪单' })).toBeVisible()
    await page.getByRole('button', { name: /新建裁剪单/ }).first().click()
    await expect(page.getByRole('heading', { name: '新建裁剪单' })).toBeVisible()

    // ---- 表头：车间 + 款号（款号候选的 value 是 UUID，label 才是款号字符串）
    // ⚠️ 车间候选的 label 是「编码 名称」（`{code} {name}`），所以关键字用**名称**
    //   里的片段（`E2E 裁剪车间`）；只打编码 `E2E-CUT` 也能命中，两个都试过。
    await pickCombo(page, '选车间', '裁剪车间')
    await pickCombo(page, '搜款号 / 款名', STYLE_NO)

    // ---- 第一层：布批行
    await page.getByRole('button', { name: /添一行布批/ }).click()
    await pickCombo(page, '按缸号 / 匹号搜布批', DYE_LOT_NO)
    // ⚠️ 选中后必须能看到门幅与可用量：那是「这匹布还剩多少米」的唯一来源
    //    （C38 的 `40006` 的依据）。看不到 = 用户在盲填。
    await expect(page.getByText(/可用.*m/).first()).toBeVisible()

    await page.locator('input[placeholder="耗料米数"]').first().fill('40')
    // 行可出件数必须 ≥ 尺码明细合计（C5 / C34），先给 40，最后按实际合计回填
    await page.locator('input[placeholder="行可出件数"]').first().fill('40')

    // ---- 第二层：行内颜色
    await clickNested(page, '加颜色')
    await pickCombo(page, '选色码', 'NVY', /NVY/)

    // ---- 第三层：尺码明细
    await clickNested(page, '加尺码行')
    // ⚠️ 尺码候选的 label 是「`{code} {name}`」，而 `name` 里**没有空格**
    //   （`L(165/84A)`）—— 所以拿「L (165」当关键字一定搜不到（后端拼的是
    //   `code || ' ' || name`，中间那个空格只加在 code 后面）。
    //   用 `/^L /` 精确挑「code 恰好是 L」那一项，避免连 `XL` 一起选中。
    await pickCombo(page, '选尺码', 'L', /^L /)
    await page.locator('input[placeholder="手数"]').first().fill('10')
    await page.locator('input[placeholder="每手件数"]').first().fill('3')
    // C25：件数**只读**自动算（10 × 3 = 30），界面上那一列没有输入框
    await expect(page.getByText('件数（只读）').first()).toBeVisible()
    await expect(page.locator('input[placeholder="行可出件数"]').first()).toHaveValue('40')

    // ---- 保存 → 跳详情
    await page.getByRole('button', { name: /保存草稿/ }).click()
    // ⚠️ **不要锚定 `$`**：保存成功后前端是 `router.push` 到详情页，URL 之后还可能被
    //   守卫补上 redirect 查询串；锚死末尾会让断言在「页面明明已经跳过去了」时失败。
    // ⚠️ 路由是 **`/cutting/orders`**（不是 `/cutting-orders`）—— 写错会让
    //   「明明已经跳到详情页」的断言一直失败，而失败信息只给 Expected / Received 两个串。
    await expect(page).toHaveURL(/\/cutting\/orders\/[0-9a-f-]{36}/)
    // ⚠️ antd 会给**两个汉字**的按钮插空格（`autoInsertSpace`）：实际可访问名是
    //   `编 辑`，写死 '编辑' 会一直等到超时 —— 而按钮就在页面上。
    await expect(page.getByRole('button', { name: /编\s*辑/ })).toBeVisible()
    await expect(page.getByText(/CT-\d{8}-\d{6}/).first()).toBeVisible()
    await expect(page.getByRole('cell', { name: new RegExp(DYE_LOT_NO) }).first()).toBeVisible()
    await expect(page.getByRole('cell', { name: /NVY/ }).first()).toBeVisible()

    // ---- ★ 刷新后还在（跨层一致性的核心一条）
    await page.reload()
    await expect(page.getByRole('cell', { name: new RegExp(DYE_LOT_NO) }).first()).toBeVisible()
    await expect(page.getByRole('cell', { name: /NVY/ }).first()).toBeVisible()

    // ---- 编辑：改手数后保存，验证**版本链**连得上（否则收 10003）
    //
    // ⚠️ 断言「版本号变了」而不是「出现了『已保存』提示」：antd 的 message 3 秒就消失，
    //   而 Playwright 的重试窗口是 10 秒 —— 用提示做断言会**偶发失败**（快机器上
    //   提示在断言前就没了）。版本号是持久状态，而且它才是版本链的直接证据：
    //   一次保存要 bump **两次**（PATCH 表头 + PUT 明细），所以必然变化。
    await page.getByRole('button', { name: /编\s*辑/ }).click()
    await expect(page.getByRole('heading', { name: /CT-\d{8}/ })).toBeVisible()
    const versionTag = page.locator('.ant-tag').filter({ hasText: '版' }).first()
    await expect(versionTag).toBeVisible()
    const versionBefore = await versionTag.innerText()

    await page.locator('input[placeholder="手数"]').first().fill('12')
    await page.getByRole('button', { name: /保\s*存/ }).click()
    // ⚠️ 轮询而不是 `waitFor`：页面要在两个接口都返回后才更新版本号
    await expect(versionTag).not.toHaveText(versionBefore, { timeout: 15_000 })

    // ---- 删除草稿：不可恢复的整单删除，必须走页面上的删除按钮 + 二次确认
    // ⚠️ 确认框是 **antd `Modal.confirm`**（`docs/06 §5` 禁止原生 `confirm()`），
    //   所以这里**不**用 `page.once('dialog')` —— 原生弹窗那条监听对 Modal 永远不触发，
    //   用例会一直等到超时，而页面上那个确认框明明就在。
    await page.getByRole('button', { name: '删除草稿' }).click()
    const confirmModal = page.locator('.ant-modal-confirm')
    await expect(confirmModal).toBeVisible()
    await expect(confirmModal).toContainText('三层明细会「一并作废」')
    // ⚠️ 又是「两个汉字插空格」：可访问名是 `删 除`
    await confirmModal.getByRole('button', { name: /删\s*除/ }).click()
    await expect(page).toHaveURL(/\/cutting\/orders$/)
  })
})