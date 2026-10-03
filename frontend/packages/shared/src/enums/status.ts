/**
 * 单据状态的中文与色 token 映射（docs/06 §1「状态色映射固定，全系统一致」）。
 *
 * ⚠️ **页面里禁止写 `switch` / 硬编码颜色**：色值一律走 design token
 * （`var(--color-*)`），这里是全系统唯一的映射来源。
 *
 * ⚠️ **取值来自后端**（docs/06 §7「枚举值来自后端，前端只做中文映射，不重新定义取值」）。
 * 这里出现的是后端 `app/common/enums.py::DocumentStatus` 的成员；后端
 * `tests/modules/test_permission_registry.py` 的守卫会断言本文件的键集合与
 * 后端枚举**完全一致**，加了一档而忘了改这里 → 闸门 1 失败。
 */

/** 后端 `DocumentStatus` 的取值（不放行未知字符串，页面必须显式处理）。 */
export const DOCUMENT_STATUSES = [
  'DRAFT',
  'SUBMITTED',
  'APPROVED',
  'REJECTED',
  'CANCELLED',
  'PAID',
] as const

export type DocumentStatus = (typeof DOCUMENT_STATUSES)[number]

export interface StatusMeta {
  /** 界面文案。⚠️ 已提交对外说「待审核」而不是「已提交」——审核员看到的就是这个动作。 */
  text: string
  /** design token 名（docs/06 §1），**不带 `var()` 与 `--`**，由组件拼成 `var(--color-xxx)`。 */
  colorToken: string
  /** 已作废要加删除线（docs/06 §1 表）。 */
  strikeThrough: boolean
}

/**
 * 状态 → 中文 / 色 token。
 *
 * 键类型用 `Record<DocumentStatus, StatusMeta>` 而不是 `Record<string, ...>`：
 * 后端加一档而这里没加 → **编译期报错**，而不是界面上默默显示空白标签。
 */
export const DOCUMENT_STATUS_META: Record<DocumentStatus, StatusMeta> = {
  DRAFT: { text: '草稿', colorToken: 'color-text-second', strikeThrough: false },
  SUBMITTED: { text: '待审核', colorToken: 'color-primary', strikeThrough: false },
  APPROVED: { text: '已审核', colorToken: 'color-success', strikeThrough: false },
  REJECTED: { text: '已驳回', colorToken: 'color-danger', strikeThrough: false },
  CANCELLED: { text: '已作废', colorToken: 'color-info', strikeThrough: true },
  PAID: { text: '已收款', colorToken: 'color-success', strikeThrough: false },
}

/** 取状态元信息；未知状态（后端加了新档而前端没跟上）返回 `null`，由界面显示原始值。 */
export function statusMeta(status: string): StatusMeta | null {
  const known = DOCUMENT_STATUSES.find((item) => item === status)
  return known === undefined ? null : DOCUMENT_STATUS_META[known]
}

/** 只取中文文案，未知状态原样返回（宁可显示 `FOO` 也不要空白）。 */
export function statusText(status: string): string {
  return statusMeta(status)?.text ?? status
}

/** 取色 token 的 CSS 变量值，例如 `var(--color-success)`。 */
export function statusColor(status: string): string {
  const token = statusMeta(status)?.colorToken
  return token === undefined ? 'var(--color-text-third)' : `var(--${token})`
}
