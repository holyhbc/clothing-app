/**
 * 计件流水台账的展示口径（从 `List.vue` 抽出，ADR-0030「单文件 ≤ 400 行」）。
 *
 * ⚠️ 流水类型（`piecework_logs.log_type`，ADR-0005）**不是文档状态**，
 *    所以走本文件的中文映射，**不进** `StatusTag`（那是给单据状态用的）。
 */
import type { PieceworkLogRowOut } from '@garment/shared'

export const LOG_TYPE_LABEL: Record<string, { text: string; color: string }> = {
  SCAN: { text: '扫码', color: 'green' },
  MANUAL: { text: '手工补录', color: 'blue' },
  SUPPLEMENT: { text: '补录', color: 'cyan' },
  REVERSAL: { text: '红冲', color: 'red' },
}

export const LOG_TYPE_OPTIONS = (Object.keys(LOG_TYPE_LABEL) as string[]).map((value) => ({
  value,
  label: LOG_TYPE_LABEL[value]!.text,
}))

/** 员工列：`工号 姓名`；缺工号时只显示姓名，缺姓名时显示破折号。 */
export function employeeText(row: PieceworkLogRowOut): string {
  const name = row.employee_name ?? '—'
  return row.employee_no !== null && row.employee_no !== undefined
    ? `${row.employee_no} ${name}`
    : name
}

/** 第几手列：`第 X/Y 手`；缺总数时只显示 `第 X 手`，缺手数显示破折号。 */
export function handsText(row: PieceworkLogRowOut): string {
  if (row.hands === null || row.hands === undefined) return '—'
  const total = row.hands_total_of_size
  return total === null || total === undefined ? `第 ${row.hands} 手` : `第 ${row.hands}/${total} 手`
}
