import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

/**
 * E2E-02：打菲单主流程 —— 建单 → 提交 → 审核 → 码数=手数 → 打印标签。
 *
 * ## 这条用例验的是**跨模块一致性**，不是「每一步点得动」
 *
 * 组件测试（`Form.test.ts` / `LabelPrint.test.ts` / `Detail.test.ts`）已经守住了
 * 「点得动」；这里验的是只有**真浏览器 + 真后端**才暴露的东西：
 *
 * 1. **码数 = 手数**（03 §5.1 第 5 步）：审核时才生成 `bundles`，一码一手；
 * 2. **余量被占用**：审核后 `available_qty` 应当减少，否则「打完还能再打」；
 * 3. **打印登记带上 `hands_seq`**（B15 + 10002）：这条曾经**真的漏传**，
 *    单测只断言「函数被调用」、没断言参数，所以一直没抓到。
 *
 * ## ⚠️ 来源裁剪单为什么由 seed 造，而不是本用例建
 *
 * B8 要求来源裁剪单必须 `APPROVED`，而**裁剪模块没有 submit/approve 端点**
 * （`openapi.json` 里 `cutting-orders` 下只有 `get/post`，`{order_id}` 下只有
 * `delete/get/patch`）—— 裁剪单在系统里走不到 APPROVED，只能由
 * `backend/tests/e2e_seed.py::_seed_approved_cutting` 直接落库。
 *
 * 种子给的固定数据（`CUTTING_SIZE_PLAN`）：**XL 2 手 / L 1 手，每手 60 件**，
 * 色码 WHT、色组 WHT-GRP，款号 `E2E-2026-0001`。
 *
 * ## ⚠️ 三处与裁剪 E2E 不同的写法
 *
 * 1. **打菲侧全部用 antd `Select`，没有一个 `Combo`**（裁剪 E2E 大量用）。
 *    `Select` 的候选项在弹层里（Teleport 到 body），要按 `.ant-select-item-option`
 *    的文本点，且**每次选完要等弹层收起** —— 否则下一步点按钮会报
 *    `element is not stable`（等 bounding box 连续两帧不变）。
 * 2. **antd 会给「两字中文」按钮插空格**：`<Button>审核</Button>` 渲染成
 *    `审 核`。所以按钮一律用**正则**匹配（`/审\s*核/`），不用字符串相等。
 * 3. **`.form-label` 定位字段**：antd `Form.Item` 的 label 是自定义的
 *    `<label class="form-label">`，不是 `.ant-form-item-label`。
 */

const STYLE_NO = 'E2E-2026-0001'
const CUTTING_DOC_NO = 'CT-E2E-APPROVED-0001'

// ⚠️ 这两个名字**必须**取自 `e2e_seed.py` 造出来的数据，不能照抄单测工厂
//   （`tests/factories/bundling.py` 里车间叫「一号车间」、工序叫「拼前」——
//   那套名字 E2E 库里**根本不存在**，照抄会一直报「没有候选项可选」，
//   而页面上输入框明明在那儿，看不出是自己名字写错了）。
const WORKSHOP_LABEL = 'E2E-CUT E2E 裁剪车间'
const OPERATION_LABEL = '01 裁'
const EMPLOYEE_NO = process.env.E2E_EMPLOYEE_NO ?? 'E2EADMIN'
const APPROVER_NO = process.env.E2E_APPROVER_NO ?? 'E2EAUDITOR'
const PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? ''

async function loginAs(page: Page, employeeNo: string): Promise<void> {
  await page.goto('/login')
  await page.getByPlaceholder('请输入工号').fill(employeeNo)
  await page.getByPlaceholder('请输入口令').fill(PASSWORD)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page).not.toHaveURL(/\/login/)
}

/** 走真实的「退出登录」，不是清 sessionStorage。 */
async function logout(page: Page): Promise<void> {
  await page.locator('.admin-user').click()
  await page.getByRole('menuitem', { name: '退出登录' }).click()
  await expect(page).toHaveURL(/\/login/)
}

/**
 * 在 antd `Select` 里按文本选一项。
 *
 * @param field 字段的 label 文本（`.form-label`）
 * @param text 选项里应当出现的文本
 *
 * ⚠️ **必须等弹层收起**再返回：antd 的下拉是绝对定位，展开/收起会改变附近元素的
 *   位置，Playwright 下一步点按钮时会报 `element is not stable` —— 而那个按钮
 *   明明可见可点，报错完全看不出「是上一步的下拉还在动」。
 */
async function selectOption(page: Page, field: string, text: string | RegExp): Promise<void> {
  const container = page
    .locator('.form-label', { hasText: new RegExp(`^${field}$`) })
    .locator('xpath=..')
  await container.locator('.ant-select-selector').first().click()

  // ⚠️ 候选是**预加载**的（`Form.vue` 挂载时一次性拉完），所以**不要往搜索框里
  //   `fill`**：填了之后 antd 按 `option-filter-prop="label"` 过滤，
  //   而过滤的匹配规则与我们要找的文本不一定一致（实测会得到「暂无数据」的
  //   空面板）。直接点选项即可。
  const option = page
    .locator('.ant-select-dropdown:visible .ant-select-item-option')
    .filter({ hasText: text })
    .first()
  // ⚠️ 「来源裁剪单」的候选是**点开后异步拉的**（`available-source-orders`），
  //   10 秒的 waitFor 在接口慢一点时刚好卡在边界上，偶发失败难查。这里放宽到 20s。
  await option.waitFor({ state: 'visible', timeout: 20_000 })
  await option.click()

  // ⚠️ **必须等下拉收起**。antd 4 会把收起的面板留在 DOM 里（只加
  //   `ant-select-dropdown-hidden`），所以不能用「等 hidden」，要用 `:visible` 过滤 ——
  //   否则那个「等隐藏」会**立刻返回、什么都没等到**，下拉就那么盖在页面上，
  //   下一步点别的字段全被它挡掉，而 Playwright 报的是「没有候选项可选」，
  //   完全看不出「上一步的下拉还开着」。
  await page.locator('.ant-select-dropdown:visible').first().waitFor({
    state: 'hidden',
    timeout: 10_000,
  })
}

/** 按 label 文本找到那个字段的输入框（色码 / 色组是纯 Input，不走 Select）。 */
function fieldInput(page: Page, field: string): Locator {
  return page
    .locator('.form-label', { hasText: new RegExp(`^${field}$`) })
    .locator('xpath=..')
    .locator('input')
    .first()
}

/**
 * 取一次响应的**统一响应包装**（docs/05 §1：`{code, message, data, ...}`）。
 *
 * ⚠️ 必须先 `waitForResponse` 拿到 promise 再 `await` 它 —— 直接 `await
 *   page.request.get()` 走的是 API 上下文、拿不到**页面这次点击**真实发出的
 *   那个响应（两者可能因为重试/幂等而不同）。
 */
async function readEnvelope(source: Promise<Response>): Promise<{ code: number; message: string }> {
  const body = (await (await source).json()) as { code: number; message: string }
  return body
}

/** 同 :func:`readEnvelope`，但取 `data` 层并顺手断言业务码为 0。 */
async function readData<T>(source: Promise<Response>): Promise<T> {
  const body = (await (await source).json()) as { code: number; message: string; data: T }
  expect(body.code, `业务码非 0：${body.message}`).toBe(0)
  return body.data
}

/**
 * 等单据状态变成 `status`。
 *
 * ⚠️ 用 `.status-tag[data-status="…"]` 而不是 `getByText('已审核')`：
 *   - `StatusTag`（`@/components/StatusTag.vue`）把后端状态渲染成中文，
 *     且**自带 `data-status` 原始值** —— 这是唯一不会被文案改动的锚点；
 *   - 中文文案里 `SUBMITTED` 显示的是「待审核」而不是「已提交」，用中文断言
 *     等于把断言绑死在文案上：`status.ts` 改一个字，测试就红，而业务没坏。
 *
 * ⚠️ **打菲的状态动作没有二次确认弹窗**（`Detail.vue` 的 `submit`/`approve`
 *   直接调接口 + `message.success`）。原先这里写着「点确认弹窗里的按钮」，
 *   于是用例在动作**已经成功之后**去等一个永远不会出现的弹窗 ——
 *   报错是「waiting for locator('.ant-modal')...」，而页面上单据明明已经提交了。
 */
function statusTag(page: Page, status: string) {
  return page.locator(`.status-tag[data-status="${status}"]`)
}

test.describe('E2E-02 打菲单主流程', () => {
  test.beforeEach(async ({ page }) => {
    if (PASSWORD === '') {
      throw new Error('缺少 E2E_ADMIN_PASSWORD：E2E 需要一个**不用强制改密**的专用账号')
    }
    await loginAs(page, EMPLOYEE_NO)
  })

  test('建单 → 提交 → 审核 → 码数=手数 → 打印登记', async ({ page }) => {
    // ---------------- 建单 ----------------
    await page.goto('/bundling/orders')
    await expect(page.getByRole('heading', { name: '打菲单' })).toBeVisible()
    await page.getByRole('button', { name: /新建打菲单/ }).first().click()
    await expect(page.getByRole('heading', { name: '新建打菲单' })).toBeVisible()

    await selectOption(page, '车间', WORKSHOP_LABEL)
    await selectOption(page, '款号', STYLE_NO)
    // 色码 / 色组是纯 Input —— `selectOption` 找不到，用 fieldInput。
    // ⚠️ 两个字段之间必须等一拍：`v-model` 更新要下一 tick 才传到子组件的 props，
    //   紧挨着写会让后一个字段展开**旧 modelValue**、把前一个覆盖回空
    //   （`Form.test.ts` 踩过同一条）。
    await fieldInput(page, '色码').fill('WHT')
    await expect(fieldInput(page, '色码')).toHaveValue('WHT')
    await fieldInput(page, '色组').fill('WHT-GRP')
    await selectOption(page, '工序', OPERATION_LABEL)

    // 来源裁剪单：候选来自 `available-source-orders`，要款号 + 车间定完才有
    await selectOption(page, '来源裁剪单', CUTTING_DOC_NO)

    // 新增一行明细并填尺码 —— `addLine()` 建的是**全空行**，不填 `blocking` 会拦提交
    await page.getByRole('button', { name: /新增行/ }).first().click()
    const sizeInput = page.locator('input[placeholder*="XL / L / M"]').first()
    await sizeInput.fill('XL')

    // 明细行的「来源裁剪明细行」：点 `.code-cell` 进编辑态 → 出现 Select → 选尺码明细行。
    //
    // ⚠️ 必须用 `:visible` 过滤：antd Table 会插一行**高度为 0 的隐藏测量行**
    //   （`padding:0; height:0`），`.first()` 命中的就是它 ——
    //   而报错是「click 超时」，页面上那个单元格明明可见可点。
    // ⚠️ 点完之后 `.code-cell` span **被 Select 整个替换掉**了，所以不能再拿它
    //   往下定位（元素已从 DOM 移除）—— 那会报「元素不存在」，
    //   而页面上明明刚弹出了下拉。改从**行**重新定位。
    // ⚠️ 选完**会退出编辑态**（`onSourceChange` 收尾），所以 `.code-cell` 会回来 ——
    //   这条曾经不成立：编辑键是 `cutting_size_line_id`，而选中候选不保证触发
    //   `@blur`，于是 `editingKey` 卡死，操作列的「编辑」按钮**凭空消失**，
    //   下面的「手数改 2」那一步就点不到按钮了。
    const dataRow = page.locator('.ant-table-tbody tr.ant-table-row').first()
    await dataRow.locator('.code-cell').first().click({ force: true })
    await dataRow.locator('.ant-select-selector').first().click()
    const lineOption = page
      .locator('.ant-select-dropdown:visible .ant-select-item-option')
      .filter({ hasText: /^XL/ })
      .first()
    await lineOption.waitFor({ state: 'visible', timeout: 20_000 })
    await lineOption.click()
    await page.locator('.ant-select-dropdown:visible').first().waitFor({ state: 'hidden' })

    // 手数：新建行默认 **1**，改成 **2**。
    // ⚠️ 这一步不能省 —— 后面的核心断言「码数 = 手数」用的是 2，
    //   而默认 1 会让整条用例变成「码数 = 1 = 手数」的空转断言：
    //   1 = 1 在任何实现下都成立，验不出「按手批量生成」有没有真的按手来。
    //   点行尾「编辑」把手数列切进编辑态（`.code-cell` 那套点法只对来源列有效）。
    await dataRow.getByRole('button', { name: /^编\s*辑$/ }).click()
    await dataRow.locator('.ant-input-number-input').first().fill('2')
    await dataRow.getByRole('button', { name: /^保\s*存$/ }).click()

    // ⚠️ 保存前先确认**没有阻断项**：`.block-line` 每条对应一个 `blocking` 原因，
    //   页面上有一条就说明还没填完。而「保存草稿」在有阻断时是 `disabled` 的，
    //   Playwright 点它会一直等到超时，报错完全看不出「按钮是灰的」。
    await expect(page.locator('.block-line')).toHaveCount(0)

    await page.getByRole('button', { name: /保存草稿/ }).first().click()

    // 建单成功会跳详情页；单号形如 BD-YYYYMMDD-NNNNNN
    await expect(page.getByRole('heading', { name: /BD-\d{8}-\d{6}/ })).toBeVisible({ timeout: 15_000 })
    // ⚠️ 单号只能从**标题**取，不能用 `getByText(/BD-\d{8}-\d{6}/).first()`：
    //   建单成功后页面上同时挂着一条 `message.success('已保存草稿 BD-…')` 的
    //   浮动提示，它的文本**包含**单号，且 antd 的 message 容器挂在 body 上 ——
    //   `.first()` 命中它就取到 `已保存草稿 BD-20261012-000008`，
    //   后面那句「回到列表确认落库」于是拿一整串提示文案去找单号，永远找不到。
    const docNo = (await page.getByRole('heading', { name: /BD-\d{8}-\d{6}/ }).textContent())?.trim()
    expect(docNo).toMatch(/^BD-\d{8}-\d{6}$/)
    await expect(statusTag(page, 'DRAFT')).toBeVisible({ timeout: 15_000 })
    // 记下详情页地址：审核要**换账号**重新进来（见下面「换人审核」）
    const detailUrlId = new URL(page.url()).pathname.split('/').pop() ?? ''
    expect(detailUrlId).toMatch(/^[0-9a-f-]{36}$/)

    // ★ 审核**之前**按手列表必须为空 —— 码是审核时才生成的（B21）。
    //   少这一条的话，「审核后码数 = 手数」里的「码」就分不清是审核生成的还是本来就有。
    await expect(page.locator('.hands-table .ant-table-tbody tr.ant-table-row')).toHaveCount(0)

    // ---------------- 提交 ----------------
    await page.getByRole('button', { name: /^提\s*交$/ }).first().click()
    await expect(statusTag(page, 'SUBMITTED')).toBeVisible({ timeout: 15_000 })

    // ---------------- 换人审核（制单人不能审自己的单，10005） ----------------
    // ⚠️ **必须换账号**，且必须走真实的「退出登录」。
    //   同一个账号点审核拿到的是 409 `10005 制单人不能审核自己开的打菲单`，
    //   报错看着像「审核功能没实现」，实际是这条双人复核规则在正常工作 ——
    //   而 E2E 要验的恰恰是「审核能生成码」，所以得有一个**不是制单人**的账号。
    //   （这条规则本身由后端单测验，E2E 这边只需保证数据够用。）
    await logout(page)
    await loginAs(page, APPROVER_NO)
    await page.goto(`/bundling/orders/${detailUrlId}`)
    await expect(statusTag(page, 'SUBMITTED')).toBeVisible({ timeout: 15_000 })

    await page.getByRole('button', { name: /^审\s*核$/ }).first().click()
    await expect(statusTag(page, 'APPROVED')).toBeVisible({ timeout: 20_000 })

    // ★ 核心断言：**码数 = 手数**（03 §5.1 第 5 步，一码一手）。
    //   这里数的是**真实的码行**（`GET /bundles`），不是表头的 `hands_total` 汇总 ——
    //   汇总数是后端算的，它对不对与「有没有真的生成 2 个码」是两件事。
    const codeRows = page.locator('.hands-table .ant-table-tbody tr.ant-table-row')
    await expect(codeRows).toHaveCount(2, { timeout: 20_000 })
    // 「第 N 手 / 共 M 手」与标签上印的是同一口径（03 §11.5.2）
    await expect(page.getByText('第 1 手 / 共 2 手').first()).toBeVisible()
    await expect(page.getByText('第 2 手 / 共 2 手').first()).toBeVisible()
    // 码带自己的打菲号（B1），标签与扫码都靠它
    await expect(page.locator('.hands-table .code-cell').first()).toContainText(/^BD-/)

    // ---------------- 打印标签 ----------------
    await page.getByRole('button', { name: /打印标签/ }).first().click()
    await expect(page.getByText('打印参数').first()).toBeVisible({ timeout: 15_000 })

    await page.getByRole('button', { name: /生成预览/ }).first().click()
    // 标签预览按手出，每手一张 —— XL 2 手 ⇒ 2 张
    await expect(page.locator('.label-card')).toHaveCount(2, { timeout: 15_000 })
    // 「第 N 手 / 共 M 手」成品文案（B25 必印）
    await expect(page.getByText('第 1 手 / 共 2 手').first()).toBeVisible()
    await expect(page.getByText('第 2 手 / 共 2 手').first()).toBeVisible()

    // ★ 打印登记**必传 `hands_seq`**（B15，缺失报 10002）。
    //   这条真的漏传过：单测只断言「函数被调用」、没断言参数，所以一直没抓到。
    //   断言点放在「登记成功」提示上 —— 漏传 hands_seq 会直接报 10002。
    //
    // ⚠️ **直接断响应体**，而不是只等提示条：`LabelPrintOut` 里
    //   `print_ids` / `printed_count` / `label_print_qty` / `is_reprint` 才是
    //   「一手一行留痕」的契约本身，提示条只说明「没报错」。
    const firstPrint = page.waitForResponse(
      (r) => r.url().includes('/label-prints') && r.request().method() === 'POST',
    )
    await page.getByRole('button', { name: /^打\s*印$/ }).first().click()
    await expect(page.getByText('打印登记成功').first()).toBeVisible({ timeout: 15_000 })
    const firstBody = await readData<{
      print_ids: string[]
      printed_count: number
      label_print_qty: number
      is_reprint: boolean
    }>(firstPrint)
    // XL 2 手 ⇒ 一手一行 ⇒ 2 个留痕 id；打印张数 2 张；累计 2 张
    expect(firstBody.print_ids).toHaveLength(2)
    expect(firstBody.printed_count).toBe(2)
    expect(firstBody.label_print_qty).toBe(2)
    expect(firstBody.is_reprint).toBe(false)

    // ---------------- 重打（TC-LE-03） ----------------
    // ⚠️ **`window.print()` 在 headless Chromium 里是无副作用的空操作**，
    //   而「打印」按钮是「先登记、再调起打印」两件事连做 —— 所以这条只能断**登记**
    //   那一半。另一半（真的出纸）**任何自动化都验不了**，别假装能验。
    // ⚠️ 点 label 里的 **checkbox** 而不是文字：「重打」这两个字同时出现在
    //   label 与它包着的 span 上，按文本点会命中两个元素（strict mode 报歧义）。
    //   antd 的 checkbox 的真实 input 是 `input[type=checkbox]`。
    await page.locator('input[type="checkbox"]').first().check()
    // ⚠️ **先不填批次序号**：`print_seq` 未填时前端不会带这个键，后端报 **`10002`**
    //   （少一个必填业务参数，与缺 `hands_seq` 同类）。⚠️ **不是 `10008`** ——
    //   `10008` 是「这个批次号用过了」（`03 §6` 修订记录第 6 条），两者语义不同，
    //   而 router 的 docstring 此前把缺参写成了 `10008`（已一并更正）。
    //   「重打必带 print_seq」这条规则以前只有一句注释守着，现在有断言了。
    const missingSeq = page.waitForResponse(
      (r) => r.url().includes('/label-prints') && r.request().method() === 'POST',
    )
    await page.getByRole('button', { name: /^打\s*印$/ }).first().click()
    const missingBody = await readEnvelope(missingSeq)
    expect(missingBody.code).toBe(10002)
    // ⚠️ 类名是 `ant-message-error`（antd 4 把类型挂在 **content 那一层**上，
    //   外层 notice 恒为 `ant-message-notice`）—— 写 `ant-message-notice-error`
    //   会一直等不到，且看不出是类名记错了。
    //   断言「**出现了 error 提示**」而不是它的文案：`LabelPrint.vue` 的 catch
    //   弹的是 `error.message`（业务原文），不是写死的「打印登记失败」——
    //   那句兜底文案只在**没有 message** 时才出现。按文案断言会把「业务提示换了个
    //   说法」变成一次假红，而真正该守的「用户看到的是错误提示、不是成功」与文案无关。
    // ⚠️ **必须 `.last()`**：antd 的提示是**堆叠**的，前面失败那次的还没消失，
    //   不加 `.last()` 就是 strict mode 报「匹配到 2 个」—— 报错与「提示没出现」
    //   长得完全不像，而实际是「出现了两次」。
    await expect(page.locator('.ant-message-error').last()).toBeVisible({ timeout: 15_000 })

    // 补上批次序号再打一次：这一次必须是**新行**（print_ids 与第一次不重叠）
    const reprint = page.waitForResponse(
      (r) => r.url().includes('/label-prints') && r.request().method() === 'POST',
    )
    // ⚠️ 断**请求体里真的有 print_seq**：只断响应码不够 ——
    //   上一版这里写的还是 `Form.Item name` 抢 `v-model` 的旧结论，而真实原因是
    //   antd 受控属性叫 `value` 不叫 `modelValue`（详见 LabelPrint.vue 那段注释）。
    //   那时这一框**填了字根本没进 model**，请求体里压根没这个键。响应码 `10002`
    //   只能证明「后端发现缺参」，证不了「用户填的值到了没有」。
    await page.locator('.ant-input-number-input').last().fill('1')
    await page.getByRole('button', { name: /^打\s*印$/ }).first().click()
    await expect(page.getByText('重打登记成功').first()).toBeVisible({ timeout: 15_000 })
    const reprintBody = await readData<{
      print_ids: string[]
      printed_count: number
      label_print_qty: number
      is_reprint: boolean
      print_seq: number | null
    }>(reprint)
    expect(reprintBody.is_reprint).toBe(true)
    expect(reprintBody.print_seq).toBe(1)
    // ⚠️ **新行**：重打是追加一条留痕、不是改原行 —— 「重打产生新行」是这张卡的
    //   核心契约，断言「数量对」验不出「有没有覆盖原行」
    expect(reprintBody.print_ids).toHaveLength(2)
    for (const id of reprintBody.print_ids) {
      expect(firstBody.print_ids).not.toContain(id)
    }
    // 重打**也累加**累计张数（LabelPrintOut 的 description 明写）
    expect(reprintBody.label_print_qty).toBe(4)

    // ★ **同一个批次序号再打一次 → `10008`**（03 §6 修订记录第 6 条）。
    //   这是「没有批次号就无法去重」的**直接证据**：批次号重复必须被拒绝，
    //   否则重打会退化成「又打了一次正常打印」。而 `04 §7.16` 的 `print_seq`
    //   **没有唯一索引**，去重全靠 service 里那三步 —— 所以这一条只有端到端才验得了。
    //   ⚠️ 与上面「缺 `print_seq`」是**不同的码**：缺参 `10002`、重复 `10008`。
    const duplicateSeq = page.waitForResponse(
      (r) => r.url().includes('/label-prints') && r.request().method() === 'POST',
    )
    await page.getByRole('button', { name: /^打\s*印$/ }).first().click()
    expect((await readEnvelope(duplicateSeq)).code).toBe(10008)
    await expect(page.locator('.ant-message-error').last()).toBeVisible({ timeout: 15_000 })

    // ---------------- 回到列表确认落库 ----------------
    await page.goto('/bundling/orders')
    await expect(page.getByText(docNo!).first()).toBeVisible({ timeout: 15_000 })
  })
})
