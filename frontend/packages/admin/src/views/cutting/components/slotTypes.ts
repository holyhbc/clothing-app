/**
 * 裁剪三层树的**收紧类型**与共享小工具（T-CUT-001c-3a）。
 *
 * ## 收紧类型：为什么不用生成类型直接编辑
 *
 * 生成类型里 ``colors`` / ``size_lines`` 是**可选**的（模型带 ``default_factory``
 * → OpenAPI 的 ``required`` 里没有它们）。拿它做可编辑的表单状态，整页到处要写
 * ``?? []`` —— 而那些 `?? []` 掩盖的其实是「这里本来就不该为空」：空数组在
 * 「还没录入」和「录漏了」两种情况下长得一模一样。
 *
 * 所以这里用 ``Omit`` + 重新声明把它们**收紧成必填**（``docs/06 §7`` 允许的组合，
 * **不新写接口**）。出口是收紧的，构造新行时就必须是字面量 —— 多一个键
 * `vue-tsc` 会报，少一个必填键也会报（见 ``LineEditor.addLine``）。
 *
 * ## `asLine` / `asColor` / `asSize`：断言集中在一处
 *
 * antd 的 ``#bodyCell`` 插槽把 ``record`` 声明成 ``Record<string, any>``。
 * 模板里**读**它不报错（``any`` 的固有性质），但那等于关掉了这个文件里所有行的类型
 * 检查 —— 字段名打错不会有任何提示，运行时是 ``undefined``，界面表现是
 * 「这一列空白且不报错」。
 *
 * 而 ``record as OrderLineIn`` 是**断言**：它对编译器说「我保证形状对」，而实际保证的
 * 只有「这个 record 是我们自己塞进去的」。把这句话收进这三个函数之后，
 * 「唯一的断言在哪」有明确答案，其余代码都是收窄。
 */
import type { LineColorIn, OrderLineIn, SizeLineIn } from '@garment/shared'

/** 页面上编辑的颜色：``size_lines`` **必填**。 */
export type LineColorTree = Omit<LineColorIn, 'size_lines'> & { size_lines: SizeLineIn[] }

/** 页面上编辑的行：``colors`` **必填**。 */
export type LineTree = (Omit<OrderLineIn, 'colors'> & { colors: LineColorTree[] })[]

/** 非对象一律返回空壳而不是抛错 —— 插槽在数据为空时也可能被调。 */
export function asLine(raw: unknown): LineTree[number] {
  return typeof raw === 'object' && raw !== null ? (raw as LineTree[number]) : ({} as never)
}

/** 同上，行内颜色。 */
export function asColor(raw: unknown): LineColorTree {
  return typeof raw === 'object' && raw !== null ? (raw as LineColorTree) : ({} as never)
}

/** 同上，尺码明细。 */
export function asSize(raw: unknown): SizeLineIn {
  return typeof raw === 'object' && raw !== null ? (raw as SizeLineIn) : ({} as never)
}

/**
 * antd `InputNumber` 的 `@change` 值是 `ValueType`（`string | number | null`），
 * **不是** `number | null` —— 直接把处理函数写成 `(value: number | null)` 会在
 * `vue-tsc` 下报参数不兼容，而在 vitest 里**不会**（不跑类型检查），于是
 * 「本地测试全绿、闸门 2 红」会让人怀疑测试是不是白写了。
 *
 * ⚠️ 收窄成 `number` 之后才用：``Number('abc')`` 是 `NaN`，直接 `String(NaN)` 存进去
 * 后端会收 10001，而报错里看不出是前端存了个 NaN。
 */
export function num(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * 一条尺码明细的出数（C21 / C25 / C28）。
 *
 * ⚠️ **精确乘、不取整**（ADR-0020）：`hands` 与 `qty_per_hand` 都是整数，乘出来必然是
 * 整数。这里**没有** `Math.floor` —— 加一个 `floor` 会在有人把 `hands` 改成小数时
 * 悄悄抹掉 0.9，而正确行为是让后端收 `30006`（C13）。
 *
 * ⚠️ `output_qty` 被人工指定过时**优先于**乘积（C28），所以它排在前面。
 */
export function sizeOutputOf(row: SizeLineIn): number {
  return row.output_qty ?? row.hands * row.qty_per_hand
}

/** 一个行内颜色的尺码出数合计。 */
export function colorOutputOf(color: LineColorTree): number {
  return color.size_lines.reduce((total, row) => total + sizeOutputOf(row), 0)
}

/**
 * 一行（布批行）的尺码出数合计 = Σ(颜色 Σ尺码 output_qty)。
 *
 * ⚠️ **尺码明细是出数的权威来源**（C5）。行上的 ``output_qty`` 是「正向录入的估算」，
 *    拿它当这一层的合计 —— 那正是「行余量恒为 0」的来源。
 */
export function lineOutputOf(line: LineTree[number]): number {
  return line.colors.reduce((total, color) => total + colorOutputOf(color), 0)
}

/**
 * 行余量 = 行 output_qty − Σ(颜色 Σ尺码 output_qty)（C34 口径 A）。
 *
 * ⚠️ 为负 → 后端收 ``30002``。前端必须在**提交前**阻断，而不是让用户填满一屏
 * 之后才被拒。
 *
 * ⚠️ 这里**必须**调 :func:`lineOutputOf` 而不是对行对象直接做乘法：行上没有
 * `hands` / `qty_per_hand`，`undefined * undefined` 是 `NaN`，而
 * `NaN < 0` 是 **false** —— 阻断条件会静默失效（第一版就这么写错过一次，
 * 是 TC-CUT-F2 抓出来的）。
 */
export function balanceOf(line: LineTree[number]): number {
  return Number(line.output_qty ?? 0) - lineOutputOf(line)
}
