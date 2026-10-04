import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { ApiError, PERM } from '@garment/shared'
import type { RoleOut, UserOut } from '@garment/shared'
import * as systemApi from '@/api/system'
import RoleList from '@/views/system/roles/List.vue'
import UserList from '@/views/system/users/List.vue'
import UserForm from '@/views/system/users/Form.vue'
import { permission } from '@/directives/permission'
import { resetHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

/**
 * 系统管理两页（TC-W21 ~ TC-W23 + 四态）。
 *
 * 打桩 `api/system` 而不是打桩 `fetch`：这些页面的行为全在「拿到数据之后怎么渲染、
 * 点了按钮之后调哪个函数」，打桩 fetch 会把用例绑死在 URL 上，后端改路径就红。
 */

const USER: UserOut = {
  id: '11111111-1111-1111-1111-111111111111',
  employee_no: 'A001',
  name: '张三',
  workshop_id: null,
  group_no: null,
  data_scope: 'GROUP',
  is_active: true,
  must_change_password: false,
  role_codes: ['workshop_supervisor'],
  version: 3,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-02T00:00:00Z',
}

const SYSTEM_ROLE: RoleOut = {
  id: '22222222-2222-2222-2222-222222222222',
  code: 'workshop_supervisor',
  name: '车间主管',
  data_scope: 'WORKSHOP',
  is_system: true,
  description: '本车间单据审批',
  permission_codes: ['cutting:approve', 'piecework:read'],
  user_count: 3,
  version: 1,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}

const CUSTOM_ROLE: RoleOut = {
  ...SYSTEM_ROLE,
  id: '33333333-3333-3333-3333-333333333333',
  code: 'production_leader',
  name: '生产组长',
  is_system: false,
  user_count: 0,
}

/**
 * 等界面稳定。
 *
 * ⚠️ **只调 `flushPromises()` 不够**：Ant Design 的 `Table` / `Dropdown` 在数据到位
 *    之后还要再走若干个 tick 才把行渲染出来。只 flush 的话，用例会看到"表头有了、
 *    数据行没有"，然后要么假失败、要么被写成 `await sleep(50)` 这种靠运气的等待。
 *    实测加两个 `nextTick` 后稳定。
 */
async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

/**
 * 下拉菜单与确认框都被 antd **teleport 到 document.body**。
 *
 * ⚠️ 所以它们不能从 `wrapper` 里找 —— `wrapper.findAll('.ant-dropdown-menu-item')`
 *    永远是空的，于是"点了 `…` 之后菜单里有没有停用项"这条断言会**假绿**
 *    （`find` 返回空数组，`some()` 自然是 false）。必须 `attachTo` 让组件真的挂上，
 *    再从 `document` 里查。
 */
/**
 * 去掉所有空白再比对。
 *
 * ⚠️ antd 的 `Button` 会在两个汉字之间插一个空格（class
 * `ant-btn-two-chinese-characters`），`textContent` 里真的有那个空格。
 * 直接比 `'重试'` 会挂 —— 挂的是一个 antd 排版细节，与被测行为无关。
 */
function plain(text: string): string {
  return text.replace(/\s/g, '')
}

function overlayItems(): string[] {
  return [...document.querySelectorAll('.ant-dropdown-menu-item')].map(
    (node) => node.textContent ?? '',
  )
}

function openRowMenu(wrapper: {
  findAll: (selector: string) => {
    at: (index: number) => { trigger: (event: string) => Promise<unknown> } | undefined
  }
}): Promise<void> {
  const trigger = wrapper.findAll('.ant-dropdown-trigger').at(-1)
  expect(trigger, '行操作里应有 `…` 下拉').toBeDefined()
  return trigger === undefined ? settle() : trigger.trigger('click').then(() => settle())
}

function stubRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/system/users', name: 'system-users', component: { template: '<div />' } },
      // ⚠️ `new` **必须排在 `:id` 前面** —— 反了的话 `/system/users/new` 会被
      // `:id` 吃掉（id="new"），页面以为自己在编辑一个叫 "new" 的用户，
      // 于是显示"编辑用户"并去查一个不存在的账号。真实路由表里也是这个顺序。
      { path: '/system/users/new', name: 'system-user-create', component: { template: '<div />' } },
      { path: '/system/users/:id', name: 'system-user-edit', component: { template: '<div />' } },
      {
        path: '/system/users/:id/password',
        name: 'system-user-password',
        component: { template: '<div />' },
      },
      { path: '/system/roles', name: 'system-roles', component: { template: '<div />' } },
      {
        path: '/system/roles/:id',
        name: 'system-role-permissions',
        component: { template: '<div />' },
      },
    ],
  })
}

function grant(codes: string[]): void {
  useAuthStore().permissions = new Set(codes)
}

describe('用户列表页', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    // 清掉上一次用例 teleport 到 body 的下拉/确认框，否则菜单项会串味
    document.body.innerHTML = ''
    grant([PERM.SYSTEM_USER_MANAGE])
  })

  function mountList(options: { items?: UserOut[]; fail?: boolean } = {}) {
    vi.spyOn(systemApi, 'listUsers').mockImplementation(async () => {
      if (options.fail === true) throw new Error('boom')
      return { items: options.items ?? [USER], total: (options.items ?? [USER]).length }
    })
    const router = stubRouter()
    void router.push('/')
    const wrapper = mount(UserList, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    })
    return { router, wrapper }
  }

  it('TC-W23 列表不含 password_hash —— 而且这条不需要任何"过滤代码"', async () => {
    /**
     * 后端 `UserOut` 模型层就没有 `password_hash`（`UserOut` 的字段是生成的）。
     * 所以本例断言的是**渲染结果**里不会出现任何口令相关字样：
     * 表格列是显式声明的，即使数据里混进了敏感字段也不会凭空多出一列。
     */
    const polluted = { ...USER, password_hash: 'argon2$xxx', phone: '13800000000' } as UserOut
    const { wrapper } = mountList({ items: [polluted] })
    await settle()

    const html = wrapper.html()
    expect(html).not.toContain('argon2')
    expect(html).not.toContain('13800000000')
    // 列头里也没有
    expect(html).not.toContain('password_hash')
  })

  it('显示工号、姓名与角色中文名（不是英文 code）', async () => {
    const { wrapper } = mountList()
    await settle()

    expect(plain(wrapper.text())).toContain('A001')
    expect(plain(wrapper.text())).toContain('张三')
    // ROLE_NAMES 里有 workshop_supervisor → 车间主管；车间里没人认得英文 code
    expect(plain(wrapper.text())).toContain('车间主管')
  })

  it('TC-W21 停用走二次确认且**必填原因**', async () => {
    const disable = vi.spyOn(systemApi, 'disableUser').mockResolvedValue(USER)
    const { wrapper } = mountList()
    await settle()

    await openRowMenu(wrapper)
    const disableItem = [...document.querySelectorAll('.ant-dropdown-menu-item')].find((node) =>
      plain(node.textContent ?? '').includes('停用'),
    )
    expect(disableItem).toBeDefined()
    disableItem?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    // 确认框出现，且确定按钮初始禁用（原因没填）
    const ok = document.querySelector<HTMLButtonElement>('.ant-modal-confirm .ant-btn-primary')
    expect(ok).not.toBeNull()
    expect(ok?.disabled).toBe(true)
    // 没填原因就不该发出请求
    expect(disable).not.toHaveBeenCalled()
  })

  it('TC-W22 停用确认框里写清了后果', async () => {
    vi.spyOn(systemApi, 'disableUser').mockResolvedValue(USER)
    const { wrapper } = mountList()
    await settle()
    await openRowMenu(wrapper)
    const disableItem = [...document.querySelectorAll('.ant-dropdown-menu-item')].find((node) =>
      plain(node.textContent ?? '').includes('停用'),
    )
    disableItem?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    const modal = document.querySelector('.ant-modal-confirm')
    expect(modal?.textContent).toContain('立即无法登录')
  })

  it('四态之空：给出下一步动作，而不是只说"暂无数据"', async () => {
    const { wrapper } = mountList({ items: [] })
    await settle()

    expect(plain(wrapper.text())).toContain('还没有用户')
    expect(plain(wrapper.text())).toContain('新建用户')
  })

  it('四态之错误：显示业务文案 + 重试按钮', async () => {
    // ⚠️ `ApiError` 必须**静态**导入：`usePageList` 用 `instanceof ApiError` 判断，
    //    而动态 import 在 vite 转换后可能给出另一个模块实例，于是 `instanceof` 失败、
    //    异常被当成"非业务异常"重新抛出，错误态永远不显示 —— 而症状是"列表空白"，
    //    指向的方向完全错。
    vi.spyOn(systemApi, 'listUsers').mockRejectedValue(
      new ApiError(12001, '你没有查看用户的权限，请联系管理员开通', {}),
    )
    const router = stubRouter()
    void router.push('/')
    const wrapper = mount(UserList, {
      global: { plugins: [router], directives: { can: permission } },
    })
    await settle()

    expect(plain(wrapper.text())).toContain('加载失败')
    expect(plain(wrapper.text())).toContain('重试')
    // ⚠️ 不能显示成 "Error: boom" 这种技术文案（docs/06 §5）
    expect(plain(wrapper.text())).not.toContain('Error:')
  })

  it('改筛选条件后回到第 1 页（否则用户看到的是空列表）', async () => {
    const calls: { page: number }[] = []
    vi.spyOn(systemApi, 'listUsers').mockImplementation(async (query) => {
      calls.push({ page: query?.page ?? 1 })
      return { items: [USER], total: 40 }
    })
    const router = stubRouter()
    void router.push('/')
    const wrapper = mount(UserList, {
      global: { plugins: [router], directives: { can: permission } },
    })
    await settle()

    // 先翻到第 2 页
    const pager = wrapper.find('.ant-pagination-item-2')
    await pager.trigger('click')
    await settle()
    expect(calls.at(-1)?.page).toBe(2)

    // 再改筛选。⚠️ 用 placeholder 定位而不是 `input` 的下标 —— 页面里还有分页的
    //    每页条数选择框，`at(-1)` 拿到的是它，改了也不影响查询条件。
    const searchInput = wrapper
      .findAll('input')
      .find((node) => node.attributes('placeholder') === '工号或姓名')
    expect(searchInput).toBeDefined()
    await searchInput?.setValue('李四')
    const searchButton = wrapper.findAll('button').find((b) => plain(b.text()) === '查询')
    await searchButton?.trigger('click')
    await settle()

    expect(calls.at(-1)?.page).toBe(1)
  })
})

describe('角色列表页', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.SYSTEM_ROLE_MANAGE])
  })

  function mountList(items: RoleOut[]) {
    vi.spyOn(systemApi, 'listRoles').mockResolvedValue(items)
    const router = stubRouter()
    void router.push('/')
    const wrapper = mount(RoleList, {
      global: { plugins: [router], directives: { can: permission } },
    })
    return { router, wrapper }
  }

  it('TC-W22 内置角色：标「内置」且 `…` 菜单里**没有**停用项', async () => {
    const { wrapper } = mountList([SYSTEM_ROLE])
    await settle()

    expect(plain(wrapper.text())).toContain('内置')
    await openRowMenu(wrapper)

    const labels = overlayItems()
    expect(labels.length).toBeGreaterThan(0)
    expect(labels.some((text) => text.includes('停用'))).toBe(false)
    expect(labels.some((text) => text.includes('配置权限点'))).toBe(true)
  })

  it('自建角色：停用项出现在 `…` 菜单里', async () => {
    const { wrapper } = mountList([CUSTOM_ROLE])
    await settle()

    await openRowMenu(wrapper)

    const labels = overlayItems()
    expect(labels.some((text) => text.includes('停用角色'))).toBe(true)
  })

  it('内置角色的权限**可以**改（超管必须能收权，docs/07 §2.3）', async () => {
    const { wrapper } = mountList([SYSTEM_ROLE])
    await settle()

    const configure = wrapper.findAll('button').find((b) => plain(b.text()) === '配置权限')
    expect(configure).toBeDefined()
  })

  it('页面说明里点明内置角色的约束（code 不可改、不可停用）', async () => {
    const { wrapper } = mountList([SYSTEM_ROLE])
    await settle()

    expect(plain(wrapper.text())).toContain('不可停用')
  })
})

describe('用户表单', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    // 清掉上一次用例 teleport 到 body 的下拉/确认框，否则菜单项会串味
    document.body.innerHTML = ''
    grant([PERM.SYSTEM_USER_MANAGE])
  })

  /**
   * ⚠️ **必须 `await router.push()` + `isReady()`**：不这样的话组件挂载时
   *    `route.params` 还是空的 → `isEdit` 判定为 false → `getUser` 根本不会被调用，
   *    表单永远是空的。症状是"编辑页显示成新建页"，看起来像 bug 而不是测试写错。
   */
  async function mountForm(path = '/system/users/new', options: { rolesError?: boolean } = {}) {
    // ⚠️ `rolesError` 做成参数而不是让用例自己 spy：早先让用例先
    //    `mockRejectedValue` 再调 `mountForm`，结果被这里的 `mockResolvedValue`
    //    覆盖 —— 于是"角色列表拉不到"那条用例测的其实是有数据的情况。
    if (options.rolesError === true) {
      vi.spyOn(systemApi, 'listRoles').mockRejectedValue(new ApiError(12001, '无权限', {}))
    } else {
      vi.spyOn(systemApi, 'listRoles').mockResolvedValue([SYSTEM_ROLE, CUSTOM_ROLE])
    }
    const router = stubRouter()
    await router.push(path)
    await router.isReady()
    const wrapper = mount(UserForm, {
      global: { plugins: [router], directives: { can: permission } },
    })
    return { router, wrapper }
  }

  it('新建态：工号可填、初始口令必填、数据范围可选', async () => {
    const { wrapper } = await mountForm()
    await settle()

    const text = wrapper.text()
    expect(text).toContain('工号')
    expect(text).toContain('初始口令')
    expect(text).toContain('数据范围')
  })

  it('编辑态：工号只读（登录凭据不可改），且不出现初始口令输入框', async () => {
    vi.spyOn(systemApi, 'getUser').mockResolvedValue(USER)
    const { wrapper } = await mountForm('/system/users/11111111-1111-1111-1111-111111111111')
    await settle()
    await settle()

    const employeeNo = wrapper
      .findAll('input')
      .find((node) => (node.element as HTMLInputElement).value === 'A001')
    expect(employeeNo?.attributes('disabled')).toBeDefined()
    expect(plain(wrapper.text())).not.toContain('初始口令')
  })

  it('编辑态：提交时先改基本信息再换角色（顺序反了第二个请求乐观锁会对不上）', async () => {
    vi.spyOn(systemApi, 'getUser').mockResolvedValue(USER)
    const order: string[] = []
    vi.spyOn(systemApi, 'patchUser').mockImplementation(async () => {
      order.push('patch')
      return USER
    })
    vi.spyOn(systemApi, 'assignUserRoles').mockImplementation(async () => {
      order.push('roles')
      return USER
    })

    const { wrapper } = await mountForm('/system/users/11111111-1111-1111-1111-111111111111')
    await settle()

    // ⚠️ 比对按钮文案要去空白：antd 会在两个汉字之间插一个空格
    const save = wrapper.findAll('button').find((b) => plain(b.text()) === '保存')
    expect(save, '应能找到保存按钮').toBeDefined()
    await save?.trigger('click')
    await settle()

    expect(order).toEqual(['patch', 'roles'])
  })

  it('角色列表拉不到时不阻断建号（车间主管没有角色管理权）', async () => {
    const { wrapper } = await mountForm('/system/users/new', { rolesError: true })
    await settle()

    expect(plain(wrapper.text())).toContain('没能读到角色列表')
    // 但保存按钮仍可用 —— 先建号、之后由管理员分配角色
    const save = wrapper.findAll('button').find((b) => plain(b.text()) === '保存')
    expect(save?.attributes('disabled')).toBeUndefined()
  })
})
