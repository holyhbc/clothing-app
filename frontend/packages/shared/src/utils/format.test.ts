/**
 * 格式化工具的测试（docs/10 §6：工具函数必须测金额、日期、枚举中文映射，
 * 且 `formatMoney` **必须**测 `0.1 + 0.2` 场景）。
 *
 * ⚠️ 这里断言的是**字符串输出**，不是"差不多对"：`¥0.30` 与 `¥0.3` 在界面上
 * 长得不一样，用户会以为金额变了。
 */

import { describe, expect, it } from 'vitest'

import {
  formatDate,
  formatDateTime,
  formatMoney,
  formatQty,
  formatRatio,
  formatUnitPrice,
} from './format.ts'

describe('formatMoney', () => {
  it('TC-W01：0.1 + 0.2 必须显示 ¥0.30 而不是 ¥0.30000000000000004', () => {
    expect(formatMoney(0.1 + 0.2)).toBe('¥0.30')
  })

  it('TC-W02：字符串入参（后端序列化成字符串）+ 千分位', () => {
    expect(formatMoney('1234.5000')).toBe('¥1,234.50')
    expect(formatMoney('1234567.891')).toBe('¥1,234,567.89')
  })

  it('负数：负号在前，不参与千分位分组', () => {
    expect(formatMoney('-1234.5')).toBe('-¥1,234.50')
  })

  it('小数位可指定（工价按 6 位、单价原样展示）', () => {
    expect(formatMoney('1234.5678', 4)).toBe('¥1,234.5678')
    expect(formatMoney('0.378000', 6)).toBe('¥0.378000')
  })

  it('空值返回横杠而不是 "¥NaN" 或 "¥0.00"', () => {
    // ⚠️ 这两种错法都出现过：界面上一片 ¥NaN，比显示 "-" 糟糕得多
    expect(formatMoney(null)).toBe('-')
    expect(formatMoney(undefined)).toBe('-')
    expect(formatMoney('')).toBe('-')
    expect(formatMoney('  ')).toBe('-')
    expect(formatMoney('abc')).toBe('-')
    expect(formatMoney(Number.NaN)).toBe('-')
    expect(formatMoney(Number.POSITIVE_INFINITY)).toBe('-')
  })

  it('0 显示 ¥0.00 —— 金额为 0 是合法业务值，不能当成"无数据"', () => {
    expect(formatMoney(0)).toBe('¥0.00')
    expect(formatMoney('0.000000')).toBe('¥0.00')
  })

  it('大额不带科学计数法', () => {
    expect(formatMoney(1234567890123)).toBe('¥1,234,567,890,123.00')
  })
})

describe('formatQty', () => {
  it('默认 3 位小数（numeric(14,3)），不带货币符号', () => {
    expect(formatQty('12')).toBe('12.000')
    expect(formatQty(12, 0)).toBe('12')
    expect(formatQty('12.5')).toBe('12.500')
  })

  it('formatUnitPrice 固定 6 位（numeric(12,6)）', () => {
    expect(formatUnitPrice('0.35')).toBe('0.350000')
    expect(formatUnitPrice(0.378)).toBe('0.378000')
  })

  it('formatRatio 固定 4 位（numeric(14,4)）', () => {
    expect(formatRatio('1.5')).toBe('1.5000')
  })

  it('负数保留符号', () => {
    expect(formatQty('-3.25')).toBe('-3.250')
  })
})

describe('formatDateTime', () => {
  it('TC-W03：带时区的 ISO8601 → 工厂当地时间', () => {
    expect(formatDateTime('2026-08-15T10:30:00+08:00')).toBe('2026-08-15 10:30:00')
  })

  it('UTC 输入按 Asia/Shanghai 换算（+8），不是显示 UTC', () => {
    // ⚠️ 这条是本函数存在的理由：直接取浏览器本地字段会在跨时区部署时全错
    expect(formatDateTime('2026-08-15T02:30:00Z')).toBe('2026-08-15 10:30:00')
  })

  it('跨日：UTC 前一天晚上 = 上海次日凌晨', () => {
    expect(formatDateTime('2026-08-14T20:00:00Z')).toBe('2026-08-15 04:00:00')
  })

  it('可省略秒 / 只取日期', () => {
    expect(formatDateTime('2026-08-15T10:30:45+08:00', { withSeconds: false })).toBe(
      '2026-08-15 10:30',
    )
    expect(formatDate('2026-08-15T10:30:45+08:00')).toBe('2026-08-15')
    expect(formatDateTime('2026-08-15T10:30:45+08:00', { withDate: false })).toBe('10:30:45')
  })

  it('非法输入返回横杠而不是 "Invalid Date"', () => {
    expect(formatDateTime(null)).toBe('-')
    expect(formatDateTime(undefined)).toBe('-')
    expect(formatDateTime('')).toBe('-')
    expect(formatDateTime('not-a-date')).toBe('-')
  })
})
