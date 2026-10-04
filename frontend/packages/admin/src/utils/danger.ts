/**
 * 危险操作二次确认（docs/06 §5「危险确认 → `Modal.confirm` + **必填原因输入**」、
 * docs/03 §2.1「任何'审核'动作必须记录原因」）。
 *
 * ## 为什么抽出来而不是每页写一遍
 *
 * 「必填原因」很容易被"看起来对"的实现绕过：
 *  - 按钮 `disabled` 没绑校验 → 用户以为能点，点了没反应，于是重复点三次；
 *  - 校验了但没提示 → 用户不知道**为什么**不能提交；
 *  - 直接 `Modal.confirm` 就完事 → 后端返回 `10006 REASON_REQUIRED`，
 *    用户刚填的内容全丢（弹窗一关就什么都没了）。
 *
 * 这里把它做成返回 `Promise` 的函数：**取消 → `null`；确认 → `{ reason }`**。
 * 调用方拿到 `null` 就不发请求，于是"忘了校验"这件事在结构上就不可能发生。
 *
 * ⚠️ **禁止原生 `confirm()` / `alert()`**（docs/06 §5）。
 */
import { h, ref } from 'vue'
import type { VNode } from 'vue'
import { Modal, message } from 'ant-design-vue'

/** 理由最少几个字。太短的「改」等于没写，而 `document_logs` 里的理由是事后唯一的追溯线索。 */
export const MIN_REASON_LENGTH = 5

export interface DangerConfirmOptions {
  /** 弹窗标题，如「停用用户」。 */
  title: string
  /**
   * 后果说明。**必须说清后果**，例如
   * 「停用后该账号立即无法登录，进行中的单据会留在原状态」。
   * 只写「确定要停用吗？」等于没提示 —— 用户是在不知道后果的情况下点的确定。
   */
  content: string
  /** 是否必须填原因。审核 / 反审核 / 停用 / 改权限 = `true`。 */
  requireReason?: boolean
  /** 确认按钮文案，默认「确定」。危险操作建议写成动词（「停用」而不是「确定」）。 */
  okText?: string
}

export interface DangerConfirmResult {
  /** 用户填写的理由；`requireReason=false` 时为 `null`。 */
  reason: string | null
}

/** 理由够长的判定。抽出来是为了和按钮禁用共用同一份逻辑。 */
export function isReasonValid(reason: string): boolean {
  return reason.trim().length >= MIN_REASON_LENGTH
}

/**
 * 弹出危险操作确认框。
 *
 * ## 为什么有重载
 *
 * `requireReason: true` 时返回的 `reason` **一定是字符串**。用单一返回类型的话，
 * 每次调用都要写 `if (result.reason === null) return` 才敢往下传 ——
 * 而那个判断在类型上永远不成立（要求理由时后端也会再拒一次），于是它变成噪声，
 * 真正该防的"忘了判空"反而被淹没。这里用重载把"要么有理由要么是 null"编码进类型。
 *
 * @returns 取消或理由不合格 → `null`（`onOk` 里校验不过会**保持弹窗打开**并给出提示，
 *   所以 promise 不结束）；确认 → `{ reason }`。
 */
export function confirmDanger(
  options: DangerConfirmOptions & { requireReason: true },
): Promise<{ reason: string } | null>
export function confirmDanger(options: DangerConfirmOptions): Promise<DangerConfirmResult | null>
export function confirmDanger(options: DangerConfirmOptions): Promise<DangerConfirmResult | null> {
  const requireReason = options.requireReason ?? false

  return new Promise<DangerConfirmResult | null>((resolve) => {
    // 用 `ref` 抓 textarea，而不是 `document.querySelector`：后者的选择器一改版
    // 就静默失效，而失效的表现是"必填校验形同虚设" —— 用户能空着理由点确定。
    const textarea = ref<HTMLTextAreaElement | null>(null)

    const setOkDisabled = (disabled: boolean): void => {
      modalHandle.update({ okButtonProps: { disabled } })
    }

    function buildBody(): VNode {
      const children: VNode[] = [h('p', { style: { margin: '0 0 12px' } }, options.content)]

      if (requireReason) {
        children.push(
          h(
            'label',
            { style: { display: 'block', marginBottom: '4px' } },
            `原因（必填，至少 ${MIN_REASON_LENGTH} 个字，会记入操作日志）`,
          ),
        )
        children.push(
          h('textarea', {
            class: 'danger-reason-input',
            rows: 3,
            style: { width: '100%' },
            ref: (el: unknown) => {
              textarea.value = el instanceof HTMLTextAreaElement ? el : null
            },
            onInput: (event: Event) => {
              const value = (event.target as HTMLTextAreaElement).value
              setOkDisabled(!isReasonValid(value))
            },
          }),
        )
        children.push(
          h(
            'p',
            {
              style: {
                margin: '4px 0 0',
                color: 'var(--color-text-third)',
                fontSize: 'var(--font-size-sm)',
              },
            },
            '例：员工离职 / 误操作恢复 / 权限到期回收',
          ),
        )
      }

      return h('div', children)
    }

    const modalHandle = Modal.confirm({
      title: options.title,
      content: buildBody,
      okText: options.okText ?? '确定',
      cancelText: '取消',
      // 点遮罩 / 按 ESC 都算取消，不做任何动作
      maskClosable: false,
      keyboard: false,
      // ⚠️ 初始就是禁用的：不给"先点确定再报错"的机会（TC-W21）
      okButtonProps: { disabled: requireReason },
      onOk: () => {
        const reason = textarea.value?.value.trim() ?? ''
        if (requireReason && !isReasonValid(reason)) {
          // 兜底而不是信任 UI：按钮已禁用，但回车 / 编程调用仍可能走到这里。
          // 保持弹窗打开并说明原因，**不静默失败**。
          void message.error(`请填写原因（至少 ${MIN_REASON_LENGTH} 个字）`)
          setOkDisabled(true)
          return Promise.reject(new Error('必须填写原因'))
        }
        resolve({ reason: requireReason ? reason : null })
        return Promise.resolve()
      },
      onCancel: () => {
        resolve(null)
      },
    })
  })
}
