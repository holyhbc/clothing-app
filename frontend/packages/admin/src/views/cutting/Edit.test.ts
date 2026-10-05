import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { PERM } from '@garment/shared'
import type { CuttingOrderOut } from '@garment/shared'
import * as cuttingApi from '@/api/cutting'
import { permission } from '@/directives/permission'
import { resetHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import CuttingOrderEdit from '@/views/cutting/Edit.vue'

/**
 * 裁剪单编辑页（T-CUT-001c-3c，TC-CUT-X1 ~ TC-CUT-X5）。
 *
 * ## 这一组用例守的是「版本链」与「只读态」，不是「能不能改字段」
 *
 * 三层字段的编辑能力由 T-CUT-001c-3a 的组件测试守着；这一页独有、也最容易错的是
 * **两个写接口之间的 `version` 传递**：
 *
 * - `PATCH`（表头）与 `PUT /lines`（明细）**各自 bump version**，所以第二次写必须
 *   带第一次的返回值；
 * - `PUT /lines` 是**软删旧行 + 插新行** → **行 id 全变** → 保存成功后必须用响应
 *   重建本地树，否则下一次保存会收到「第 xxx 行不属于这张裁剪单」。
 *
 * ⚠️ 这两条的共同症状是「用户每点一次保存就失败一次」，而报错（10003 / 10001）
 * 完全看不出是页面自己的版本管理错了。
 */

const mountedWrappers: { unmount: () => void }[] = []

function track<T extends { unmount: () => void }>(wrapper: T): T {
  mountedWrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  while (mountedWrappers.length > 0) mountedWrappers.pop()?.unmount()
  document.body.innerHTML = ''
})

const ORDER_ID = '33333333-3333-3333-3333-333333333333'

/** 一张草稿单：version=3，两行（第二行没有颜色，模拟「录到一半」）。 */
const DRAFT: CuttingOrderOut = {
  id: ORDER_ID,
  doc_no: 'CT-20261005-000123',
  workshop_id: '44444444-4444-4444-4444-444444444444',
  style_id: '55555555-5555-5555-5555-555555555555',
  style_no: 'HB-2026-0001',
  color_codes: 'NVY',
  doc_date: '2026-10-05',
  delivery_date: null,
  ply_count: 1,
  entry_mode_default: 'MANUAL',
  fabric_qty: '40.000',
  output_qty: '30',
  cut_waste_qty: '0.000',
  balance_qty: '0',
  hands_total: 10,
  status: 'DRAFT',
  remark_source: null,
  approved_by: null,
  approved_at: null,
  rejected_reason: null,
  cancelled_reason: null,
  version: 3,
  remark: null,
  created_at: '2026-10-05T02:10:00Z',
  updated_at: '2026-10-05T02:10:00Z',
  lines: [
    {
      id: 'l1',
      line_no: 1,
      stock_id: '99999999-9999-9999-9999-999999999999',
      supplier_id: null,
      material_id: '88888888-8888-8888-8888-888888888888',
      style_no: 'HB-2026-0001',
      dye_lot_no: 'H2408',
      bolt_no: '01',
      color_plan: null,
      width_cm: '152.00',
      fabric_qty: '40.000',
      fabric_weight_kg: null,
      waste_qty: '0.000',
      output_qty: '30',
      size_line_sum_qty: '30',
      balance_qty: '0',
      colors: [
        {
          id: 'c1',
          line_id: 'l1',
          color_code: 'NVY',
          entry_mode: 'MANUAL',
          qty_per_hand: null,
          uniform_qty: null,
          ratio_snapshot: null,
          hands_total: '10',
          output_qty_total: '30',
          balance_qty_total: '0',
          entry_mode_changed_at: null,
          entry_mode_changed_by: null,
          size_lines: [
            {
              id: 's1',
              size_line_no: 1,
              size_code: 'L',
              hands: 10,
              qty_per_hand: 3,
              output_qty: '30',
              output_qty_manual: false,
              balance_qty: '0',
              hands_seq: null,
              remark: null,
            },
          ],
        },
      ],
    },
    {
      id: 'l2',
      line_no: 2,
      stock_id: '',
      supplier_id: null,
      material_id: '88888888-8888-8888-8888-888888888888',
      style_no: 'HB-2026-0001',
      dye_lot_no: '-',
      bolt_no: '-',
      color_plan: null,
      width_cm: null,
      fabric_qty: '0.000',
      fabric_weight_kg: null,
      waste_qty: '0.000',
      output_qty: '0',
      size_line_sum_qty: '0',
      balance_qty: '0',
      colors: [],
    },
  ],
}

/**
 * 保存成功后的响应：version 递增，且**行 id 全变**（模拟 `PUT /lines` 的全量替换）。
 *
 * ⚠️ **必须以「加载时那份」为基线**而不是 `DRAFT` 常量 —— 否则「两行都录完」那条
 *    用例在保存后会拿到「第二行是空的」响应，于是第二次保存又被挡住，
 *    症状是「连点两次保存，第二次没反应」这种完全不像版本问题的失败。
 */
function afterWrite(version: number, base: CuttingOrderOut = DRAFT): CuttingOrderOut {
  return {
    ...base,
    version,
    lines: (base.lines ?? []).map((line, index) => ({
      ...line,
      id: `new-l${index + 1}`,
      // ⚠️ `colors` 在出参类型里是可选的 —— 这里必须 `?? []`，而**不能**靠断言：
      //    断言会让「出参少给了一个数组」这种真实错误变成运行时的 `.map of undefined`。
      colors: (line.colors ?? []).map((color) => ({ ...color, id: `new-c${color.id}` })),
    })),
  }
}

/** 两行都录完的夹具（用于验保存链路 —— 第二行没录完时保存按钮本来就该禁用）。 */
function completeDraft(): CuttingOrderOut {
  const base = structuredClone(DRAFT)
  const second = base.lines?.[1]
  if (second !== undefined) {
    second.stock_id = 'aaaa1111-1111-1111-1111-111111111111'
    second.fabric_qty = '20.000'
    second.output_qty = '15'
    second.colors = [
      {
        id: 'c2',
        line_id: 'l2',
        color_code: 'BLK',
        entry_mode: 'MANUAL',
        qty_per_hand: null,
        uniform_qty: null,
        ratio_snapshot: null,
        hands_total: '5',
        output_qty_total: '15',
        balance_qty_total: '0',
        entry_mode_changed_at: null,
        entry_mode_changed_by: null,
        size_lines: [
          {
            id: 's2',
            size_line_no: 1,
            size_code: 'XL',
            hands: 5,
            qty_per_hand: 3,
            output_qty: '15',
            output_qty_manual: false,
            balance_qty: '0',
            hands_seq: null,
            remark: null,
          },
        ],
      },
    ]
  }
  return base
}

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

function plain(text: string): string {
  return text.replace(/\s/g, '')
}

function text(): string {
  return plain(document.body.textContent ?? '')
}

function button(label: string): HTMLButtonElement | undefined {
  const all = [...document.querySelectorAll<HTMLButtonElement>('button')].filter((node) =>
    plain(node.textContent ?? '').includes(label),
  )
  return all[all.length - 1]
}

/**
 * @param detail 加载返回的详情
 * @param loadError 非空时让加载**失败**（⚠️ 必须走这个开关而不是在调用之后再
 *   `mockRejectedValue` —— `mountEdit` 内部自己 `spyOn` 过 `getCuttingOrder`，
 *   外面那次会被覆盖，而失败信息只会显示「页面地址不对」这种无关内容）。
 */
async function mountEdit(detail: CuttingOrderOut = DRAFT, loadError = '') {
  const get = loadError === ''
    ? vi.spyOn(cuttingApi, 'getCuttingOrder').mockResolvedValue(detail)
    : vi.spyOn(cuttingApi, 'getCuttingOrder').mockRejectedValue(new Error(loadError))
  // ⚠️ 两个桩都**按传入的 version 递增**（`version + 1`），不是固定返回 4 / 5：
  //   固定值的桩会让「第二次保存」拿到比第一次小的版本，于是页面行为正确而用例
  //   断言失败 —— 而失败信息（expected 4 to be 5）完全看不出是桩太笨。
  const patch = vi
    .spyOn(cuttingApi, 'patchCuttingOrder')
    .mockImplementation(async (_id, payload) => afterWrite(payload.version + 1, detail))
  const put = vi
    .spyOn(cuttingApi, 'putCuttingOrderLines')
    .mockImplementation(async (_id, payload) => afterWrite(payload.version + 1, detail))
  vi.spyOn(cuttingApi, 'searchStockBatchOptions').mockResolvedValue([])
  vi.spyOn(cuttingApi, 'deleteCuttingOrder').mockResolvedValue({ deleted: ORDER_ID })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/cutting/orders', name: 'cutting-orders', component: { template: '<div />' } },
      {
        path: '/cutting/orders/:orderId',
        name: 'cutting-orders-detail',
        component: { template: '<div />' },
      },
      // ⚠️ **必须有这一条**：测试推的是 `/cutting/orders/{id}/edit`，而桩路由少了
      //    `/edit` 段时会匹配不到 → `route.params.orderId` 是 undefined → 页面报
      //    「缺少裁剪单 ID（页面地址不对）」。而失败信息完全看不出是「桩路由少一段」。
      {
        path: '/cutting/orders/:orderId/edit',
        name: 'cutting-orders-edit',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push(`/cutting/orders/${ORDER_ID}/edit`)
  await router.isReady()
  const wrapper = track(
    mount(CuttingOrderEdit, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  return { wrapper, get, patch, put, router }
}

describe('裁剪单编辑页（TC-CUT-001c-3c）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    useAuthStore().permissions = new Set([
      PERM.CUTTING_READ,
      PERM.CUTTING_UPDATE,
      PERM.CUTTING_CREATE,
    ])
  })

  it('TC-CUT-X1 加载后三层**照搬后端**，第二行「没录完」进问题清单并挡住保存', async () => {
    const { patch, put } = await mountEdit()
    expect(text()).toContain('第2行')
    expect(text()).toContain('还没选布批')
    expect(button('保存')?.disabled, '没录完的行不该允许保存').toBe(true)
    expect(patch).not.toHaveBeenCalled()
    expect(put).not.toHaveBeenCalled()
  })

  it('TC-CUT-X1b 录完的行**不该**出现在问题清单里（否则正常数据被误判）', async () => {
    await mountEdit(completeDraft())
    expect(text()).not.toContain('还没选布批')
    expect(text()).not.toContain('至少要有一个颜色')
  })

  it('TC-CUT-X2 ★ 版本链：PATCH 用旧 version，PUT 用 PATCH 返回的新 version', async () => {
    const { patch, put } = await mountEdit(completeDraft())
    expect(button('保存')?.disabled, '两行都录完时保存按钮应可点').toBe(false)

    button('保存')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 第一步：PATCH 带着**加载时的** version=3
    expect(patch).toHaveBeenCalledTimes(1)
    expect(patch.mock.calls[0]?.[1]?.version).toBe(3)
    // 第二步：PUT /lines 必须带 PATCH 返回的 version=4 —— 用页面上的旧值就是 10003
    expect(put).toHaveBeenCalledTimes(1)
    expect(put.mock.calls[0]?.[1]?.version).toBe(4)
    // 保存成功后界面上的版本号跟着走（PUT 的响应）
    expect(text()).toContain('第5版')
  })

  it('TC-CUT-X3 ★ 保存成功后用响应里的新 version 继续（连续两次保存都成功）', async () => {
    const { patch, put } = await mountEdit(completeDraft())

    button('保存')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    // 保存后界面上的版本号跟着 PUT 的响应走
    expect(text()).toContain('第5版')

    // ⚠️ 再保存一次：PATCH 应带 5（PUT 响应的新版本），而不是 3 或 4。
    //    这是「用户每点一次保存就失败一次」那个 bug 的最小复现。
    //    ⚠️ 两次点击之间必须**确保第一次已结束**：否则第二次会撞上防重入守卫被丢掉
    //    （而那条守卫本身是 TC-CUT-X3b 要单独验的东西，不能混在这条里）。
    await settle()
    expect(button('保存')?.disabled).toBe(false)
    button('保存')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    // 第二次保存：PATCH 带 5（上次 PUT 的返回值），PUT 带 6（本次 PATCH 的返回值）
    expect(patch.mock.calls[1]?.[1]?.version).toBe(5)
    expect(put.mock.calls[1]?.[1]?.version).toBe(6)
    // ⚠️ 版本号是 7 不是 6：每次保存要 bump **两次**（PATCH 一次、PUT 一次）。
    //   写 6 的那个版本是我第一次算错时的期望值 —— 而页面显示 7 才是对的。
    expect(text()).toContain('第7版')
  })

  it('TC-CUT-X3b ★ 连点两次保存**只发一次请求**（防重入，docs/06 §2.4）', async () => {
    const { patch, put } = await mountEdit(completeDraft())
    const save = button('保存')
    save?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    // ⚠️ **不 settle**：两次点击连着发，第二次会在第一次还在飞的时候到达。
    save?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    expect(patch, '并发两次保存 → 两个请求带同一个 version → 后到的必然 10003').toHaveBeenCalledTimes(1)
    expect(put).toHaveBeenCalledTimes(1)
  })

  it('TC-CUT-X4 ★ 非草稿状态整块只读，且保存按钮禁用', async () => {
    await mountEdit({ ...DRAFT, status: 'APPROVED' })
    expect(text()).toContain('不可修改')
    expect(button('保存')?.disabled).toBe(true)
  })

  it('TC-CUT-X5 加载失败显示错误原文与重试，且不渲染空白表头', async () => {
    // ⚠️ `mountEdit` 内部**也会** `spyOn(getCuttingOrder)`，所以失败桩必须走它的
    //    参数（`detail` 可以是任意值，这里靠再 `mockRejectedValueOnce` 覆盖第一调）。
    const { wrapper } = await mountEdit(DRAFT, '12002 不在数据范围内')
    expect(text()).toContain('12002不在数据范围内')
    expect(button('重试'), '错误提示里应有「重试」按钮').toBeDefined()
    // ⚠️ 不该渲染一张**空表头**：空表会让用户以为「这张单没有明细」，
    //   而真相是「压根没加载出来」—— 这两种状态必须在界面上可区分。
    expect(document.querySelectorAll('.ant-table-tbody').length).toBe(0)
    expect(wrapper.exists()).toBe(true)
  })
})