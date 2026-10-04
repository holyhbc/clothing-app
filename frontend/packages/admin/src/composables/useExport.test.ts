import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@garment/shared'
import { buildExportFilename, triggerBlobDownload, useExport } from '@/composables/useExport'
import { baseApi } from '@/api/base'

/**
 * 导出（docs/05 §9.1）。
 *
 * ⚠️ jsdom 里 **没有** `URL.createObjectURL` / `revokeObjectURL`，必须先 stub ——
 * 不 stub 的话第一条用例就会抛 `TypeError: URL.createObjectURL is not a function`，
 * 而症状看起来像"导出功能坏了"，与真因（测试环境缺 API）毫无关系。
 */
const createObjectURL = vi.fn(() => 'blob:mock')
const revokeObjectURL = vi.fn()

describe('导出文件名（docs/05 §9.1 的 `{资源}_{筛选摘要}_{时间}.xlsx`）', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    createObjectURL.mockClear()
    revokeObjectURL.mockClear()
  })

  it('无筛选 → 摘要是「全部」', () => {
    const name = buildExportFilename('颜色', { page: 3, size: 20 })
    expect(name).toMatch(/^颜色_全部_\d{8}-\d{4}\.xlsx$/)
  })

  it('⚠️ 摘要里不含 page / size（否则同一筛选导出两次得到两个文件名）', () => {
    const a = buildExportFilename('颜色', { q: '藏青', page: 1, size: 20 })
    const b = buildExportFilename('颜色', { q: '藏青', page: 7, size: 50 })
    expect(a).toBe(b)
    expect(a).toContain('颜色_藏青_')
  })

  it('枚举筛选用中文名而不是 MENS', () => {
    const name = buildExportFilename('尺码', { size_class: 'WOMENS' }, (field, value) =>
      field === 'size_class' ? '女装' : String(value),
    )
    expect(name).toContain('尺码_女装_')
  })

  it('时间戳用的是业务时区（Asia/Shanghai），不是浏览器所在时区', () => {
    // 2026-10-04T20:00Z 在上海是 10-05 04:00 —— 用 UTC 会写成 10-04
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-04T20:00:00Z'))
    const name = buildExportFilename('颜色', {})
    vi.useRealTimers()
    expect(name).toContain('_20261005-0400.xlsx')
  })

  it('触发下载后**必须 revoke**（不 revoke 的话 blob 一直挂在页面上）', () => {
    triggerBlobDownload(new Blob(['x']), '颜色.xlsx')
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock')
    // 临时 <a> 不留在 DOM 里
    expect(document.querySelectorAll('a[download]')).toHaveLength(0)
  })
})

describe('useExport', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
  })

  // ⚠️ 实现签名故意放宽成 `(...args: unknown[])`：直接照抄 `exportXlsx` 的签名会让
  //    每条用例的实现都写一遍无意义的参数类型，而断言全在实现内部。
  function stubExport(impl: (...args: never[]) => Promise<unknown>) {
    return vi.spyOn(baseApi.colors, 'exportXlsx').mockImplementation(impl as never)
  }

  it('成功时回显**实际导出行数**（导出行数与列表对不上时，这是唯一的现场证据）', async () => {
    const exportSpy = stubExport(async () => ({
      blob: new Blob(['x']),
      filename: 'colors-20261004-120000.xlsx',
      rowCount: 16,
      requestId: 'req-1',
    }))
    const messages: string[] = []
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { run, exporting } = useExport({
      resourceLabel: '颜色',
      api: baseApi.colors,
      currentQuery: () => ({ q: '藏青', page: 1, size: 20 }),
    })
    // antd 的 message 在 jsdom 里渲染到 body，断言副作用太脆；这里只关心返回值
    const rows = await run()
    expect(rows).toBe(16)
    expect(exportSpy).toHaveBeenCalledTimes(1)
    expect(exporting).toBe(false)
    expect(messages).toEqual([])
  })

  it('⚠️ 筛选条件是**点击时**求值，不是创建时快照（否则翻页后导出的还是旧范围）', async () => {
    let page = 1
    const exportSpy = stubExport((async (query: { page?: number }) => {
      expect(query.page).toBe(page)
      return { blob: new Blob(), filename: null, rowCount: 0, requestId: null }
    }) as never)
    const { run } = useExport({
      resourceLabel: '颜色',
      api: baseApi.colors,
      currentQuery: () => ({ page, size: 20 }),
    })
    await run()
    page = 3
    await run()
    expect(exportSpy).toHaveBeenCalledTimes(2)
  })

  it('失败时返回 null 且不再触发第二次（导出失败不该刷屏）', async () => {
    stubExport(async () => {
      throw new ApiError(11011, '导出结果 200000 行超过上限，请缩小筛选范围')
    })
    const { run } = useExport({
      resourceLabel: '颜色',
      api: baseApi.colors,
      currentQuery: () => ({}),
    })
    await expect(run()).resolves.toBeNull()
  })
})
