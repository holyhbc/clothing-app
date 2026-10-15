import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { PERM } from '@garment/shared'
import type { PieceworkLogRowOut } from '@garment/shared'
import * as pieceworkApi from '@/api/piecework'
import { baseApi } from '@/api/base'
import * as cuttingApi from '@/api/cutting'
import { permission } from '@/directives/permission'
import { useAuthStore } from '@/stores/auth'
import PieceworkLogList from '@/views/piecework/List.vue'

/**
 * 计件流水台账页（T-PIECE-005，TC-PW-L01 ~ TC-PW-L05）。
 *
 * 打桩 `api/piecework` 而不是 `fetch`：验「界面选了筛选之后**调哪个函数、
 * 传了什么参数**」，打桩 fetch 会把用例绑死在 URL 与 query 拼接上。
 */
const mountedWrappers: { unmount: () => void }[] = []

function track<T extends { unmount: () => void }>(wrapper: T): T {
  mountedWrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  while (mountedWrappers.length > 0) mountedWrappers.pop()?.unmount()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

const ROW: PieceworkLogRowOut = {
  log_id: '11111111-1111-1111-1111-111111111111',
  bundle_no: 'BD-20261013-000001-01-0001',
  style_no: 'HB-2026-0001',
  operation_no: '03',
  operation_name: '拼前',
  color_name: '藏青',
  size_code: 'M',
  size_name: '中',
  employee_id: '22222222-2222-2222-2222-222222222222',
  employee_no: 'A001',
  employee_name: '陈美玲',
  group_no: 'G1',
  workshop_name: '一号车间',
  work_date: '2026-10-13',
  hands: 2,
  hands_total_of_size: 6,
  bundle_qty: '60.000',
  qty: '28.000',
  unit_price: '1.200000',
  amount: '33.6000',
  log_type: 'SCAN',
  payroll_id: null,
  is_settled: false,
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
      { path: '/piecework/logs', name: 'piecework-logs', component: { template: '<div />' } },
    ],
  })
}

async function mountList(rows: PieceworkLogRowOut[] = [ROW], total = 1) {
  const list = vi.spyOn(pieceworkApi, 'listPieceworkLogs').mockResolvedValue({ items: rows, total })
  vi.spyOn(baseApi.workshops, 'optionsById').mockResolvedValue([
    { value: '44444444-4444-4444-4444-444444444444', label: '一号车间', disabled: false },
  ])
  vi.spyOn(cuttingApi, 'searchStyleOptionsById').mockResolvedValue([
    { value: 'HB-2026-0001', label: 'HB-2026-0001', disabled: false },
  ])
  vi.spyOn(baseApi.operations, 'optionsById').mockResolvedValue([
    { value: '03', label: '拼前', disabled: false },
  ])
  const router = stubRouter()
  await router.push('/piecework/logs')
  await router.isReady()
  const wrapper = track(
    mount(PieceworkLogList, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  return { wrapper, router, list }
}

function bodyText(): string {
  return plain(document.querySelector('.ant-table-tbody')?.textContent ?? '')
}

function plain(text: string): string {
  return text.replace(/\s/g, '')
}

function findButton(label: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find((node) =>
    plain(node.textContent ?? '').includes(label),
  )
}

/** 按 placeholder 文案找下拉（页面上有多个 Select）。 */
function selectByPlaceholder(placeholder: string): Element | undefined {
  return [...document.querySelectorAll('.ant-select')].find((node) =>
    (node.textContent ?? '').includes(placeholder),
  )
}

async function pickOption(placeholder: string, optionText: string): Promise<void> {
  const select = selectByPlaceholder(placeholder)
  expect(select, `应有占位为「${placeholder}」的下拉`).toBeDefined()
  const selector = select?.querySelector('.ant-select-selector')
  selector?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
  selector?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()
  const option = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
    (node.textContent ?? '').includes(optionText),
  )
  expect(option, `下拉里应有「${optionText}」`).toBeDefined()
  option?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()
}

describe('计件流水台账（TC-PW-L01 ~ TC-PW-L05）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.PIECEWORK_READ, PERM.PIECEWORK_EXPORT])
  })

  it('TC-PW-L01 行按后端口径渲染：员工、款号、打菲号、第几手、件数、金额、类型、结算', async () => {
    await mountList()
    const text = bodyText()
    expect(text).toContain('A001')
    expect(text).toContain('陈美玲')
    expect(text).toContain('HB-2026-0001')
    expect(text).toContain('BD-20261013-000001-01-0001')
    expect(text).toContain('第2/6手')
    expect(text).toContain('28')
    // 类型走本地标签映射（不是 StatusTag）：SCAN → 扫码
    expect(text).toContain('扫码')
    expect(text).toContain('未结算')
  })

  it('TC-PW-L02 筛选是**等值**传参，没有后端忽略的 `q` 参数', async () => {
    const { list } = await mountList()
    list.mockClear()

    await pickOption('按类型', '红冲')

    expect(list).toHaveBeenCalled()
    const query = list.mock.calls[0]?.[0] ?? {}
    expect(query['log_type']).toBe('REVERSAL')
    // ⚠️ 后端 `_query` 没有 `q`：多传一个被忽略的键 = 界面假装筛了
    expect(Object.keys(query)).not.toContain('q')
  })

  it('TC-PW-L03 改筛选回到第 1 页（不是接着第 5 页筛出空列表）', async () => {
    const { wrapper, list } = await mountList([ROW], 500)
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

  it('TC-PW-L04 导出把当前筛选原样传给后端并触发下载（05 §9.1）', async () => {
    const createObjectURL = vi.fn(() => 'blob:mock')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })

    const exportSpy = vi
      .spyOn(pieceworkApi, 'exportPieceworkLogs')
      .mockResolvedValue({ blob: new Blob(['x']), filename: null, rowCount: 3, requestId: null })

    await mountList()

    // 先选一个筛选，验证导出带着它走（与列表同一套筛选）
    await pickOption('按类型', '扫码')
    exportSpy.mockClear()

    const button = findButton('导出')
    expect(button, '工具区应有「导出 CSV」按钮').toBeDefined()
    button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(exportSpy).toHaveBeenCalledTimes(1)
    const query = exportSpy.mock.calls[0]?.[0] ?? {}
    expect(query['log_type']).toBe('SCAN')
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledTimes(1)
  })

  it('TC-PW-L05 空列表显示「还没有计件流水」的下一步动作', async () => {
    await mountList([], 0)
    expect(document.body.textContent ?? '').toContain('还没有计件流水')
    expect(findButton('清空筛选'), '空态应有「清空筛选」').toBeDefined()
  })
})
