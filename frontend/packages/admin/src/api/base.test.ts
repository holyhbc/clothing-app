import { describe, expect, it, vi } from 'vitest'
import { BASE_DICT_CONTRACT } from '@garment/shared'
import type { BaseDictRow } from '@garment/shared'
import {
  REGISTRY_KEYS,
  RESOURCE_REGISTRY,
  baseApi,
  compactQuery,
  contractOf,
  editableFieldNames,
  requiredFields,
  validateDecl,
} from '@/api/base'
import type { BaseListQuery, BaseResourceDecl, RegistryKey } from '@/api/base'
import { http } from '@/api/http'

/**
 * 基础资料注册表与请求封装（T-WEB-005 缺口②③的守卫）。
 *
 * ⚠️ 这一组用例的价值全在**「注册表不许自己长出真相」**上：
 *    必填清单、路径参数列、写权限、真删还是软删，全部读后端生成物；
 *    前端只补中文名与控件。所以这里既断言"九个资源都对得上"，
 *    也断言「校验器真的能抓出问题」（用一份故意写坏的声明反验）。
 */

/** 造一行最小可用的 `DictOut + ref_count`。 */
function row(overrides: Partial<BaseDictRow> = {}): BaseDictRow {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    version: 1,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    is_active: true,
    ref_count: 0,
    ...overrides,
  } as BaseDictRow
}

describe('注册表与后端契约一致（缺口②③）', () => {
  it('九个资源的声明都能通过契约校验', () => {
    for (const key of REGISTRY_KEYS) {
      expect(() => validateDecl(RESOURCE_REGISTRY[key]), `${key} 声明与契约不一致`).not.toThrow()
    }
  })

  it('注册表的 key 集合恰好是任务卡固定的那九个', () => {
    // ⚠️ 显式列出九个（而不是只断言长度）：多一个资源（比如把 customers 顺手加进来）
    //    会让这个数组变长，而任务卡的范围是固定的九个 —— 该讨论的是开新卡，不是悄悄加。
    expect([...REGISTRY_KEYS].sort()).toEqual([
      'colors',
      'operations',
      'product-categories',
      'size-groups',
      'sizes',
      'uom-units',
      'warehouses',
      'workshop-groups',
      'workshops',
    ])
  })

  it('baseApi 与注册表一一对应（少一个就是那个资源静默 404）', () => {
    expect(Object.keys(baseApi).sort()).toEqual([...REGISTRY_KEYS].sort())
  })

  it('必填清单直接来自后端契约，前端没有第二份', () => {
    // 颜色：色码 + 色名（modules/01 §3.6.2）
    expect(requiredFields('colors')).toEqual(['color_code', 'name'])
    // 工序：工序号 + 工序名（§3.4）
    expect(requiredFields('operations')).toEqual(['operation_no', 'name'])
    // 码表：名字本身是唯一键（04 §7.4 取消了 group_code）
    expect(requiredFields('size-groups')).toEqual(['name', 'size_class', 'items'])
    expect(requiredFields('colors')).toBe(BASE_DICT_CONTRACT.colors.create.required)
  })

  it('校验器能抓出「表单漏了必填字段」', () => {
    const broken: BaseResourceDecl = {
      ...RESOURCE_REGISTRY.colors,
      formFields: RESOURCE_REGISTRY.colors.formFields.filter((f) => f.name !== 'name'),
    }
    expect(() => validateDecl(broken)).toThrow(/name/)
  })

  it('校验器能抓出「拼错的筛选项」（否则后端回 10001，用户不知道为什么）', () => {
    const broken: BaseResourceDecl = {
      ...RESOURCE_REGISTRY.colors,
      filters: [{ name: 'colorName', label: '色名', control: 'text' }],
    }
    expect(() => validateDecl(broken)).toThrow(/不在后端可筛选白名单/)
  })

  it('校验器能抓出「combo 筛选项没有候选来源」', () => {
    const broken: BaseResourceDecl = {
      ...RESOURCE_REGISTRY.operations,
      filters: [{ name: 'workshop_id', label: '所属车间', control: 'combo' }],
    }
    expect(() => validateDecl(broken)).toThrow(/没有候选来源资源/)
  })

  it('内置角标与「恢复内置库」按钮由 hasBuiltinFlag 决定，不在前端声明', () => {
    // 字典三表 → 显示内置角标；车间 / 仓库 → 不显示
    expect(['colors', 'sizes', 'size-groups'].map((k) => contractOf(k as RegistryKey).hasBuiltinFlag)).toEqual([
      true,
      true,
      true,
    ])
    expect(contractOf('workshops').hasBuiltinFlag).toBe(false)
  })

  it('编码列取自后端注册表（码表没有编码列，`name` 兼作路径参数）', () => {
    expect(contractOf('colors').codeColumn).toBe('color_code')
    expect(contractOf('operations').codeColumn).toBe('operation_no')
    expect(contractOf('size-groups').codeColumn).toBe('name')
    expect(baseApi.colors.codeColumn).toBe('color_code')
  })

  it('编辑态可改字段取自 patch 模型：组别的所属车间建后不可改', () => {
    // ⚠️ `WorkshopGroupPatch` 只有 `name` / `remark` —— 组别不能换车间。
    //    前端若按"除了编码都能改"渲染，编辑时能改车间，提交必被 `extra=forbid`
    //    的后端拒（10001），而界面上看不出任何异常。
    const editable = editableFieldNames('workshop-groups')
    expect([...editable].sort()).toEqual(['name', 'remark'])
    expect(RESOURCE_REGISTRY['workshop-groups'].formFields.map((f) => f.name)).toContain(
      'workshop_id',
    )

    // 对照：颜色的四个字段都能改（编码除外，编码在 create 里但不在 patch 里）
    expect([...editableFieldNames('colors')].sort()).toEqual([
      'color_family',
      'name',
      'pantone_code',
      'remark',
    ])
  })

  it('写权限点分类 / 工序与默认 base:* 不同（按钮显隐靠它）', () => {
    expect(baseApi['product-categories'].permissions.create).toBe('base:category:manage')
    expect(baseApi.operations.permissions.create).toBe('base:operation:manage')
    expect(baseApi.colors.permissions.create).toBe('base:create')
  })
})

describe('请求封装打到正确的路径', () => {
  it('列表：GET /{key}，并把筛选原样传过去', async () => {
    const get = vi.spyOn(http, 'get').mockResolvedValue({ items: [], total: 0 })
    await baseApi.colors.list({ q: '藏青', is_active: true, page: 2, size: 20 })

    expect(get).toHaveBeenCalledWith('/colors', {
      query: { q: '藏青', is_active: true, page: 2, size: 20 },
    })
  })

  it('⚠️ /options 与 /exports 排在 /{code} 之前（后端注册顺序，反了会 404）', async () => {
    const get = vi.spyOn(http, 'get').mockResolvedValue([])
    const download = vi.spyOn(http, 'download').mockResolvedValue({
      blob: new Blob(),
      filename: 'colors-20261004-120000.xlsx',
      rowCount: 3,
      requestId: 'req-1',
    })

    await baseApi.colors.options('藏')
    await baseApi.colors.exportXlsx({ q: '藏青' })

    expect(get.mock.calls[0]?.[0]).toBe('/colors/options')
    expect(download.mock.calls[0]?.[0]).toBe('/colors/exports')
  })

  it('路径参数是**业务编码**且要转义（`/` 不能把路径截断）', async () => {
    const get = vi.spyOn(http, 'get').mockResolvedValue(row())
    await baseApi.sizes.get('XL/2')
    expect(get).toHaveBeenCalledWith('/sizes/XL%2F2')
  })

  it('停用走 POST /{key}/{code}/disables 且带原因', async () => {
    const post = vi.spyOn(http, 'post').mockResolvedValue({})
    await baseApi.colors.disable('NVY', '该色已停用')
    expect(post).toHaveBeenCalledWith('/colors/NVY/disables', { reason: '该色已停用' })
  })

  it('optionsById 的 value 是主键 id（workshop_id / size_id 要的是 UUID）', async () => {
    vi.spyOn(http, 'get').mockResolvedValue({
      items: [row({ id: 'aaaa', code: 'C01', name: '一号车间' })],
      total: 1,
    })
    const options = await baseApi.workshops.optionsById('一号')

    expect(options).toEqual([{ value: 'aaaa', label: 'C01 一号车间', sub: null, disabled: false }])
  })

  it('optionsById 把停用项标成不可选（docs/05 §9.5.2）', async () => {
    vi.spyOn(http, 'get').mockResolvedValue({
      items: [row({ id: 'bbbb', color_code: 'BLK', name: '黑', is_active: false })],
      total: 1,
    })
    const options = await baseApi.colors.optionsById()
    expect(options[0]?.disabled).toBe(true)
  })
})

describe('compactQuery', () => {
  it('剔掉 undefined 与空串（"清空筛选"就是设成 undefined）', () => {
    // ⚠️ 走 `unknown`：查询条件在 `exactOptionalPropertyTypes` 下不接受显式
    // `undefined`，而"清空筛选"在运行时恰恰就是把它设成 undefined
    const query = { q: undefined, color_family: '', page: 1, size: 20 } as unknown as BaseListQuery
    expect(compactQuery(query)).toEqual({ page: 1, size: 20 })
  })
})
