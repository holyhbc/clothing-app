import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { PERM } from '@garment/shared'
import type { CuttingOrderListOut } from '@garment/shared'
import * as cuttingApi from '@/api/cutting'
import { baseApi } from '@/api/base'
import * as stylesApi from '@/api/styles'
import { permission } from '@/directives/permission'
import { resetHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import CuttingOrderList from '@/views/cutting/List.vue'

/**
 * 裁剪单列表页（T-CUT-001c-2b，TC-CUT-W1 ~ TC-CUT-W4）。
 *
 * 打桩 `api/cutting` 而不是 `fetch`：这些用例要验的是「界面选了筛选之后**调哪个函数、
 * 传了什么参数**」，打桩 fetch 会把用例绑死在 URL 与 query 拼接上。
 *
 * ⚠️ `baseApi` 要**具名导入**（`import { baseApi }`）而不是 `import * as baseApi`：
 *    后者拿到的是模块命名空间，上面并没有 `workshops` 这个顶层导出，
 *    `vi.spyOn(baseApi.workshops, ...)` 会报「could not find an object to spy upon」——
 *    而这条报错完全看不出是导入方式错了。
 *
 * ⚠️ 「列表要传等值筛选而不是模糊搜索」这条是本文件最要紧的用例（TC-CUT-W2）：
 *    后端 `list_cutting_orders` **没有 `q` 参数**。如果哪天有人「顺手加个搜索框」，
 *    前端会安静地传一个后端忽略的参数 —— 界面显示「搜过了」而结果根本没筛。
 */

/** 见 `views/base/styles/StylePages.test.ts`：带弹层的 wrapper 必须同步卸载，否则跨用例污染。 */
const mountedWrappers: { unmount: () => void }[] = []

function track<T extends { unmount: () => void }>(wrapper: T): T {
  mountedWrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  while (mountedWrappers.length > 0) mountedWrappers.pop()?.unmount()
  document.body.innerHTML = ''
})

/**
 * 列表行。
 *
 * ⚠️ 字段与后端 `CuttingOrderListOut` **逐个对齐**（含 `color_codes` 缺席这件事）：
 * 多写一个字段名不会报错，只会在联调那天变成「表格里那一列永远是空白」。
 */
const ROW: CuttingOrderListOut = {
  id: '33333333-3333-3333-3333-333333333333',
  doc_no: 'CT-20261005-000123',
  workshop_id: '44444444-4444-4444-4444-444444444444',
  style_no: 'HB-2026-0001',
  doc_date: '2026-10-05',
  ply_count: 4,
  fabric_qty: '96.000',
  output_qty: '120',
  balance_qty: '0',
  hands_total: 40,
  status: 'DRAFT',
  version: 3,
  created_at: '2026-10-05T02:10:00Z',
}

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

function grant(codes: string[]): void {
  useAuthStore().permissions = new Set(codes)
}

function stubRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/cutting/orders', name: 'cutting-orders', component: { template: '<div />' } },
      {
        path: '/cutting/orders/:orderId',
        name: 'cutting-orders-detail',
        component: { template: '<div />' },
      },
    ],
  })
}

/**
 * 挂载列表页，返回 wrapper 与 router（要断言跳转目标）。
 *
 * @param total 分页总数。⚠️ 默认给 **500** 而不是 1：antd 的分页器只渲染出页码按钮，
 *   `total <= size` 时 `.ant-pagination-item-3` 根本不存在，「改筛选回第 1 页」
 *   那条用例就只能靠 `.catch(() => undefined)` 吞掉 —— 那等于把断言废掉。
 */
async function mountList(total = 500) {
  const list = vi
    .spyOn(cuttingApi, 'listCuttingOrders')
    .mockResolvedValue({ items: [ROW], total })
  // ⚠️ `disabled` 是**必填**（docs/05 §9.5.2 的候选结构），少写会在 typecheck 阶段红，
  //    而 vitest 不做类型检查 —— 只跑 `test:unit` 会一路绿到闸门 2 才炸。
  vi.spyOn(stylesApi, 'searchStyleOptions').mockResolvedValue([
    { value: 'HB-2026-0001', label: 'HB-2026-0001', disabled: false },
  ])
  vi.spyOn(baseApi.workshops, 'optionsById').mockResolvedValue([
    { value: '44444444-4444-4444-4444-444444444444', label: '一号车间', disabled: false },
  ])
  const router = stubRouter()
  await router.push('/cutting/orders')
  await router.isReady()
  const wrapper = track(
    mount(CuttingOrderList, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  return { wrapper, router, list }
}

/** 表格正文文本（antd 会把汉字拆到多个元素里，整段 textContent 反而好比对）。 */
function bodyText(): string {
  return plain(document.querySelector('.ant-table-tbody')?.textContent ?? '')
}

/**
 * 去空白再比对。
 *
 * ⚠️ antd 会在**两个汉字之间插一个空格**（`ant-btn-two-chinese-characters`），
 * 所以按钮文本是 `重 置` 而不是 `重置`。直接 `includes('重置')` 永远匹配不上，
 * 而断言失败信息是「expected undefined to be defined」，完全看不出是空格问题。
 */
function plain(text: string): string {
  return text.replace(/\s/g, '')
}

/** 按文本（去空白后）找一个按钮。 */
function findButton(label: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find((node) =>
    plain(node.textContent ?? '').includes(label),
  )
}

describe('裁剪单列表（TC-CUT-W1 ~ TC-CUT-W4）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.CUTTING_READ])
  })

  it('TC-CUT-W1 单号、款号、状态按后端口径渲染，尾数为 0 显示「—」而不是「0」', async () => {
    await mountList()
    const text = bodyText()
    expect(text).toContain('CT-20261005-000123')
    expect(text).toContain('HB-2026-0001')
    // 状态必须走 `StatusTag`（docs/06 §1：色值唯一映射来源），中文是「草稿」
    expect(text).toContain('草稿')
    // 尾数 0 是常态，显示「—」才不会把真正 >0 的尾数淹没
    expect(text).not.toContain('0.000')
  })

  it('TC-CUT-W2 筛选是**等值**传参，没有 `q` 这种后端忽略的参数', async () => {
    const { list } = await mountList()
    list.mockClear()

    // ⚠️ antd Select 的选项**在下拉打开后才渲染**（关着时 DOM 里根本没有选项节点），
    //    所以先点开选择器再点选项 —— 直接找选项会得到「找不到」，而报错完全看不出
    //    是「下拉还没打开」。
    const selector = document.querySelector('.ant-select-selector')
    expect(selector, '筛选区应有状态下拉').toBeDefined()
    selector?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    selector?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    const option = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
      (node.textContent ?? '').includes('待审核'),
    )
    expect(option, '状态下拉里应有「待审核」').toBeDefined()
    option?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(list).toHaveBeenCalled()
    const query = list.mock.calls[0]?.[0] ?? {}
    expect(query['status']).toBe('SUBMITTED')
    // ⚠️ 后端 `list_cutting_orders` 没有 `q`：多传一个被忽略的键 = 界面假装筛了
    expect(Object.keys(query)).not.toContain('q')
  })

  it('TC-CUT-W3 改筛选回到第 1 页（不是接着第 5 页筛出空列表）', async () => {
    const { wrapper, list } = await mountList()
    // ⚠️ 先清掉挂载时那一次调用：`mock.calls[0]` 是首屏加载（page=1），
    //    直接看它会得到「翻页没生效」的假结论。
    list.mockClear()
    const third = wrapper.find('.ant-pagination-item-3')
    expect(third.exists(), '分页器应渲染出第 3 页（total=500）').toBe(true)
    await third.trigger('click')
    await settle()
    expect(list.mock.calls[0]?.[0]?.['page']).toBe(3)
    list.mockClear()

    const reset = findButton('重置')
    expect(reset, '筛选区应有「重置」按钮').toBeDefined()
    reset?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(list.mock.calls[0]?.[0]?.['page']).toBe(1)
  })

  it('TC-CUT-W4 点「详情」跳详情路由并带上单据 id', async () => {
    const { router } = await mountList()
    const detail = findButton('详情')
    expect(detail, '操作列应有「详情」按钮').toBeDefined()
    detail?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(router.currentRoute.value.name).toBe('cutting-orders-detail')
    expect(router.currentRoute.value.params['orderId']).toBe(ROW.id)
  })

  it('TC-CUT-W5 候选拉取失败不阻断列表（用户仍能看数据与翻页）', async () => {
    vi.spyOn(stylesApi, 'searchStyleOptions').mockRejectedValue(new Error('候选接口 500'))
    vi.spyOn(baseApi.workshops, 'optionsById').mockRejectedValue(new Error('候选接口 500'))
    await mountList()
    expect(bodyText()).toContain('CT-20261005-000123')
  })
})
