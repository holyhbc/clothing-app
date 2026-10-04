import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

/**
 * E2E-00：登录 → 建款号 → 配 2 工序 → 设价 → resolve → **刷新页面数据仍在**。
 *
 * ## 这条用例真正验的是什么
 *
 * 不是「每一步点得动」—— 那是组件测试的事。这里验的是**跨层一致性**：
 * 浏览器里看到的数字，和后端库里存的一致，且**刷新后还在**。
 *
 * ⚠️ 「刷新后还在」这一条是这里最容易被省掉、也最关键的一条：
 *   少了它，「页面显示的是内存里的临时状态」这种 bug 测不出来 —— 而那正是
 *   「我明明填了保存，刷新就没了」这类线上问题的成因。
 *
 * ## 为什么建款号不用 UI
 *
 * 款号由 `globalSetup` 的 API seed 建好（docs/10 §4）。这里只**读**它。
 * ⚠️ 用 UI 建款号会让这条用例同时覆盖「建款号的表单校验」+「路由」+「接口」，
 *   一失败三处都可疑 —— 拆开之后每条用例只回答一个问题。
 */

const STYLE_NO = 'E2E-2026-0001'
const EMPLOYEE_NO = process.env.E2E_EMPLOYEE_NO ?? 'E2EADMIN'
const PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? ''

/**
 * 登录。
 *
 * ⚠️ 用 `getByPlaceholder` 而不是 CSS 选择器 / text：placeholder 是**给用户看的
 * 提示**，改了它这个用例就该跟着改 —— 这正是我们要的（提示变了意味着界面变了）。
 */
async function login(page: Page): Promise<void> {
  await page.goto('/login')
  await page.getByPlaceholder('请输入工号').fill(EMPLOYEE_NO)
  await page.getByPlaceholder('请输入口令').fill(PASSWORD)
  // ⚠️ 用 `/登\s*录/` 而不是 `'登录'`：antd 对**两个汉字**的按钮会自动插空格
  //    （`autoInsertSpace`），实际可访问名是 `'登 录'`。写死 `'登录'` 会一直
  //    等到超时 —— 而页面上分明有那个按钮，报错极具迷惑性。
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await expect(page.getByText('服装厂 ERP')).toBeVisible()
  // 等路由离开 /login：只等标题可见的话，刷新后任意页面都有这个标题
  await expect(page).not.toHaveURL(/\/login/)
}

test.describe('E2E-00 款号与工序单价主流程', () => {
  test.beforeEach(async ({ page }) => {
    if (PASSWORD === '') {
      throw new Error('缺少 E2E_ADMIN_PASSWORD：E2E 需要一个**不用强制改密**的专用账号')
    }
    await login(page)
  })

  test('款号详情：3 道工序 + 单价区间都在，刷新后不变', async ({ page }) => {
    await page.goto(`/base/styles/${STYLE_NO}`)

    // ---- 款号本身
    // ⚠️ 用 `getByRole('heading', { name: ... })` 而不是 `getByText`：
    //    款号与款名渲染在**同一个** `<h4>` 里（`款号 + 款名` 两段），于是
    //    `getByText('E2E 全棉圆领 T 恤')` 同时命中标题与描述列表那一行，
    //    Playwright 的 strict mode 直接报「resolved to 2 elements」。
    //    锚到 heading 就唯一了。
    await expect(page.getByRole('heading', { name: new RegExp(STYLE_NO) })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'E2E 全棉圆领 T 恤' })).toBeVisible()
    // 大货数量（09 §1.1）：详情页必须显示，缺了界面只能显示「接口未返回」
    await expect(page.getByRole('cell', { name: '1200' })).toBeVisible()

    // ---- 款号工序 Tab（seed 建了 3 道：01 裁 / 02 车 / 90 整烫）
    await page.getByRole('tab', { name: /款号工序/ }).click()
    await expect(page.getByRole('cell', { name: '01' }).first()).toBeVisible()
    await expect(page.getByRole('cell', { name: '02' }).first()).toBeVisible()
    // ⚠️ `exact: true`：不精确匹配时「整烫」会同时命中工序名列与
    //    「最后一道（整烫）」徽标那一行，strict mode 直接判失败。
    //    工序名列里的工序名必须是纯工序名 —— 这是 B-CAT-05 的事实，不带后缀。
    await expect(page.getByRole('cell', { name: '整烫', exact: true })).toBeVisible()

    // ---- 现行价 Tab：金额要对
    // ⚠️ 用 `formatMoney` 的**显示口径**（¥ + 6 位小数）来断言，而不是
    //   「页面上有 0.450000 这几个字符」—— 后者会在格式变化时误报，
    //   前者才是业务上的约定（docs/04 §4）
    await page.getByRole('tab', { name: /现行价/ }).click()
    await expect(page.getByText('¥0.450000').first()).toBeVisible()

    // ---- **刷新**：验的是「数据在后端，不在内存里」
    await page.reload()
    await expect(page.getByRole('heading', { name: new RegExp(STYLE_NO) })).toBeVisible()
    await page.getByRole('tab', { name: /款号工序/ }).click()
    await expect(page.getByRole('cell', { name: '整烫', exact: true })).toBeVisible()
  })

  test('单价列表：显示取价档位与 3 段历史区间', async ({ page }) => {
    await page.goto(`/base/operation-rates?style_no=${STYLE_NO}`)

    // ⚠️ 断言**表头单元格**而不是页面上出现「款号」二字：菜单里也有「款号」，
    //    `getByText('款号')` 会匹配到侧栏，点不到真正的表格内容 ——
    //    于是用例要么假绿，要么报「元素不唯一」，都跟被测行为无关。
    await expect(page.getByRole('columnheader', { name: '款号' })).toBeVisible()
    // ADR-0026 强制显示取价档位：用户要能知道这个价是从哪一档来的
    // ⚠️ 列名是「档位」不是「来源」—— 写错列名时 Playwright 报的是
    //    「element(s) not found」，看起来像页面缺列，实际是断言写错了。
    await expect(page.getByRole('columnheader', { name: '档位' })).toBeVisible()
    await expect(page.getByRole('cell', { name: '款号价' }).first()).toBeVisible()
    // ⚠️ 三段区间必须都在：只看当前价的话，「上个月裁剪单价 0.35」这条就查不到了
    for (const price of ['¥0.350000', '¥0.400000', '¥0.450000']) {
      await expect(page.getByRole('cell', { name: price }).first()).toBeVisible()
    }
  })

  test('菜单里能点到款号页（不许只有路由没有入口）', async ({ page }) => {
    // ⚠️ 「只有路由没有菜单入口」是本仓反复出现的问题（T-WEB-004 移除系统管理两项时
    //    的理由），而**只测路由表测不出来** —— 路由表里当然有，菜单里没有。
    //    所以这条专门走菜单点击。
    // ⚠️ antd 的 Menu 子菜单**不是** `menuitem` role（是普通 `li` + `aria`），
    //    所以只能按文本点。展开后再点子项 ——
    //    先点「展开菜单」是因为侧栏在 1366 宽下默认折叠，折叠时子项不渲染进 DOM。
    // ⚠️ `force: true`：antd 的 Menu 展开/收起带动画，刚点「展开菜单」后侧栏还在
    //    做宽度过渡，此时子菜单标题的 bounding box 每帧都在变，Playwright 的
    //    「element is not stable」会一直重试到超时。这里等的是**展开动作**本身
    //    （下一行的 URL 断言），点标签不需要等稳定。
    await page.getByRole('button', { name: /展开菜单/ }).click()
    await expect(page.getByRole('button', { name: /收起菜单/ })).toBeVisible()
    await page.getByText('基础资料', { exact: true }).click({ force: true })
    await page.getByText('款号', { exact: true }).click({ force: true })

    await expect(page).toHaveURL(/\/base\/styles$/)
    await expect(page.getByRole('columnheader', { name: '款号' })).toBeVisible()
    // 种子建的款号必须出现在列表里 —— 验的是「列表读的是后端」
    await expect(page.getByRole('cell', { name: STYLE_NO })).toBeVisible()
  })

  test('resolve 取价预演：给定日期命中对应区间', async ({ request, page }) => {
    // ⚠️ 这一条**直接打 API**，不走页面：resolve 是纯读接口，没有界面入口
    //   （设计稿 §4.5 把它定位成「单据保存前的预演」）。绕开页面验它，
    //   才不会因为「页面上还没这个功能」而把这条用例挂掉 —— 那属于另一张卡。
    await page.goto('/login')
    const login = await request.post('/api/v1/auth/login', {
      data: { employee_no: EMPLOYEE_NO, password: PASSWORD },
    })
    expect(login.ok(), `登录失败：${login.status()} ${await login.text()}`).toBeTruthy()
    const token = (await login.json()).data.access_token

    // 2026-02-15 落在第 2 段区间（2026-02-01 ~ 2026-03-01）
    // ⚠️ 路径是 `/operation-rates/resolve` + **query 参数** style_no（不是
    //    `/operation-rates/{style_no}/resolve`）—— 写成后者会 404，而报错
    //    「看起来像款号不存在」，极容易往错的方向排查
    const resolved = await request.get(
      `/api/v1/operation-rates/resolve?style_no=${STYLE_NO}&operation_no=01&work_date=2026-02-15`,
      { headers: { Authorization: `Bearer ${token}` } },
    )
    expect(resolved.ok()).toBeTruthy()
    const payload = await resolved.json()
    expect(payload.data.unit_price).toBe('0.400000')
    // ⚠️ 档位必须回显（ADR-0026 强制）：用户要能知道这个价是从哪一档来的
    expect(payload.data.rate_source).toBe('STYLE')
  })
})
