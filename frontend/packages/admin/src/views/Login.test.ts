import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { ApiError } from '@garment/shared'
import Login from '@/views/Login.vue'
import { useAuthStore } from '@/stores/auth'

/**
 * 登录页（TC-W17 与验收标准里的两条口径）。
 *
 * 值得测的不是"能不能登录"，而是两条**容易被做错**的口径：
 *  1. 文案**不区分**「账号不存在」与「口令错误」—— 分出来就成了账号枚举工具；
 *  2. 网络错误必须给「重试」—— 否则用户唯一的出路是刷新整页、丢掉已填的工号。
 */

function stubRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: Login },
    ],
  })
  return router
}

describe('Login 登录页', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    sessionStorage.clear()
    useAuthStore().clear()
  })

  function mountLogin() {
    const router = stubRouter()
    router.push('/login')
    const wrapper = mount(Login, { global: { plugins: [router] } })
    return { router, wrapper }
  }

  function fill(wrapper: ReturnType<typeof mountLogin>['wrapper']): void {
    const inputs = wrapper.findAll('input')
    void inputs[0]?.setValue('A001')
    void inputs[1]?.setValue('wrong-password')
  }

  it('TC-W17 口令错误时文案不区分「账号不存在」与「口令错误」', async () => {
    const { wrapper } = mountLogin()
    const auth = useAuthStore()
    vi.spyOn(auth, 'login').mockRejectedValue(
      new ApiError(31001, '工号或口令错误', { status: 401 }),
    )

    fill(wrapper)
    await wrapper.find('button[type="button"], button').trigger('click')
    await flushPromises()

    const text = wrapper.text()
    // 防枚举：绝不能出现"账号不存在"这种能区分存在性的措辞
    expect(text).not.toContain('账号不存在')
    expect(text).not.toContain('用户不存在')
    expect(text).toContain('账号或口令不正确')
  })

  it('网络错误给「重试」按钮，而不是让用户自己刷新页面', async () => {
    const { wrapper } = mountLogin()
    const auth = useAuthStore()
    const login = vi
      .spyOn(auth, 'login')
      .mockRejectedValue(new ApiError(0, '服务器返回了非预期的内容，请稍后重试', { status: 0 }))

    fill(wrapper)
    await wrapper.find('button[type="button"], button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('网络异常')
    expect(login).toHaveBeenCalledTimes(1)

    // 点「重试」应该真的再发一次，而不是刷新整页
    const retry = wrapper.findAll('a').find((node) => node.text() === '重试')
    expect(retry).toBeDefined()
    await retry?.trigger('click')
    await flushPromises()

    expect(login).toHaveBeenCalledTimes(2)
  })

  it('回车提交（docs/06 §6.3「登录页回车提交」）', async () => {
    const { wrapper } = mountLogin()
    const auth = useAuthStore()
    const login = vi.spyOn(auth, 'login').mockRejectedValue(new Error('stop here'))

    fill(wrapper)
    const passwordInput = wrapper.findAll('input')[1]
    await passwordInput?.trigger('keydown.enter')
    await flushPromises()

    expect(login).toHaveBeenCalledWith('A001', 'wrong-password')
  })

  it('提交期间禁用输入并显示 loading，防重复提交', async () => {
    const { wrapper } = mountLogin()
    const auth = useAuthStore()
    let release: (() => void) | undefined
    vi.spyOn(auth, 'login').mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )

    fill(wrapper)
    await wrapper.find('button[type="button"], button').trigger('click')
    await flushPromises()

    // antd 的 Button 在 loading 时加 `ant-btn-loading` 并阻止点击，而不是加 disabled
    // 属性 —— 所以断 class 而不是断 disabled，否则这条用例会一直挂着一个不存在的断言
    expect(wrapper.find('button.ant-btn-loading').exists()).toBe(true)

    release?.()
    await flushPromises()
  })

  it('提交失败后清空口令（保住工号），但不清工号 —— 用户只需改一个字段', async () => {
    const { wrapper } = mountLogin()
    vi.spyOn(useAuthStore(), 'login').mockRejectedValue(new Error('nope'))

    fill(wrapper)
    await wrapper.find('button[type="button"], button').trigger('click')
    await flushPromises()

    const inputs = wrapper.findAll('input')
    expect((inputs[0]?.element as HTMLInputElement).value).toBe('A001')
    expect((inputs[1]?.element as HTMLInputElement).value).toBe('')
  })

  it('redirect 只接受站内相对路径（防开放重定向）', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { template: '<div />' } },
        { path: '/login', name: 'login', component: Login },
      ],
    })
    await router.push('/login?redirect=//evil.example.com/x')
    const wrapper = mount(Login, { global: { plugins: [router] } })
    vi.spyOn(useAuthStore(), 'login').mockResolvedValue(undefined)

    fill(wrapper)
    await wrapper.find('button[type="button"], button').trigger('click')
    await flushPromises()

    // 协议相对地址（//host）会被浏览器当外站 —— 必须退回首页而不是跳过去
    expect(router.currentRoute.value.name).toBe('home')
  })
})
