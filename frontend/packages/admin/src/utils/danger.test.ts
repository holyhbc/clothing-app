import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { confirmDanger, isReasonValid, MIN_REASON_LENGTH } from '@/utils/danger'

/**
 * 危险操作二次确认（TC-W21「停用未填原因 → 提交按钮禁用」）。
 *
 * ## 为什么必须在**真 DOM** 上断言
 *
 * 这段逻辑的三个关键点全在浏览器行为上：
 *  - 确认按钮初始 `disabled`（`okButtonProps`）→ 断言它必须在 DOM 上真的是 disabled；
 *  - 输入够长后解禁 → 依赖 `Modal.update()` 真的生效；
 *  - 取消返回 `null` → 依赖 `onCancel` 被触发。
 *  打桩掉 Modal 就等于把要测的东西删掉了。
 */

function modalEl(): HTMLElement {
  const el = document.querySelector<HTMLElement>('.ant-modal-confirm')
  if (el === null) throw new Error('确认框没有渲染出来')
  return el
}

function okButton(): HTMLButtonElement {
  const btn = modalEl().querySelector<HTMLButtonElement>('.ant-btn-primary')
  if (btn === null) throw new Error('确认框里找不到确认按钮')
  return btn
}

function cancelButton(): HTMLButtonElement {
  const btn = modalEl().querySelector<HTMLButtonElement>('.ant-btn:not(.ant-btn-primary)')
  if (btn === null) throw new Error('确认框里找不到取消按钮')
  return btn
}

function reasonInput(): HTMLTextAreaElement {
  const input = modalEl().querySelector<HTMLTextAreaElement>('textarea')
  if (input === null) throw new Error('确认框里找不到原因输入框')
  return input
}

function typeReason(text: string): void {
  const input = reasonInput()
  input.value = text
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('confirmDanger 危险操作二次确认', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('TC-W21 必填原因时，确认按钮初始就是禁用的', async () => {
    void confirmDanger({ title: '停用用户', content: '停用后立即无法登录', requireReason: true })
    await flushPromises()

    expect(okButton().disabled).toBe(true)
    expect(modalEl().textContent).toContain('必填')
  })

  it('原因够长后解禁确认按钮', async () => {
    void confirmDanger({ title: '停用用户', content: '停用后立即无法登录', requireReason: true })
    await flushPromises()

    typeReason('员工已离职，工号回收')
    await flushPromises()

    expect(okButton().disabled).toBe(false)
  })

  it('MIN_REASON_LENGTH 是个真门槛：4 个字不够（登记为 L-050）', async () => {
    // 写这条的时候踩过一次：测试数据用了「员工离职」正好 4 个字，达不到门槛，
    // 于是"解禁按钮"与"确认后返回理由"两条用例一起挂掉，症状看起来像组件坏了。
    // 所以把边界显式断言出来。
    expect(MIN_REASON_LENGTH).toBe(5)
    expect(isReasonValid('员工离职')).toBe(false)
    expect(isReasonValid('员工已离职')).toBe(true)
  })

  it('原因太短仍然禁用（「改」不算理由）', async () => {
    void confirmDanger({ title: '停用用户', content: '停用后立即无法登录', requireReason: true })
    await flushPromises()

    typeReason('改')
    await flushPromises()

    expect(okButton().disabled).toBe(true)
    expect(isReasonValid('改')).toBe(false)
    expect(isReasonValid('x'.repeat(MIN_REASON_LENGTH))).toBe(true)
  })

  it('确认后返回用户填的理由（会进 document_logs）', async () => {
    const promise = confirmDanger({
      title: '停用用户',
      content: '停用后立即无法登录',
      requireReason: true,
    })
    await flushPromises()

    typeReason('员工已离职，工号回收')
    await flushPromises()
    okButton().click()
    const result = await promise

    expect(result).toEqual({ reason: '员工已离职，工号回收' })
  })

  it('理由首尾空格被去掉（存进日志的应该是干净文本）', async () => {
    const promise = confirmDanger({
      title: '停用用户',
      content: '停用后立即无法登录',
      requireReason: true,
    })
    await flushPromises()

    typeReason('   员工已离职，工号回收   ')
    await flushPromises()
    okButton().click()

    expect(await promise).toEqual({ reason: '员工已离职，工号回收' })
  })

  it('取消返回 null —— 调用方拿到 null 就不发请求', async () => {
    const promise = confirmDanger({
      title: '停用用户',
      content: '停用后立即无法登录',
      requireReason: true,
    })
    await flushPromises()

    cancelButton().click()

    expect(await promise).toBeNull()
  })

  it('不要求原因时按钮不禁用，且 reason 为 null', async () => {
    const promise = confirmDanger({ title: '导出', content: '将导出全部记录' })
    await flushPromises()

    expect(okButton().disabled).toBe(false)
    okButton().click()

    expect(await promise).toEqual({ reason: null })
  })

  it('必须展示后果说明：只写「确定吗？」等于没提示', async () => {
    void confirmDanger({
      title: '反审核',
      content: '反审核后已生成的计件将失效',
      requireReason: true,
    })
    await flushPromises()

    expect(modalEl().textContent).toContain('反审核后已生成的计件将失效')
  })

  it('危险操作的确认按钮文案是动词（"停用" 而不是 "确定"）', async () => {
    void confirmDanger({ title: '停用用户', content: '停用后立即无法登录', okText: '停用' })
    await flushPromises()

    // ⚠️ antd 会在两个汉字之间插一个空格（`ant-btn-two-chinese-characters`），
    //    所以比对前要去掉所有空白，否则断言的是一个 antd 的排版细节。
    expect(okButton().textContent?.replace(/\s/g, '')).toBe('停用')
  })
})
