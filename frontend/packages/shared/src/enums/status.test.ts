/**
 * 状态映射的测试（docs/10 §6「工具函数 / 格式化：必须测枚举中文映射」）。
 *
 * ⚠️ 这层映射错的表现很隐蔽：状态标签显示成英文码，用户以为系统出问题了，
 * 而后端返回的数据完全正确。所以逐个状态断言文案与色 token。
 */

import { describe, expect, it } from 'vitest'

import {
  DOCUMENT_STATUSES,
  DOCUMENT_STATUS_META,
  statusColor,
  statusMeta,
  statusText,
} from './status.ts'

describe('状态映射（docs/06 §1 固定表）', () => {
  it('每个状态都有元信息，且键集合与取值清单严格一致', () => {
    expect(Object.keys(DOCUMENT_STATUS_META).sort()).toEqual([...DOCUMENT_STATUSES].sort())
  })

  it('文案与色 token 逐条对齐', () => {
    expect(DOCUMENT_STATUS_META.DRAFT).toEqual({
      text: '草稿',
      colorToken: 'color-text-second',
      strikeThrough: false,
    })
    // ⚠️ 已提交对外说「待审核」：审核员看到的就是他要做的事
    expect(DOCUMENT_STATUS_META.SUBMITTED.text).toBe('待审核')
    expect(DOCUMENT_STATUS_META.APPROVED.text).toBe('已审核')
    expect(DOCUMENT_STATUS_META.REJECTED.text).toBe('已驳回')
    expect(DOCUMENT_STATUS_META.CANCELLED.text).toBe('已作废')
  })

  it('只有已作废加删除线（docs/06 §1 表）', () => {
    for (const status of DOCUMENT_STATUSES) {
      expect(DOCUMENT_STATUS_META[status].strikeThrough).toBe(status === 'CANCELLED')
    }
  })

  it('色 token 全部是 design token，不含字面量颜色', () => {
    // ⚠️ 组件里禁止出现 #1677ff / rgb(...) 这类字面量（docs/06 §1）
    for (const status of DOCUMENT_STATUSES) {
      expect(DOCUMENT_STATUS_META[status].colorToken).toMatch(/^color-[a-z-]+$/)
    }
  })

  it('statusText / statusColor / statusMeta 对未知状态降级而不是崩', () => {
    // ⚠️ 后端加一档而前端没跟上时，显示原始值比显示空白更容易排查
    expect(statusMeta('FOO')).toBeNull()
    expect(statusText('FOO')).toBe('FOO')
    expect(statusColor('FOO')).toBe('var(--color-text-third)')
    expect(statusColor('APPROVED')).toBe('var(--color-success)')
  })

  it('取不到元信息时颜色回落到中性色 token，不返回空串', () => {
    // 空串会让 style="color:" 变成无效声明，标签退化成继承色（可能与徽标底色撞）
    expect(statusColor('FOO')).not.toBe('')
  })
})
