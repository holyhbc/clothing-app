/**
 * 金额 / 数量 / 日期格式化（docs/03 §2.1 第 8 条：**统一封装，禁止手写 `toFixed`**）。
 *
 * 为什么必须集中：同一笔金额在列表页显示 `¥0.30`、在详情页显示 `¥0.3`、
 * 在导出里显示 `0.300000` —— 这种不一致用户看得见，而且会怀疑数据错了。
 * 散落的 `toFixed(2)` 迟早会长出第二种写法。
 *
 * ⚠️ **入参接受 `string`**：docs/05 §3 规定后端把金额 / 数量 / 单价序列化成字符串
 * （JS 的 `number` 表示不了 `0.378000`），所以前端拿到的就是 `'0.378000'`。
 * 用 `parseFloat` 会丢精度，所以这里走 `Number()` 再按小数位量化。
 */

/** 业务时区（docs/11：PG 与后端统一 `Asia/Shanghai`）。 */
export const BUSINESS_TIMEZONE = 'Asia/Shanghai'

const YUAN = '¥'

/** 量化到指定小数位（避免 `0.1 + 0.2` 直接 `toFixed` 出来的 `0.30` 之外的长尾）。 */
function quantize(value: number, places: number): number {
  const factor = 10 ** places
  // ⚠️ `Math.round(x * f) / f` 在 |x| 很大且精度接近 f 时仍可能有 1ulp 误差，
  // 所以再加一层 `Number(E)` 归一 —— TC-W01（`0.1 + 0.2`）就依赖这一步。
  return Math.round((value + Number.EPSILON * Math.sign(value || 1)) * factor) / factor
}

/** 解析入参：`string | number | null | undefined` → `number | null`。 */
function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const trimmed = value.trim()
  if (trimmed === '') return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

/** 千分位分隔。负号在前，不参与分组。 */
function group(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/**
 * 格式化金额：`¥1,234.50`。
 *
 * @param value 金额（字符串或数字；`null` / `undefined` / 空串 → `-`）
 * @param places 小数位，默认 2（docs/05 §3：金额响应一律字符串，界面统一 2 位）
 *
 * @example formatMoney(0.1 + 0.2)          // '¥0.30'   ← TC-W01
 * @example formatMoney('1234.5000')       // '¥1,234.50' ← TC-W02
 * @example formatMoney('1234.5678', 4)    // '¥1,234.5678'
 * @example formatMoney(null)              // '-'
 */
export function formatMoney(value: string | number | null | undefined, places = 2): string {
  const parsed = toNumber(value)
  if (parsed === null) return '-'
  const fixed = quantize(parsed, places).toFixed(places)
  const negative = fixed.startsWith('-')
  const [intPart = '', fracPart] = (negative ? fixed.slice(1) : fixed).split('.')
  const grouped = group(intPart)
  const body = fracPart === undefined ? grouped : `${grouped}.${fracPart}`
  return `${negative ? '-' : ''}${YUAN}${body}`
}

/**
 * 格式化数量（件数 / 手数 / 米数）。
 *
 * 与 `formatMoney` 的区别：**不带货币符号**，且默认 3 位小数
 * （docs/04 §7.5：`bundle_qty numeric(14,3)`、`hands` 为整数）。
 * 整数数量传 0 位小数即可（`formatQty(12, 0)` → `'12'`）。
 */
export function formatQty(value: string | number | null | undefined, places = 3): string {
  const parsed = toNumber(value)
  if (parsed === null) return '-'
  const fixed = quantize(parsed, places).toFixed(places)
  const negative = fixed.startsWith('-')
  const [intPart = '', fracPart] = (negative ? fixed.slice(1) : fixed).split('.')
  const grouped = group(intPart)
  const body = fracPart === undefined ? grouped : `${grouped}.${fracPart}`
  return `${negative ? '-' : ''}${body}`
}

/** 工价：单价按 6 位精度展示（docs/04 §4 `numeric(12,6)`），不带货币符号。 */
export function formatUnitPrice(value: string | number | null | undefined): string {
  return formatQty(value, 6)
}

/** 手数比例：`1.5000`（docs/04 §7.7.1 `ratio numeric(14,4)`）。 */
export function formatRatio(value: string | number | null | undefined): string {
  return formatQty(value, 4)
}

function pad(value: number, width: number): string {
  return String(value).padStart(width, '0')
}

interface DateParts {
  year: number
  month: number
  day: number
  hour: number
  minute: number
  second: number
}

function parts(iso: string): DateParts | null {
  // ⚠️ 用 `Date.parse` 而不是手写正则拆时间：ISO8601 带时区时浏览器/D8 的解析
  // 行为是标准化的，手写拆反而会漏掉 `Z` 结尾与毫秒。
  const parsed = new Date(iso)
  const stamp = parsed.getTime()
  if (Number.isNaN(stamp)) return null
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: BUSINESS_TIMEZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
  // `en-CA` 的输出是 `YYYY-MM-DD, HH:MM:SS`，直接切
  const [datePart = '', timePart = ''] = formatter.format(parsed).split(', ')
  const [year = '0', month = '01', day = '01'] = datePart.split('-')
  const [hour = '00', minute = '00', second = '00'] = timePart.split(':')
  // ⚠️ 必须转成**具名接口**而不是 `Record<string, number>`：后者在
  // `noUncheckedIndexedAccess` 下每个字段都变成 `number | undefined`，
  // 于是 pad() 的入参全部要断言，断言一多就等于关掉了类型检查
  return {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour: Number(hour),
    minute: Number(minute),
    second: Number(second),
  }
}

/**
 * 格式化日期时间：`2026-08-15 10:30:00`（工厂当地时间，docs/03 §2.1 第 8 条）。
 *
 * ⚠️ 后端存的是 **timestamptz**，序列化成带时区的 ISO8601（docs/05 §3）。
 * 前端**不做** `new Date()` 后直接取本地字段 —— 那会显示浏览者所在时区的时间，
 * 而工厂在别的时区时每个数字都错。
 *
 * @param iso 带时区的 ISO8601 字符串
 * @param withSeconds 是否带秒（列表页一般不带，省视觉噪声）
 * @param withDate 是否带日期（详情页通常都要）
 */
export function formatDateTime(
  iso: string | null | undefined,
  options: { withSeconds?: boolean; withDate?: boolean } = {},
): string {
  if (!iso) return '-'
  const parsed = parts(iso)
  if (parsed === null) return '-'
  const { withSeconds = true, withDate = true } = options
  const date = `${parsed.year}-${pad(parsed.month, 2)}-${pad(parsed.day, 2)}`
  const clock = `${pad(parsed.hour, 2)}:${pad(parsed.minute, 2)}`
  const time = withSeconds ? `${clock}:${pad(parsed.second, 2)}` : clock
  return withDate ? `${date} ${time}` : time
}

/** 只取日期：`2026-08-15`。 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-'
  // ⚠️ 直连 parts() 而不再走 formatDateTime(...).split(' ')[0]：
  // 那条写法里 `[0]` 在类型上是 `string | undefined`，逼我写一个 `?? '-'` 兜底，
  // 而该分支**永远不可达**（formatDateTime 带日期时要么返回完整串、要么返回 '-'）——
  // 不可达的兜底就是纯噪声，还拉低分支覆盖率。
  const parsed = parts(iso)
  if (parsed === null) return '-'
  return `${parsed.year}-${pad(parsed.month, 2)}-${pad(parsed.day, 2)}`
}
