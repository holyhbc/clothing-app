/**
 * 打菲单详情页的「按手列表」（T-BUND-010）。
 *
 * ## 这个组件存在的原因
 *
 * 原来详情页的按手列表是个**占位**：直接拿明细行当码渲染，页面自己都写着
 * 「此处应通过 `GET /bundles?doc_id={orderId}` 获取完整码列表」。两者不是一回事 ——
 * 一行**尺码**（XL 打 2 手 = 1 行）≠ 一**手**（2 行），而且码要**审核后**才生成。
 * 于是「审核后码数 = 手数」（B21）这条不变量在界面上根本验不了。
 *
 * 所以下面每条断言都盯着「**一行 = 一手**」和「码是哪来的」，
 * 而不是组件内部的实现细节。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import type { BundleListOut } from '@garment/shared'
import * as bundlingApi from '@/api/bundling'
import HandsTable from './HandsTable.vue'

const ORDER_ID = '33333333-3333-3333-3333-333333333333'

function bundle(over: Partial<BundleListOut> & { bundle_no: string }): BundleListOut {
  return {
    bundle_qty: '60',
    color_code: 'WHT',
    counted_at: null,
    counted_by_name: null,
    counted_qty: '0',
    doc_id: ORDER_ID,
    hands: 1,
    hands_total_of_size: 2,
    line_id: 'line-1',
    operation_no: '01',
    qr_content: over.bundle_no,
    size_code: 'XL',
    status: 'ACTIVE',
    style_no: 'HB-2026-0001',
    ...over,
  } as BundleListOut
}

function mockBundles(items: BundleListOut[]): void {
  vi.spyOn(bundlingApi, 'listBundlesByOrder').mockResolvedValue({ items, total: items.length })
}

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

function rowCount(wrapper: { findAll: (s: string) => unknown[] }): number {
  return wrapper.findAll('.ant-table-tbody tr.ant-table-row').length
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('打菲单按手列表（bundling/HandsTable）', () => {
  it('一行就是一「手」，不是一「尺码」', async () => {
    // XL 打 2 手 ⇒ 2 个码（即便它们同属一条明细行）
    mockBundles([
      bundle({ bundle_no: 'BD-1-XL01-0001', hands: 1 }),
      bundle({ bundle_no: 'BD-1-XL01-0002', hands: 2 }),
    ])
    const wrapper = mount(HandsTable, { props: { orderId: ORDER_ID, reloadKey: 4 } })
    await settle()

    expect(rowCount(wrapper)).toBe(2)
    expect(bundlingApi.listBundlesByOrder).toHaveBeenCalledWith(ORDER_ID)
  })

  it('手号文案是「第 N 手 / 共 M 手」（与标签同一口径）', async () => {
    mockBundles([bundle({ bundle_no: 'BD-1-XL01-0001', hands: 1, hands_total_of_size: 2 })])
    const wrapper = mount(HandsTable, { props: { orderId: ORDER_ID, reloadKey: 4 } })
    await settle()

    expect(wrapper.text()).toContain('第 1 手 / 共 2 手')
  })

  /**
   * 审核是在**当前页面**点按钮完成的，码这时才出现。
   *
   * ⚠️ 若组件只在 `onMounted` 拉一次，页面会一直停在「审核后才有码」的空态，
   *   直到用户手动刷新 —— 而这恰恰是 E2E 里最容易骗过自己的一段
   *   （「审核完了啊」，可界面上一行码都没有）。所以 `reloadKey`（单据 version）
   *   变化必须触发重拉。
   */
  it('reloadKey 变化（= 单据状态迁移）后重新拉，审核完的码会出现', async () => {
    mockBundles([])
    const wrapper = mount(HandsTable, { props: { orderId: ORDER_ID, reloadKey: 2 } })
    await settle()
    expect(rowCount(wrapper)).toBe(0)
    expect(wrapper.text()).toContain('审核')

    // 审核完成：单据 version 2 → 4，码出现了
    mockBundles([bundle({ bundle_no: 'BD-1-XL01-0001', hands: 1, hands_total_of_size: 2 })])
    await wrapper.setProps({ reloadKey: 4 })
    await settle()

    expect(rowCount(wrapper)).toBe(1)
    expect(bundlingApi.listBundlesByOrder).toHaveBeenCalledTimes(2)
  })

  it('计件列看 counted_at：没扫过就是「未计件」（不能拿 counted_qty 判）', async () => {
    mockBundles([
      bundle({ bundle_no: 'BD-1-XL01-0001', counted_at: null, counted_qty: '0' }),
      bundle({
        bundle_no: 'BD-1-XL01-0002',
        hands: 2,
        counted_at: '2026-10-05T03:00:00Z',
        counted_by_name: '张三',
        counted_qty: '58',
      }),
    ])
    const wrapper = mount(HandsTable, { props: { orderId: ORDER_ID, reloadKey: 4 } })
    await settle()

    // ⚠️ 第一行 `counted_qty` 是 '0'：若拿它判「有没有计件」，
    //   「整手没生产」与「还没扫」在数字上分不开，两行都会显示成未计件。
    expect(wrapper.text()).toContain('未计件')
    expect(wrapper.text()).toContain('张三 · 58 件')
  })

  it('拉取失败时给出可见提示，且不留上一轮的旧码', async () => {
    mockBundles([bundle({ bundle_no: 'BD-1-XL01-0001' })])
    const wrapper = mount(HandsTable, { props: { orderId: ORDER_ID, reloadKey: 4 } })
    await settle()
    expect(rowCount(wrapper)).toBe(1)

    vi.spyOn(bundlingApi, 'listBundlesByOrder').mockRejectedValue(new Error('500 服务端错误'))
    await wrapper.setProps({ reloadKey: 5 })
    await settle()

    expect(wrapper.text()).toContain('500 服务端错误')
    // ⚠️ 失败后**清空**：留着旧码会让人以为那是本次拉到的真数据
    expect(rowCount(wrapper)).toBe(0)
  })

  it('作废的码也列出来（码不可恢复，少了几手要看得见）', async () => {
    mockBundles([
      bundle({ bundle_no: 'BD-1-XL01-0001', hands: 1, status: 'ACTIVE' }),
      bundle({ bundle_no: 'BD-1-XL01-0002', hands: 2, status: 'VOIDED' }),
    ])
    const wrapper = mount(HandsTable, { props: { orderId: ORDER_ID, reloadKey: 4 } })
    await settle()

    expect(rowCount(wrapper)).toBe(2)
    expect(wrapper.findAll('.row-voided').length).toBe(1)
  })
})