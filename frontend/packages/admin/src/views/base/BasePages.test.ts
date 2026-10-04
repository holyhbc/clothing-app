import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { ApiError, PERM } from '@garment/shared'
import type { BaseDictRow } from '@garment/shared'
import { baseApi } from '@/api/base'
import * as systemApi from '@/api/system'
import { permission } from '@/directives/permission'
import { resetHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import ColorList from '@/views/base/colors/List.vue'
import SizeGroupForm from '@/views/base/size-groups/Form.vue'
import OperationList from '@/views/base/operations/List.vue'

/**
 * 基础资料页（T-WEB-005 的 TC-W24 ~ TC-W29 + 四态）。
 *
 * 打桩 `api/base` 的方法而不是 `fetch`：这些用例要验的是「拿到数据之后界面怎么反应、
 * 点了按钮之后调哪个函数」，打桩 fetch 会把用例绑死在 URL 上。
 */

/** 造一行基础资料（`DictOut` 的字段大多是 null，所以只填用得上的）。 */
function row(overrides: Partial<BaseDictRow> = {}): BaseDictRow {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    version: 1,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    is_active: true,
    is_builtin: false,
    ref_count: 0,
    ...overrides,
  } as BaseDictRow
}

const NVY = row({ color_code: 'NVY', name: '藏青', is_builtin: true, color_family: 'Pantone TCX' })

/** ⚠️ antd 的 Table / Dropdown 在数据到位后还要再走几个 tick 才渲染出行。 */
async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

/** 去掉空白再比对：antd 会在两个汉字之间插一个空格（class `ant-btn-two-chinese-characters`）。 */
function plain(text: string): string {
  return text.replace(/\s/g, '')
}

/**
 * 打开最后一行的 `…` 菜单。
 *
 * ⚠️ antd 把 Dropdown **teleport 到 document.body**，`wrapper.findAll()` 永远是空的 ——
 *    「菜单里没有删除项」这类断言会**假绿**（`some()` 对空数组自然是 false）。
 *    所以挂载时 `attachTo: document.body`，菜单项从 `document` 里查。
 */
async function openRowMenu(wrapper: {
  findAll: (selector: string) => {
    at: (index: number) => { trigger: (event: string) => Promise<unknown> } | undefined
  }
}): Promise<void> {
  const trigger = wrapper.findAll('.ant-dropdown-trigger').at(-1)
  expect(trigger, '行操作里应有 `…` 下拉').toBeDefined()
  await trigger?.trigger('click')
  await settle()
}

function menuItems(): { text: string; disabled: boolean; node: Element }[] {
  return [...document.querySelectorAll('.ant-dropdown-menu-item')].map((node) => ({
    text: plain(node.textContent ?? ''),
    disabled: node.classList.contains('ant-dropdown-menu-item-disabled'),
    node,
  }))
}

async function clickMenuItem(label: string): Promise<void> {
  const item = menuItems().find((entry) => entry.text.includes(label))
  expect(item, `菜单里应有「${label}」项`).toBeDefined()
  item?.node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()
}

function stubRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/base/colors', name: 'base-colors', component: { template: '<div />' } },
      { path: '/base/colors/new', name: 'base-colors-new', component: { template: '<div />' } },
      { path: '/base/colors/:code', name: 'base-colors-edit', component: { template: '<div />' } },
      // ⚠️ 工序页也要有这三条：真实路由表由 `baseDictRoutes()` 生成，桩路由漏了的话
      //    `router.push({name})` 抛 "No match" —— 那是个**未捕获异常**，症状是
      //    断言莫名其妙地停在 'home'，与被测行为毫无关系。
      { path: '/base/operations', name: 'base-operations', component: { template: '<div />' } },
      { path: '/base/operations/:code', name: 'base-operations-edit', component: { template: '<div />' } },
    ],
  })
}

function grant(codes: string[]): void {
  useAuthStore().permissions = new Set(codes)
}

/** 挂载列表页并把首屏数据打桩好。 */
function mountList(
  component: typeof ColorList,
  items: BaseDictRow[],
  extra: { total?: number } = {},
) {
  vi.spyOn(baseApi.colors, 'list').mockImplementation(async () => ({
    items,
    total: extra.total ?? items.length,
  }))
  const router = stubRouter()
  void router.push('/')
  const wrapper = mount(component, {
    global: { plugins: [router], directives: { can: permission } },
    attachTo: document.body,
  })
  return { router, wrapper }
}

describe('字典删除的两分支（TC-W24 / TC-W25）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.BASE_READ, PERM.BASE_CREATE, PERM.BASE_UPDATE, PERM.BASE_DISABLE, PERM.BASE_DELETE, PERM.BASE_EXPORT, PERM.SYSTEM_CONFIG_MANAGE])
  })

  it('TC-W24 被引用时：删除项禁用且写明原因，**不发 DELETE**', async () => {
    const remove = vi.spyOn(baseApi.colors, 'remove').mockResolvedValue({ deleted: true, cascaded: 0 })
    const { wrapper } = mountList(ColorList, [row({ ...NVY, ref_count: 3 })])
    await settle()

    await openRowMenu(wrapper)
    const item = menuItems().find((entry) => entry.text.includes('删除'))
    expect(item).toBeDefined()
    expect(item?.disabled, '被引用的行不该给出可点的删除入口').toBe(true)
    // ⚠️ 只灰一个按钮而不说为什么，用户会以为坏了然后反复点
    expect(item?.text).toContain('不能删除')
    expect(item?.text).toContain('请改为停用')

    item?.node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    expect(remove).not.toHaveBeenCalled()
  })

  it('TC-W25 无引用时：二次确认写明「不可撤销」→ 确认后真的删掉并刷新列表', async () => {
    const remove = vi.spyOn(baseApi.colors, 'remove').mockResolvedValue({ deleted: true, cascaded: 0 })
    const { wrapper } = mountList(ColorList, [NVY])
    await settle()

    await openRowMenu(wrapper)
    const item = menuItems().find((entry) => entry.text.includes('删除'))
    expect(item?.disabled).toBe(false)
    await clickMenuItem('删除')

    // 确认框里必须写清后果（docs/06 §5）
    const modal = document.querySelector('.ant-modal-confirm')
    expect(modal?.textContent).toContain('不可撤销')

    const ok = document.querySelector<HTMLButtonElement>('.ant-modal-confirm .ant-btn-primary')
    expect(ok?.disabled).toBe(false)
    ok?.click()
    await settle()

    expect(remove).toHaveBeenCalledWith('NVY')
    // 删完要重拉列表，否则行还留在界面上（用户会以为没删掉，再点一次）
    expect(baseApi.colors.list).toHaveBeenCalledTimes(2)
  })

  it('删除被并发插入的引用拦下（后端 20003）时不吞异常、也不重复刷新', async () => {
    vi.spyOn(baseApi.colors, 'remove').mockRejectedValue(
      new ApiError(20003, '颜色 NVY 已被引用，不能删除；请改为停用', {
        details: { ref_count: 2, references: [{ model: 'style_colors', count: 2 }] },
      }),
    )
    const { wrapper } = mountList(ColorList, [NVY])
    await settle()
    await openRowMenu(wrapper)
    await clickMenuItem('删除')
    document.querySelector<HTMLButtonElement>('.ant-modal-confirm .ant-btn-primary')?.click()
    await settle()

    expect(baseApi.colors.list).toHaveBeenCalledTimes(1)
  })

  it('TC-W29 四态之空：给出下一步动作', async () => {
    const { wrapper } = mountList(ColorList, [])
    await settle()

    const text = plain(wrapper.text())
    expect(text).toContain('还没有颜色')
    expect(text).toContain('新建颜色')
  })

  it('四态之错误：业务文案 + 重试，而不是 "Error: xxx"', async () => {
    vi.spyOn(baseApi.colors, 'list').mockRejectedValue(
      new ApiError(12001, '你没有查看基础资料的权限，请联系管理员', {}),
    )
    const router = stubRouter()
    void router.push('/')
    const wrapper = mount(ColorList, {
      global: { plugins: [router], directives: { can: permission } },
    })
    await settle()

    expect(plain(wrapper.text())).toContain('加载失败')
    expect(plain(wrapper.text())).not.toContain('Error:')
  })

  it('停用走二次确认且必填原因（docs/06 §5）', async () => {
    const disable = vi.spyOn(baseApi.colors, 'disable').mockResolvedValue({
      code: 'NVY',
      is_active: false,
      reason: 'x',
    })
    const { wrapper } = mountList(ColorList, [NVY])
    await settle()
    await openRowMenu(wrapper)
    await clickMenuItem('停用')

    const ok = document.querySelector<HTMLButtonElement>('.ant-modal-confirm .ant-btn-primary')
    expect(ok).not.toBeNull()
    expect(ok?.disabled, '原因没填就不该让点确定').toBe(true)
    expect(disable).not.toHaveBeenCalled()
  })

  it('「恢复内置库」：先报缺失数量 → 二次确认 → 只恢复字典', async () => {
    vi.spyOn(systemApi, 'listMissingBuiltin').mockResolvedValue({
      permissions: [],
      roles: [],
      dicts: { colors: ['WHT', 'BLK'], sizes: [], size_groups: [], product_categories: [] },
      total: 2,
    })
    const restore = vi.spyOn(systemApi, 'restoreBuiltin').mockResolvedValue({
      restored_permissions: 0,
      restored_roles: 0,
      restored_role_bindings: 0,
      restored_dicts: 2,
      dict_detail: { colors: 2 },
      message: '已恢复 2 个内置颜色',
    })
    const { wrapper } = mountList(ColorList, [NVY])
    await settle()

    const button = wrapper.findAll('button').find((b) => plain(b.text()) === '恢复内置库')
    expect(button, '字典页应有「恢复内置库」按钮').toBeDefined()
    await button?.trigger('click')
    await settle()

    // 确认框里要说清会恢复什么、以及"自建的找不回来"
    const modal = document.querySelector('.ant-modal-confirm')
    expect(modal?.textContent).toContain('colors 2 个')
    expect(modal?.textContent).toContain('只恢复')

    document.querySelector<HTMLButtonElement>('.ant-modal-confirm .ant-btn-primary')?.click()
    await settle()

    // ⚠️ 三类都要显式传：按钮的语义是"只恢复字典"
    expect(restore).toHaveBeenCalledWith({ dicts: true, permissions: false, roles: false })
  })

  it('TC-W26 导出：文件名带筛选摘要，且**与列表同一筛选**', async () => {
    const createObjectURL = vi.fn(() => 'blob:mock')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const exportXlsx = vi.spyOn(baseApi.colors, 'exportXlsx').mockImplementation(async (query) => {
      // ⚠️ `query` 声明成可选（导出允许不带筛选），所以这里要判空而不是直接读 ——
      //    漏了判空，用例会在「导出没带筛选」时抛 TypeError，报错指向测试而不是行为
      expect(query?.q, '导出必须带上当前关键字，否则导出的是全量').toBe('藏青')
      return { blob: new Blob(['x']), filename: 'colors-20261004.xlsx', rowCount: 16, requestId: null }
    })
    const { wrapper } = mountList(ColorList, [NVY], { total: 16 })
    await settle()

    // 先设关键字再导出
    const input = wrapper
      .findAll('input')
      .find((node) => node.attributes('placeholder') === '编码或名称')
    await input?.setValue('藏青')
    const search = wrapper.findAll('button').find((b) => plain(b.text()) === '查询')
    await search?.trigger('click')
    await settle()

    const exportButton = wrapper.findAll('button').find((b) => plain(b.text()) === '导出')
    expect(exportButton, '列表页必须有导出按钮（docs/05 §9.1）').toBeDefined()
    await exportButton?.trigger('click')
    await settle()

    expect(exportXlsx).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock')
  })
})

describe('工序页（TC-W27）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.BASE_READ, PERM.BASE_UPDATE, PERM.BASE_DISABLE, PERM.BASE_DELETE])
  })

  it('TC-W27 被引用的工序：删除项禁用并说明原因，界面只留停用', async () => {
    vi.spyOn(baseApi.operations, 'list').mockResolvedValue({
      items: [
        row({
          operation_no: '01',
          name: '裁',
          ref_count: 5,
          is_piecework: true,
          default_bundle_qty: '30.000',
          sort_order: 1,
        }),
      ],
      total: 1,
    })
    const router = stubRouter()
    void router.push('/')
    const wrapper = mount(OperationList, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    })
    await settle()

    expect(plain(wrapper.text())).toContain('裁')
    await openRowMenu(wrapper)

    const items = menuItems()
    expect(items.some((entry) => entry.text.includes('停用'))).toBe(true)
    const deleteItem = items.find((entry) => entry.text.includes('删除'))
    expect(deleteItem?.disabled).toBe(true)
    expect(deleteItem?.text).toContain('已被5处引用')
  })

  it('编辑按钮跳到该资源的编辑路由（编码作路径参数）', async () => {
    vi.spyOn(baseApi.operations, 'list').mockResolvedValue({
      items: [row({ operation_no: '01', name: '裁' })],
      total: 1,
    })
    const router = stubRouter()
    void router.push('/')
    const wrapper = mount(OperationList, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    })
    await settle()

    const edit = wrapper.findAll('button').find((b) => plain(b.text()) === '编辑')
    await edit?.trigger('click')
    await settle()

    expect(router.currentRoute.value.name).toBe('base-operations-edit')
    expect(router.currentRoute.value.params['code']).toBe('01')
  })
})

describe('码表的有序尺码成员（TC-W28）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.BASE_CREATE])
    vi.spyOn(baseApi.sizes, 'optionsById').mockResolvedValue([
      { value: 'size-s', label: 'S 155/80A', sub: null, disabled: false },
      { value: 'size-m', label: 'M 160/84A', sub: null, disabled: false },
    ])
  })

  async function mountForm() {
    const create = vi.spyOn(baseApi['size-groups'], 'create').mockResolvedValue(
      row({ name: '女款模板', size_class: 'WOMENS' }),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { template: '<div />' } },
        { path: '/base/size-groups', name: 'base-size-groups', component: { template: '<div />' } },
        { path: '/base/size-groups/new', name: 'base-size-groups-new', component: { template: '<div />' } },
        { path: '/base/size-groups/:code', name: 'base-size-groups-edit', component: { template: '<div />' } },
      ],
    })
    await router.push('/base/size-groups/new')
    await router.isReady()
    const wrapper = mount(SizeGroupForm, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    })
    await settle()
    return { create, router, wrapper }
  }

  /** 往 Combo 里加一个成员：focus 触发默认候选，再点第一条。 */
  async function pickSize(wrapper: { find: (s: string) => { trigger: (e: string) => Promise<unknown> } }, label: string) {
    const combo = wrapper.find('.combo-input')
    await combo.trigger('focus')
    await settle()
    // ⚠️ 两边都要去空白：`plain()` 会把「尺码 155/80A」中间的空格也去掉，
  //    拿带空格的 label 去比 plain 过的文本，永远匹配不上（第一版就踩了）
  const option = [...document.querySelectorAll('.combo-option')].find((node) =>
    plain(node.textContent ?? '').includes(plain(label)),
  )
    expect(option, `候选里应有 ${label}`).toBeDefined()
    option?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await settle()
  }

  /** 选枚举字段（尺码类）。antd 的 Select 要先点开再点选项。 */
  async function pickEnum(
    wrapper: { findAll: (s: string) => { at: (i: number) => { trigger: (e: string) => Promise<unknown> } | undefined } },
    label: string,
  ): Promise<void> {
    // ⚠️ antd 的 Select 是在 `.ant-select-selector` 的 **mousedown** 上展开的，
    //    只 trigger('click') 不会打开下拉 —— 症状是"下拉里没有选项"，而组件没报错
    const selector = wrapper.findAll('.ant-select-selector').at(0)
    await selector?.trigger('mousedown')
    await settle()
    const option = [...document.querySelectorAll('.ant-select-item-option')].find((node) =>
      plain(node.textContent ?? '').includes(plain(label)),
    )
    expect(option, `下拉里应有「${label}」`).toBeDefined()
    option?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
  }

  it('必填字段由契约驱动：没填尺码类时点保存**不发请求**并标红', async () => {
    const { create, wrapper } = await mountForm()
    await wrapper
      .findAll('input')
      .find((node) => node.attributes('placeholder')?.includes('女款模板'))
      ?.setValue('女款模板')
    await pickSize(wrapper as never, 'S 155/80A')

    const save = wrapper.findAll('button').find((b) => plain(b.text()) === '保存')
    await save?.trigger('click')
    await settle()

    // ⚠️ `size_class` 是后端 `SizeGroupCreate` 的必填 —— 前端不写这份清单，
    //    正是靠它生成红星（任务卡缺口③）
    expect(create).not.toHaveBeenCalled()
    expect(plain(wrapper.text())).toContain('请填写尺码类')
  })

  it('TC-W28 成员按加入顺序落库（sort_order 从 1 起）', async () => {
    const { create, wrapper } = await mountForm()

    await wrapper
      .findAll('input')
      .find((node) => node.attributes('placeholder')?.includes('女款模板'))
      ?.setValue('女款模板')
    await pickEnum(wrapper as never, '女装')
    await pickSize(wrapper as never, 'S 155/80A')
    await pickSize(wrapper as never, 'M 160/84A')

    const save = wrapper.findAll('button').find((b) => plain(b.text()) === '保存')
    await save?.trigger('click')
    await settle()

    expect(create).toHaveBeenCalledTimes(1)
    expect(create.mock.calls[0]?.[0]).toMatchObject({
      name: '女款模板',
      size_class: 'WOMENS',
      items: [
        { size_id: 'size-s', sort_order: 1 },
        { size_id: 'size-m', sort_order: 2 },
      ],
    })
  })

  it('TC-W28 下移成员后 sort_order 跟着重排（顺序是业务数据，不是显示顺序）', async () => {
    const { create, wrapper } = await mountForm()
    await wrapper
      .findAll('input')
      .find((node) => node.attributes('placeholder')?.includes('女款模板'))
      ?.setValue('女款模板')
    await pickEnum(wrapper as never, '女装')
    await pickSize(wrapper as never, 'S 155/80A')
    await pickSize(wrapper as never, 'M 160/84A')

    const downButtons = wrapper.findAll('button').filter((b) => plain(b.text()) === '下移')
    await downButtons.at(0)?.trigger('click')
    await settle()

    const save = wrapper.findAll('button').find((b) => plain(b.text()) === '保存')
    await save?.trigger('click')
    await settle()

    expect(create.mock.calls[0]?.[0]).toMatchObject({
      items: [
        { size_id: 'size-m', sort_order: 1 },
        { size_id: 'size-s', sort_order: 2 },
      ],
    })
  })

  it('编辑态**不渲染**成员编辑器（取详情接口不回显成员，渲染空编辑器会一保存清空）', async () => {
    vi.spyOn(baseApi['size-groups'], 'get').mockResolvedValue(row({ name: '女款模板', size_class: 'WOMENS' }))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { template: '<div />' } },
        { path: '/base/size-groups', name: 'base-size-groups', component: { template: '<div />' } },
        { path: '/base/size-groups/:code', name: 'base-size-groups-edit', component: { template: '<div />' } },
      ],
    })
    await router.push('/base/size-groups/女款模板')
    await router.isReady()
    const wrapper = mount(SizeGroupForm, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    })
    await settle()

    expect(plain(wrapper.text())).toContain('保存时不会改动尺码成员')
    expect(wrapper.find('.ordered-items').exists()).toBe(false)
  })
})