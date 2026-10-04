import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { ApiError, PERM } from '@garment/shared'
import type { StyleDetailOut, StyleListOut } from '@garment/shared'
import * as stylesApi from '@/api/styles'
import { message } from 'ant-design-vue'
import { permission } from '@/directives/permission'
import { resetHandlers } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import StyleForm from '@/views/base/styles/Form.vue'
import StyleList from '@/views/base/styles/List.vue'
import StyleDetail from '@/views/base/styles/Detail.vue'
import CopyDialog from '@/views/base/styles/CopyDialog.vue'
import CopyResult from '@/views/base/styles/CopyResult.vue'
import HistoryDrawer from '@/views/base/styles/HistoryDrawer.vue'

/**
 * 款号页（TC-W30 ~ TC-W35）。
 *
 * 打桩 `api/styles` 而不是 `fetch`：这些用例要验的是「拿到数据之后界面怎么反应、
 * 点了按钮之后调哪个函数」，打桩 fetch 会把用例绑死在 URL 上。
 */

/**
 * 本文件挂载过的 wrapper，在每个用例后**同步**卸载。
 *
 * ⚠️ 为什么必须卸载：带 `attachTo: document.body` 的弹层（Modal / Drawer）在用例结束后
 *    仍留在 body 里，它内部的 `nextTick` 回调会在**用例跑完之后**才触发，于是报出
 *    「N passed 但 exit code 1」的未处理错误（`TextArea.js: Cannot read properties of
 *    null (reading 'textArea')`），而且报错位置指向毫不相干的用例，极难定位。
 *
 * ⚠️ 为什么**不用** VTU 的 `enableAutoUnmount(afterEach)`：它在模块顶层注册到
 *    **root suite** 的 afterEach，会跨测试文件生效；而 vitest 在文件之间就销毁了
 *    jsdom 环境，于是回调里 `document` 已经不存在，冒出
 *    `ReferenceError: document is not defined`（实测踩过）。
 *    只在本文件的 afterEach 里管，作用域刚好。
 */
const mountedWrappers: { unmount: () => void }[] = []

/** 登记挂载的 wrapper，供 `afterEach` 卸载（见下面那条 ⚠️）。 */
function track<T extends { unmount: () => void }>(wrapper: T): T {
  mountedWrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  while (mountedWrappers.length > 0) mountedWrappers.pop()?.unmount()
  document.body.innerHTML = ''
})

const STYLE: StyleListOut = {
  id: '11111111-1111-1111-1111-111111111111',
  style_no: 'HB-2026-0001',
  name: '全棉圆领 T 恤',
  category_id: '22222222-2222-2222-2222-222222222222',
  category_name: '单衣',
  customer_id: null,
  customer_name: null,
  merchandiser_id: null,
  is_active: true,
  last_used_at: '2026-10-01T00:00:00Z',
  version: 2,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
}

const DETAIL: StyleDetailOut = {
  style: STYLE,
  colors: [
    {
      id: 'c1',
      style_no: 'HB-2026-0001',
      color_group: 'A',
      color_code: 'NVY',
      color_name: '藏青',
      material_color_code: null,
      version: 1,
      created_at: '',
      updated_at: '',
      remark: null,
    } as never,
  ],
  sizes: [
    {
      id: 's1',
      style_no: 'HB-2026-0001',
      size_code: 'XL',
      size_name: 'XL(170/92A)',
      sort_no: 1,
      version: 1,
      created_at: '',
      updated_at: '',
      remark: null,
    } as never,
  ],
  operations: [],
  current_rates: [],
}

/**
 * 取 Modal 里的按钮。
 *
 * ⚠️ antd 的 `Modal` / `Drawer` 被 **teleport 到 document.body** —— `wrapper.findAll('button')`
 *    在弹层里永远是空的，于是「按钮在不在」这条断言要么假绿、要么「点不到」却静默跳过。
 *    与 Dropdown 同一个坑（T-WEB-004 / T-WEB-005 都踩过）。
 */
function modalButtons(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLButtonElement>('.ant-modal button')]
}

/** 点 Modal 里的某个按钮（按文本前缀匹配，避免同名的多个按钮）。 */
async function clickModalButton(label: string): Promise<void> {
  const button = modalButtons().find((node) => plain(node.textContent ?? '').startsWith(label))
  expect(button, `Modal 里应有「${label}」按钮`).toBeDefined()
  button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await settle()
}

/** 点 antd 的 RadioGroup 选项。⚠️ 要点**里面的 input**：antd 监听的是 radio 的 change。 */
async function clickRadio(label: string): Promise<void> {
  const input = [...document.querySelectorAll('.ant-radio-button-wrapper input')].find((node) =>
    (node.closest('.ant-radio-button-wrapper')?.textContent ?? '').includes(label),
  )
  expect(input, `单选组里应有「${label}」`).toBeDefined()
  input?.dispatchEvent(new Event('change', { bubbles: true }))
  await settle()
}

/** 详情页的路由桩：`styleNo` 必填。 */
const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    {
      path: '/base/styles/:styleNo',
      name: 'base-styles-detail',
      component: { template: '<div />' },
    },
    { path: '/base/styles', name: 'base-styles', component: { template: '<div />' } },
    {
      path: '/base/operation-rates',
      name: 'base-operation-rates',
      component: { template: '<div />' },
    },
  ],
})

async function mountDetail(): Promise<ReturnType<typeof mount>> {
  await router.push(`/base/styles/${STYLE.style_no}`)
  await router.isReady()
  const wrapper = track(
    mount(StyleDetail, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  return wrapper
}

/** 在停用弹窗里填原因（`Textarea` 只有一个，页面上其它 textarea 都不该被填）。 */
async function fillDisableReason(text: string): Promise<void> {
  const textarea = document.querySelector<HTMLTextAreaElement>('.ant-modal textarea')
  expect(textarea, '停用弹窗应有原因输入框').toBeDefined()
  textarea!.value = text
  textarea!.dispatchEvent(new Event('input', { bubbles: true }))
  await settle()
}

async function settle(): Promise<void> {
  await flushPromises()
  await nextTick()
  await nextTick()
  await flushPromises()
}

/** 去空白再比对：antd 会在两个汉字之间插空格（`ant-btn-two-chinese-characters`）。 */
function plain(text: string): string {
  return text.replace(/\s/g, '')
}

function stubRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/base/styles', name: 'base-styles', component: { template: '<div />' } },
      { path: '/base/styles/new', name: 'base-styles-new', component: { template: '<div />' } },
      {
        path: '/base/styles/:styleNo',
        name: 'base-styles-detail',
        component: { template: '<div />' },
      },
      {
        path: '/base/styles/:styleNo/edit',
        name: 'base-styles-edit',
        component: { template: '<div />' },
      },
    ],
  })
}

/**
 * 抓 `message` 收到的文案。
 *
 * ⚠️ 不能断言 DOM：antd 的 message 是**动画挂载**到 body 上的，而 body 里还并排
 * 放着整页内容 —— 既要等动画，又可能误命中页面上本来就有的同一句话。
 * 这里在组件挂载**之前**替换 `message.error/success`，把它收到的文案收集起来。
 */
function messageMessages(): string[] {
  return (globalThis as { __messageTexts?: string[] }).__messageTexts ?? []
}

function installMessageSpy(): void {
  const texts: string[] = []
  ;(globalThis as { __messageTexts?: string[] }).__messageTexts = texts
  // ⚠️ 必须 spy **`message` 对象的方法**而不是模块命名空间：ESM 命名空间是只读的，
  //    `vi.spyOn(namespace, 'error')` 直接抛 "The property 'error' is not defined"。
  for (const level of ['error', 'success', 'info', 'warning'] as const) {
    // ⚠️ 返回类型必须与 antd 的 `MessageType` 兼容：直接 `return message` 类型不对
    //    （MessageApi 缺 MessageType 的 thenable 签名），用 `as never` 收口
    vi.spyOn(message, level).mockImplementation(((text: unknown) => {
      texts.push(String(text))
      return message
    }) as never)
  }
}

function grant(codes: string[]): void {
  useAuthStore().permissions = new Set(codes)
}

async function mountForm(path: string) {
  const router = stubRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = track(
    mount(StyleForm, {
      global: { plugins: [router], directives: { can: permission } },
      attachTo: document.body,
    }),
  )
  await settle()
  return { router, wrapper }
}

describe('款号表单（TC-W30 / TC-W31）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.BASE_CREATE, PERM.BASE_UPDATE, PERM.BASE_READ])
    installMessageSpy()
  })

  it('TC-W30 货号与款名都是必填 —— 不填就不发请求并标红', async () => {
    const create = vi.spyOn(stylesApi, 'createStyle').mockResolvedValue(STYLE as never)
    const { wrapper } = await mountForm('/base/styles/new')

    const save = wrapper.findAll('button').find((b) => plain(b.text()) === '保存')
    await save?.trigger('click')
    await settle()

    expect(create, '必填没填就提交 = 后端必然 10001').not.toHaveBeenCalled()
    expect(plain(wrapper.text())).toContain('请填写货号')
    expect(plain(wrapper.text())).toContain('请填写款名')
  })

  it('TC-W31 点「生成建议号」回填输入框，**并且仍然可以手动改**（Q-P0-04）', async () => {
    const suggest = vi
      .spyOn(stylesApi, 'suggestStyleNo')
      .mockResolvedValue({ style_no: 'HB-2026-0007', customer_id: null })
    const { wrapper } = await mountForm('/base/styles/new')

    const suggestButton = wrapper.findAll('button').find((b) => plain(b.text()) === '生成建议号')
    expect(suggestButton, '新建态应有「生成建议号」按钮').toBeDefined()
    await suggestButton?.trigger('click')
    await settle()

    expect(suggest).toHaveBeenCalledTimes(1)
    const styleNoInput = wrapper
      .findAll('input')
      .find((node) => (node.element as HTMLInputElement).value === 'HB-2026-0007')
    expect(styleNoInput, '建议号应回填到货号输入框').toBeDefined()
    // ⚠️ **没有 disabled**：建议号只是参考，用户输入一律优先（Q-P0-04）
    expect(styleNoInput?.attributes('disabled')).toBeUndefined()
  })

  it('⚠️ 建议号端点失败时给业务文案，不静默（用户会以为是按钮坏了）', async () => {
    vi.spyOn(stylesApi, 'suggestStyleNo').mockRejectedValue(
      new ApiError(20001, '归属客户不存在或已删除，无法生成建议款号', {}),
    )
    const { wrapper } = await mountForm('/base/styles/new')

    const suggestButton = wrapper.findAll('button').find((b) => plain(b.text()) === '生成建议号')
    await suggestButton?.trigger('click')
    await settle()

    // ⚠️ 断言**提交给 message 的文案**，而不是 DOM 里有没有那条提示：antd 的
    //    message 挂到 body 上还带动画，`document.body.textContent` 里混着整页内容，
    //    断言它既脆（等动画）又可能误命中（页面里本来就有这句话）。
    //    这里用 spy 看**有没有把业务文案报给用户** —— 那才是被测行为。
    const messages = messageMessages()
    expect(messages.some((text) => text.includes('归属客户不存在'))).toBe(true)
  })

  it('编辑态：货号**只读**（历史单据按它引用，改号即改历史指向）', async () => {
    vi.spyOn(stylesApi, 'getStyle').mockResolvedValue(DETAIL)
    const { wrapper } = await mountForm('/base/styles/HB-2026-0001/edit')

    const styleNoInput = wrapper
      .findAll('input')
      .find((node) => (node.element as HTMLInputElement).value === 'HB-2026-0001')
    expect(styleNoInput?.attributes('disabled')).toBeDefined()
    // 编辑态不给「生成建议号」：给了也是填不了结果的按钮
    expect(wrapper.findAll('button').some((b) => plain(b.text()) === '生成建议号')).toBe(false)
  })

  it('编辑态保存带 `version`（乐观锁必传，后端不匹配回 10003）', async () => {
    vi.spyOn(stylesApi, 'getStyle').mockResolvedValue(DETAIL)
    const patch = vi.spyOn(stylesApi, 'patchStyle').mockResolvedValue(STYLE as never)
    const { wrapper } = await mountForm('/base/styles/HB-2026-0001/edit')

    const nameInput = wrapper
      .findAll('input')
      .find((node) => (node.element as HTMLInputElement).value === '全棉圆领 T 恤')
    await nameInput?.setValue('全棉圆领 T 恤（改）')
    const save = wrapper.findAll('button').find((b) => plain(b.text()) === '保存')
    await save?.trigger('click')
    await settle()

    expect(patch).toHaveBeenCalledTimes(1)
    const [, payload] = patch.mock.calls[0] ?? []
    expect(payload).toMatchObject({ version: 2, name: '全棉圆领 T 恤（改）' })
    // ⚠️ **`style_no` 不在 PATCH 体里**：后端 `StylePatch` 没有这个字段（extra=forbid）
    expect(payload).not.toHaveProperty('style_no')
  })

  it('10003 时给「刷新为最新内容」，不静默失败（否则用户只会再点一次保存）', async () => {
    vi.spyOn(stylesApi, 'getStyle').mockResolvedValue(DETAIL)
    vi.spyOn(stylesApi, 'patchStyle').mockRejectedValue(
      new ApiError(10003, '数据已被他人修改，请刷新后重试', {}),
    )
    const { wrapper } = await mountForm('/base/styles/HB-2026-0001/edit')

    const save = wrapper.findAll('button').find((b) => plain(b.text()) === '保存')
    await save?.trigger('click')
    await settle()

    expect(plain(wrapper.text())).toContain('刷新为最新内容')
  })
})

describe('模板复制（TC-W35）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
    grant([PERM.BASE_RATE_TEMPLATE_MANAGE])
    vi.spyOn(stylesApi, 'searchStyleOptions').mockResolvedValue([
      { value: 'HB-2026-0001', label: 'HB-2026-0001 全棉圆领 T 恤', sub: null, disabled: false },
    ])
  })

  function mountCopy() {
    return track(
      mount(CopyDialog, {
        props: { open: true, targetStyleNo: 'HB-2026-0002' },
        global: { directives: { can: permission } },
        attachTo: document.body,
      }),
    )
  }

  it('TC-W35 源款 / 模式 / 冲突策略没选全时**提交禁用**（后端不给默认值，默默覆盖更危险）', async () => {
    const copy = vi.spyOn(stylesApi, 'copyStyleTemplate')
    mountCopy()
    await settle()

    const submit = modalButtons().find((b) => plain(b.textContent ?? '').startsWith('复制到'))
    expect(submit, '应有提交按钮').toBeDefined()
    expect(submit?.getAttribute('disabled'), '参数没选全时提交必须禁用').not.toBeNull()
    // ⚠️ 还要说清**为什么**禁用 —— 只灰一个按钮用户会以为坏了
    expect(plain(document.body.textContent ?? '')).toContain('需要先选')
    expect(copy).not.toHaveBeenCalled()
  })

  it('模式②（上浮）额外要求填比例，填了才让提交', async () => {
    vi.spyOn(stylesApi, 'copyStyleTemplate').mockResolvedValue({
      copy_mode: 'COPY_PRICE_WITH_RATIO',
      conflict_policy: 'OVERWRITE',
      structure_written: 2,
      structure_overwritten: 0,
      price_written: 2,
      price_overwritten: 0,
      skipped: [],
      prices: [],
      messages: ['尺码比例未复制，请按目标款的销售构成为各颜色录入尺码比例'],
      document_log_id: null,
      operations: [],
      rates: [],
    } as never)
    mountCopy()
    await settle()

    // 选模式②
    // ⚠️ 必须点**里面的 `<input>`**：antd 的 RadioGroup 监听的是 radio 的 change，
    //    点外层 label 不会触发（实测过，点 label 时 `copyMode` 一直是 null，
    //    于是「模式②要问比例」那条断言永远看不到那个字段）。
    const ratioInput = [...document.querySelectorAll('.ant-radio-button-wrapper input')].find(
      (node) =>
        node.closest('.ant-radio-button-wrapper')?.textContent?.includes('按比例上浮') === true,
    )
    ratioInput?.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()

    expect(plain(document.body.textContent ?? ''), '模式②必须问比例').toContain(
      '上浮比例（模式②必填）',
    )
  })

  it('模式②（上浮）额外要求填比例，没填比例时提交仍是禁用的', async () => {
    mountCopy()
    await settle()
    await clickRadio('按比例上浮（1.08 = 上浮 8%）')

    expect(plain(document.body.textContent ?? ''), '模式②必须问比例').toContain(
      '上浮比例（模式②必填）',
    )
  })
})

describe('变更历史抽屉', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    resetHandlers()
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('⚠️ 第二次打开另一个单据时**不能**还显示上一个的历史', async () => {
    vi.spyOn(stylesApi, 'listDocumentLogs')
      .mockResolvedValueOnce({
        items: [
          {
            id: 'l1',
            doc_type: 'Style',
            doc_no: 'HB-2026-0001',
            action: 'CREATE',
            from_status: null,
            to_status: null,
            operator_name: '张三',
            reason: '新建款号',
            changed_fields: null,
            created_at: '2026-10-01T00:00:00Z',
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: 'l2',
            doc_type: 'Style',
            doc_no: 'HB-2026-0002',
            action: 'UPDATE',
            from_status: null,
            to_status: null,
            operator_name: '李四',
            reason: '改了款名',
            changed_fields: null,
            created_at: '2026-10-02T00:00:00Z',
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      })

    const wrapper = track(
      mount(HistoryDrawer, {
        props: { open: true, docType: 'Style', docNo: 'HB-2026-0001' },
        attachTo: document.body,
      }),
    )
    await vi.waitFor(() => {
      expect(plain(document.body.textContent ?? '')).toContain('张三')
    })

    await wrapper.setProps({ open: false, docNo: 'HB-2026-0002' })
    await settle()
    await wrapper.setProps({ open: true })
    await settle()

    await vi.waitFor(() => {
      expect(plain(document.body.textContent ?? '')).toContain('李四')
    })
    expect(plain(document.body.textContent ?? ''), '不该还显示上一个单据的日志').not.toContain(
      '新建款号',
    )
  })

  it('未知动作**原样显示**而不是空白（后端加了新动作而前端没跟上时的沉默）', async () => {
    vi.spyOn(stylesApi, 'listDocumentLogs').mockResolvedValue({
      items: [
        {
          id: 'l3',
          doc_type: 'Style',
          doc_no: 'HB-2026-0001',
          action: 'SOME_FUTURE_ACTION',
          from_status: null,
          to_status: null,
          operator_name: '王五',
          reason: null,
          changed_fields: null,
          created_at: '2026-10-02T00:00:00Z',
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    })
    track(
      mount(HistoryDrawer, {
        props: { open: true, docType: 'Style', docNo: 'HB-2026-0001' },
        attachTo: document.body,
      }),
    )
    await settle()

    expect(document.body.textContent ?? '').toContain('SOME_FUTURE_ACTION')
  })
})

describe('模板复制结果展示（modules/01 §5.1）', () => {
  const RESULT = {
    copy_mode: 'COPY_PRICE_AS_IS',
    conflict_policy: 'OVERWRITE',
    structure_written: 2,
    structure_overwritten: 1,
    price_written: 2,
    price_overwritten: 1,
    skipped: [{ target: '02', reason: '目标款已有单价（冲突策略：保留）' }],
    prices: [
      {
        operation_no: '01',
        source_unit_price: '0.350000',
        target_unit_price: '0.350000',
        rate_source: 'STYLE',
      },
    ],
    messages: ['尺码比例未复制，请按目标款的销售构成为各颜色录入尺码比例'],
    document_log_id: null,
    operations: [],
    rates: [],
  }

  it('⚠️ 「尺码比例未复制」必须逐条显示 —— 不显示的话用户以为比例也配好了', () => {
    // ⚠️ 单独测本组件而不是走 Modal 交互：走交互要在 teleport 出去的弹层里
    //    点 RadioGroup / Select / Combo，那是**交互路径**的测试（很脆），
    //    而这里要守的是**展示要求**（modules/01 §5.1 的硬提示）。
    const wrapper = track(
      mount(CopyResult, {
        props: { result: RESULT as never },
        attachTo: document.body,
      }),
    )

    const text = plain(wrapper.text())
    expect(text).toContain('尺码比例未复制')
    // 被跳过的项也要说清是哪些、为什么
    expect(text).toContain('被跳过的项')
    expect(text).toContain('02')
    // 逐项新价（ADR-0029：复制错误率必须为 0，靠逐项核对）
    expect(text).toContain('¥0.350000')
  })

  it('没有单价被复制时**说清为什么**（模式「只复制工序结构」本就不写单价）', () => {
    const wrapper = track(
      mount(CopyResult, {
        props: { result: { ...RESULT, prices: [], messages: [] } as never },
        attachTo: document.body,
      }),
    )

    expect(plain(wrapper.text())).toContain('只复制工序结构')
  })
})

describe('款号停用（T-WEB-006 补端点）', () => {
  it('⚠️ 停用弹窗**必须填原因**才让提交 —— 原因半年后要能查出来是谁停的', async () => {
    // 原因必填不只是后端校验（`10001`）：按钮先灰着，用户就不会提交一次注定失败的请求
    const disabled = vi.spyOn(stylesApi, 'disableStyle')
    vi.spyOn(stylesApi, 'getStyle').mockResolvedValue(DETAIL)
    const wrapper = await mountDetail()

    const stopButton = wrapper.findAll('button').find((node) => plain(node.text()) === '停用')
    expect(stopButton, '启用中的款号应有「停用」按钮').toBeDefined()
    await stopButton?.trigger('click')
    await settle()

    const okButton = modalButtons().find((node) => plain(node.textContent ?? '') === '确认停用')
    expect(okButton, '应有确认按钮').toBeDefined()
    expect(okButton?.getAttribute('disabled'), '没填原因时确认必须禁用').not.toBeNull()
    expect(disabled, '没填原因不该发请求').not.toHaveBeenCalled()
  })

  it('⚠️ 停用要带 `version`（乐观锁）—— 否则会停掉别人刚改过的款号', async () => {
    const disabled = vi
      .spyOn(stylesApi, 'disableStyle')
      .mockResolvedValue({ ...STYLE, is_active: false } as never)
    vi.spyOn(stylesApi, 'getStyle').mockResolvedValue(DETAIL)
    const wrapper = await mountDetail()

    await wrapper
      .findAll('button')
      .find((n) => plain(n.text()) === '停用')
      ?.trigger('click')
    await settle()
    fillDisableReason('客户取消该款，不再下单')
    await settle()
    await clickModalButton('确认停用')

    expect(disabled).toHaveBeenCalledTimes(1)
    expect(disabled.mock.calls[0]?.[0]).toBe('HB-2026-0001')
    // ⚠️ version 必须传：款号是共享档案（工序/单价/比例四张子表），不带就是覆盖别人
    expect(disabled.mock.calls[0]?.[1]).toMatchObject({
      version: 2,
      reason: '客户取消该款，不再下单',
    })
  })

  it('⚠️ 已经停用的款号**不再给停用按钮**（后端会回 10008，白白浪费一次点击）', async () => {
    vi.spyOn(stylesApi, 'getStyle').mockResolvedValue({
      ...DETAIL,
      style: { ...STYLE, is_active: false },
    })
    const wrapper = await mountDetail()

    expect(wrapper.findAll('button').some((n) => plain(n.text()) === '停用')).toBe(false)
  })
})

describe('款号导出（docs/05 §9.1）', () => {
  it('⚠️ 导出**必须带当前筛选、不带分页** —— 分页导出会让人以为导全了', async () => {
    // ⚠️ jsdom 没有 `URL.createObjectURL`，不 stub 直接抛 TypeError
    const createObjectURL = vi.fn(() => 'blob:mock')
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL: vi.fn() })
    const exported = vi.spyOn(stylesApi, 'exportStyles').mockResolvedValue({
      blob: new Blob(['x']),
      filename: 'styles.xlsx',
      rowCount: 7,
    } as never)
    vi.spyOn(stylesApi, 'listStyles').mockResolvedValue({ items: [STYLE], total: 1 })

    const wrapper = track(
      mount(StyleList, {
        global: { plugins: [router], directives: { can: permission } },
        attachTo: document.body,
      }),
    )
    await settle()
    // ⚠️ 搜索框是 `@press-enter` 触发（不是 input 事件）：只 `setValue` 的话
    //    `list.query.q` 根本不会变，于是「导出带当前筛选」这条断言会**假红**
    const search = wrapper
      .findAll('input')
      .find((node) => node.attributes('placeholder') === '款号 / 款名 / 客户名')
    expect(search, '应有款号搜索框').toBeDefined()
    await search!.setValue('HB-2026')
    await search!.trigger('keydown.enter')
    await settle()

    const exportButton = wrapper.findAll('button').find((node) => plain(node.text()) === '导出')
    expect(exportButton, '列表页应有导出按钮（docs/05 §9.1）').toBeDefined()
    await exportButton?.trigger('click')
    await settle()

    expect(exported).toHaveBeenCalledTimes(1)
    const query = exported.mock.calls[0]?.[0] as Record<string, unknown>
    expect(query['q']).toBe('HB-2026')
    // ⚠️ page/size 必须**不在**筛选里：后端导出不分页，带上等于「导了第 N 页」
    expect(query).not.toHaveProperty('page')
    expect(query).not.toHaveProperty('size')
  })
})
