import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import type { OptionOut } from '@garment/shared'
import Combo from '@/components/Combo.vue'

/**
 * `<Combo>` 可搜索下拉（TC-W13 ~ TC-W16）。
 *
 * ## 假定时器是必须的，不是可选的
 *
 * 300ms 防抖用真实时间测会写出"至少 sleep 350ms"的用例 —— 慢、易抖动、而且在 CI 上
 * 时不时假失败。用 `vi.useFakeTimers()` 才能**精确**断言"输入后 299ms 不发请求、
 * 300ms 发一次"，顺带把"连续输入 5 个字只发 1 次"这种真正要防的行为钉住。
 *
 * ## 为什么 mock `fetchOptions` 而不是打桩 fetch
 *
 * 组件的输入是一个**函数**（`fetchOptions`），这正是可测性的体现：这里不需要关心
 * 请求 URL、请求头、统一响应包装。用打桩 fetch 反而把测试绑死在 URL 上，
 * 改个路径就红。
 */

function option(value: string, label: string, extra: Partial<OptionOut> = {}): OptionOut {
  return { value, label, sub: null, disabled: false, ...extra }
}

/**
 * 候选项按**后端真实形状**构造：`label` 里已经含编码
 * （`base/service.py` 的款号候选是 `label=f"{style_no} {name}"`）。
 * 用「label 只有名称」的假数据写测试，会让"前端要不要自己拼编码"这个真问题测不出来。
 */
const STYLES: OptionOut[] = [
  option('0021', '0021 夏季连衣裙'),
  option('0022', '0022 夏季短袖衬衫'),
  option('0023', '0023 已停用款', { sub: '已停用', disabled: true }),
]

describe('Combo 可搜索下拉', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  function mountCombo(props: Partial<InstanceType<typeof Combo>['$props']> = {}) {
    const fetchOptions = vi.fn<(keyword: string) => Promise<OptionOut[]>>()
    fetchOptions.mockResolvedValue(STYLES)
    const wrapper = mount(Combo, {
      props: {
        modelValue: null,
        fetchOptions,
        ...props,
      },
      attachTo: document.body,
    })
    return { fetchOptions, wrapper }
  }

  async function flush(): Promise<void> {
    await nextTick()
    await Promise.resolve()
    await nextTick()
  }

  function input(wrapper: ReturnType<typeof mountCombo>['wrapper']) {
    return wrapper.get('input')
  }

  async function typeKeyword(
    wrapper: ReturnType<typeof mountCombo>['wrapper'],
    text: string,
  ): Promise<void> {
    await input(wrapper).setValue(text)
    await flush()
  }

  function options(wrapper: ReturnType<typeof mountCombo>['wrapper']) {
    return wrapper.findAll('.combo-option')
  }

  it('TC-W13 输入后 300ms 才发请求，且传的是输入的关键字', async () => {
    const { fetchOptions, wrapper } = mountCombo()
    await typeKeyword(wrapper, '0021')

    // 299ms：还没到点
    vi.advanceTimersByTime(299)
    await flush()
    expect(fetchOptions).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    await flush()
    expect(fetchOptions).toHaveBeenCalledTimes(1)
    expect(fetchOptions).toHaveBeenCalledWith('0021')
  })

  it('连续输入只发一次请求（防抖的意义就在这里）', async () => {
    const { fetchOptions, wrapper } = mountCombo()
    await typeKeyword(wrapper, '0')
    vi.advanceTimersByTime(100)
    await typeKeyword(wrapper, '00')
    vi.advanceTimersByTime(100)
    await typeKeyword(wrapper, '0021')
    vi.advanceTimersByTime(300)
    await flush()

    expect(fetchOptions).toHaveBeenCalledTimes(1)
    expect(fetchOptions).toHaveBeenCalledWith('0021')
  })

  it('候选最多 20 条，超出提示「请继续输入关键字」', async () => {
    const many = Array.from({ length: 30 }, (_, i) => option(`S${i}`, `款号 ${i}`))
    const { wrapper } = mountCombo({ fetchOptions: vi.fn().mockResolvedValue(many) })
    await typeKeyword(wrapper, '款')

    vi.advanceTimersByTime(300)
    await flush()

    expect(options(wrapper)).toHaveLength(20)
    expect(wrapper.text()).toContain('请继续输入关键字')
  })

  it('TC-W14 ↓ 移动高亮 + Enter 选中，并回显「编码 + 名称」', async () => {
    const { wrapper } = mountCombo()
    await input(wrapper).trigger('focus')
    await flush()
    vi.advanceTimersByTime(300)
    await flush()

    // 默认高亮落在第一项
    expect(options(wrapper)[0]?.classes()).toContain('is-active')

    await input(wrapper).trigger('keydown', { key: 'ArrowDown' })
    expect(options(wrapper)[1]?.classes()).toContain('is-active')

    await input(wrapper).trigger('keydown', { key: 'Enter' })
    await flush()

    // 回显必须是「编码 + 名称」（docs/05 §9.5.2），不是光秃秃的编码 ——
    // 后端已经把 `f"{style_no} {name}"` 拼进 label，前端只负责原样显示 + sub
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['0022'])
    expect((input(wrapper).element as HTMLInputElement).value).toBe('0022 夏季短袖衬衫')
  })

  it('↑/↓ 会在可选项之间循环，不会停在停用项上', async () => {
    const { wrapper } = mountCombo()
    await input(wrapper).trigger('focus')
    await flush()
    vi.advanceTimersByTime(300)
    await flush()

    const visited: number[] = []
    for (let i = 0; i < 4; i += 1) {
      await input(wrapper).trigger('keydown', { key: 'ArrowDown' })
      visited.push(options(wrapper).findIndex((o) => o.classes().includes('is-active')))
    }
    await input(wrapper).trigger('keydown', { key: 'ArrowUp' })
    visited.push(options(wrapper).findIndex((o) => o.classes().includes('is-active')))

    expect(visited).not.toContain(2)
    expect(new Set(visited)).toEqual(new Set([0, 1]))
  })

  it('Enter 被 preventDefault（否则父级表单被顺带提交）', async () => {
    const { wrapper } = mountCombo()
    await input(wrapper).trigger('focus')
    await flush()
    vi.advanceTimersByTime(300)
    await flush()

    // 用户只是选个款号，结果整张单据被提交 —— 这个坑靠 preventDefault 避免
    const event = new KeyboardEvent('keydown', { key: 'Enter', cancelable: true })
    input(wrapper).element.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)
  })

  it('TC-W15 停用项标红，且无论怎么按键盘都选不中它', async () => {
    const { wrapper } = mountCombo()
    await input(wrapper).trigger('focus')
    await flush()
    vi.advanceTimersByTime(300)
    await flush()

    const disabled = options(wrapper)[2]
    expect(disabled?.classes()).toContain('is-disabled')
    expect(disabled?.text()).toContain('已停用')

    // docs/05 §9.5.2：停用项「不可直接选中（需提示已停用，是否启用）」——
    // 不变的是**值**，所以这里只能断言"选中的永远是可用项"
    for (let i = 0; i < 4; i += 1) {
      await input(wrapper).trigger('keydown', { key: 'ArrowDown' })
      await input(wrapper).trigger('keydown', { key: 'Enter' })
    }

    const picked = wrapper.emitted('update:modelValue')?.map((args) => args[0]) ?? []
    expect(picked).not.toContain('0023')
    expect(picked.length).toBeGreaterThan(0)
  })

  it('鼠标点停用项也不选中，并提示已停用', async () => {
    const { wrapper } = mountCombo()
    await input(wrapper).trigger('focus')
    await flush()
    vi.advanceTimersByTime(300)
    await flush()

    await options(wrapper)[2]?.trigger('mousedown')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.text()).toContain('已停用，请先启用后再选')
  })

  it('TC-W16 空结果 → 「无匹配结果」+「＋ 新建」入口', async () => {
    const { wrapper } = mountCombo({
      fetchOptions: vi.fn().mockResolvedValue([]),
      allowCreate: true,
      createText: '＋ 用「{kw}」新建款号',
    })
    await typeKeyword(wrapper, '不存在的款')
    vi.advanceTimersByTime(300)
    await flush()

    expect(wrapper.text()).toContain('无匹配结果')
    expect(wrapper.find('.combo-create').exists()).toBe(true)
  })

  it('点「＋ 新建」把关键字抛给页面（跳哪个新建表单由页面决定）', async () => {
    const { wrapper } = mountCombo({
      fetchOptions: vi.fn().mockResolvedValue([]),
      allowCreate: true,
    })
    await typeKeyword(wrapper, '新款')
    vi.advanceTimersByTime(300)
    await flush()

    await wrapper.find('.combo-create').trigger('mousedown')

    expect(wrapper.emitted('create')?.[0]).toEqual(['新款'])
  })

  it('未开 allowCreate 时不显示新建入口', async () => {
    const { wrapper } = mountCombo({ fetchOptions: vi.fn().mockResolvedValue([]) })
    await typeKeyword(wrapper, '新款')
    vi.advanceTimersByTime(300)
    await flush()

    expect(wrapper.find('.combo-create').exists()).toBe(false)
  })

  it('搜索失败时保留原候选并给「重试」，不静默清空', async () => {
    const fetchOptions = vi
      .fn<(keyword: string) => Promise<OptionOut[]>>()
      .mockResolvedValueOnce(STYLES)
      .mockRejectedValueOnce(new Error('network down'))
    const { wrapper } = mountCombo({ fetchOptions })

    await input(wrapper).trigger('focus')
    await flush()
    vi.advanceTimersByTime(300)
    await flush()
    expect(options(wrapper)).toHaveLength(3)

    await typeKeyword(wrapper, '002')
    vi.advanceTimersByTime(300)
    await flush()

    // 清空候选 = 用户以为"库里没有"，然后手工录一份 —— 那是数据污染的起点
    expect(options(wrapper)).toHaveLength(3)
    expect(wrapper.text()).toContain('搜索失败')
    expect(wrapper.find('.combo-retry').exists()).toBe(true)
  })

  it('点「重试」重新发一次请求', async () => {
    const fetchOptions = vi
      .fn<(keyword: string) => Promise<OptionOut[]>>()
      .mockResolvedValueOnce(STYLES)
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce(STYLES)
    const { wrapper } = mountCombo({ fetchOptions })

    await input(wrapper).trigger('focus')
    await flush()
    vi.advanceTimersByTime(300)
    await flush()
    await typeKeyword(wrapper, '002')
    vi.advanceTimersByTime(300)
    await flush()

    await wrapper.find('.combo-retry').trigger('mousedown')
    await flush()

    expect(fetchOptions).toHaveBeenCalledTimes(3)
    expect(options(wrapper)).toHaveLength(3)
  })

  it('Esc 关闭候选面板', async () => {
    const { wrapper } = mountCombo()
    await input(wrapper).trigger('focus')
    await flush()
    expect(wrapper.find('.combo-panel').exists()).toBe(true)

    await input(wrapper).trigger('keydown', { key: 'Escape' })

    expect(wrapper.find('.combo-panel').exists()).toBe(false)
  })

  it('点击组件外部关闭面板', async () => {
    const { wrapper } = mountCombo()
    await input(wrapper).trigger('focus')
    await flush()
    expect(wrapper.find('.combo-panel').exists()).toBe(true)

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flush()

    expect(wrapper.find('.combo-panel').exists()).toBe(false)
  })

  it('卸载时取消待执行的防抖搜索（否则会对已卸载组件发请求）', async () => {
    const { fetchOptions, wrapper } = mountCombo()
    await typeKeyword(wrapper, '0021')
    // 还没到 300ms 就卸载
    wrapper.unmount()
    vi.advanceTimersByTime(1000)
    await flush()

    expect(fetchOptions).not.toHaveBeenCalled()
  })

  it('外部改 modelValue 时用 resolveLabel 补回显（刷新页面后只有编码）', async () => {
    const resolveLabel = vi.fn().mockResolvedValue(option('0021', '0021 夏季连衣裙'))
    const { wrapper } = mountCombo({ modelValue: '0021', resolveLabel })

    await flush()

    expect(resolveLabel).toHaveBeenCalledWith('0021')
    expect((input(wrapper).element as HTMLInputElement).value).toBe('0021 夏季连衣裙')
  })

  it('resolveLabel 失败时退回显示编码，不白屏', async () => {
    const resolveLabel = vi.fn().mockRejectedValue(new Error('boom'))
    const { wrapper } = mountCombo({ modelValue: '0021', resolveLabel })

    await flush()

    expect((input(wrapper).element as HTMLInputElement).value).toBe('0021')
  })
})
