<script setup lang="ts">
/**
 * 裁剪单的第一层：**布批行**（耗料记在行，出数是正向录入的估算值）。
 *
 * ## 为什么行里**不**再自己画颜色与尺码
 *
 * 第二、三层在 :file:`./ColorEditor.vue` 里。拆两个组件而不是一个的理由不是「文件太长」
 * ——而是**改动的频率不同**：行这一层换的是**选料与耗料**（跟布批库存、门幅、可用量
 * 打交道），颜色与尺码那一层换的是**录入口径**（三种模式、手数与件数）。
 * 两边的字段、校验、错误码来源都不一样，塞在一个文件里时「选料相关」与「录入相关」
 * 的注释会互相打断，读的人要同时装两套概念。
 *
 * ## 三个口径
 *
 * | 口径 | 要求 | 依据 |
 * | --- | --- | --- |
 * | C38 | 布批**不允许自由输入缸号**，必须从候选里选（且候选只列 `available_qty > 0`）；`fabric_qty > available_qty` → `40006` | C38 / ADR-0022 |
 * | C35 | 耗料是**布的属性**，正向录入，**不由出数反推** | C35 |
 * | C34 | 行 `output_qty` 是用户按铺布实耗的**估算**（正向录入），差值即**行余量**；小于尺码合计 → 阻断提交（否则后端 `30002`） | C34 口径 A |
 *
 * ⚠️ **已选布批的详情存在本地 `Map`**：`<Combo>` 只回 `value`（批次 UUID），不回
 * 你选中的那一项；而门幅与可用量**只在候选里有**。不存下来的话界面只能显示一个
 * UUID，用户看不到「这匹布还剩多少米」，而 `40006` 要等提交后才收。
 */
import { computed } from 'vue'
import { Button, InputNumber, Space, Table, Tag } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { formatQty } from '@garment/shared'
import type { StockBatchOptionOut } from '@garment/shared'
import Combo from '@/components/Combo.vue'
import { asLine, balanceOf, num } from './slotTypes'
import type { LineColorTree, LineTree } from './slotTypes'

import ColorEditor from './ColorEditor.vue'

const props = defineProps<{
  modelValue: LineTree
  /** 铺布层数（C4）。> 1 时出数列必须标注「含 N 层」。 */
  plyCount: number
  /** 布批候选（按关键字搜缸号 / 匹号）。`value` 是**批次 UUID**。 */
  fetchStockOptions: (keyword: string) => Promise<StockBatchOptionOut[]>
}>()

const emit = defineEmits<{ 'update:modelValue': [value: LineTree] }>()

const MAX_LINES = 200

/**
 * 批次 UUID → 候选详情。
 *
 * ⚠️ 由 :func:`fetchAndRemember` 在**候选返回时**填，而不是在「选中时」填 ——
 * 因为 `<Combo>` 的 `update:modelValue` **只回 value（UUID）**，不回你选中的那一项
 * （`Combo.vue` 的注释写得很明确：回显走 `resolveLabel`）。想「选中时记下详情」
 * 就得改 Combo 的契约，而那会让十个调用点都得改。
 *
 * ⚠️ 用 `Map` 而不是普通对象：`Map.has` 不会撞上 `Object.prototype` 上的
 * `toString` / `constructor`，且它在重渲染之间保持同一个引用。
 */
const picked = new Map<string, StockBatchOptionOut>()

/** 包一层：把返回的候选缓存起来，之后 `stockOf()` 才查得到门幅与可用量。 */
async function fetchAndRemember(keyword: string): Promise<StockBatchOptionOut[]> {
  const options = await props.fetchStockOptions(keyword)
  for (const option of options) picked.set(option.value, option)
  return options
}

/**
 * 取一行（`record` 是 `Record<string, any>`，这里做唯一一次断言）。
 *
 * ⚠️ 断言集中在这一个函数里是有意的：模板里其余地方要么读 `LineTree` 的字段
 * （类型已收紧），要么走 {@link setFabric} 这类收窄过的写入函数。
 */
function lineOf(raw: unknown): LineTree[number] {
  return asLine(raw) as LineTree[number]
}

/** `<Combo>` 的 `model-value` 要 `string | null`：空串必须转 `null`，否则 Combo 显示成「已选了空值」。 */
function stockIdText(line: LineTree[number]): string | null {
  return line.stock_id === '' ? null : line.stock_id
}

function stockOf(stockId: unknown): StockBatchOptionOut | undefined {
  return typeof stockId === 'string' && stockId !== '' ? picked.get(stockId) : undefined
}

/** 不可变的更新入口：改完整棵树再 emit，避免多处直接改数组元素。 */
function update(next: LineTree): void {
  emit('update:modelValue', next)
}

// ------------------------------------------------------------------ 行增删改与排序

function addLine(): void {
  if (props.modelValue.length >= MAX_LINES) return
  update([
    ...props.modelValue,
    {
      line_no: props.modelValue.length + 1,
      stock_id: '',
      fabric_qty: '0',
      waste_qty: '0',
      output_qty: '0',
      colors: [],
    },
  ])
}

/**
 * 重排 `line_no`。
 *
 * ⚠️ **必须重排**：删掉中间一行之后，后面的号会留下空洞（1, 3, 4），
 * 而后端的三层业务唯一键之一是 `UNIQUE (doc_id, line_no)` —— 空洞不冲突，
 * 但**页面上「行号 3」紧跟在「行 1」后面**会让录入员以为漏了一行，
 * 而且详情页按 `line_no` 排序时会出现跳号。留着空洞只是把问题推给下一个人。
 */
function reindexLine(line: LineTree[number], index: number): LineTree[number] {
  return { ...line, line_no: index + 1 }
}

function removeLine(index: number): void {
  const kept = props.modelValue.filter((_line, i) => i !== index)
  update(kept.map((line, position) => reindexLine(line, position)))
}

function patchLine(index: number, patch: Partial<LineTree[number]>): void {
  update(props.modelValue.map((line, i) => (i === index ? { ...line, ...patch } : line)))
}

function setColors(index: number, colors: LineColorTree[]): void {
  patchLine(index, { colors })
}

/** `v-model` 的 emit 类型与 prop 声明的收紧类型必须一致，否则 vue-tsc 会报参数不兼容。 */


/** 上下移动用**按钮**而不是拖拽：1366×768 的表格密度下拖拽命中区太小（工厂用户戴手套点不准）。 */
function moveLine(index: number, delta: number): void {
  const target = index + delta
  if (target < 0 || target >= props.modelValue.length) return
  const next = [...props.modelValue]
  // ⚠️ `splice` 的返回值是「被删掉的那个」，TS 在 `noUncheckedIndexedAccess` 下认为它
  //    **可能不存在** —— 而这里 index 已由上两行夹在 `[0, length)` 里，所以是安全的。
  //    写成 `as LineTree[number]` 是断言，所以改用「删完再取目标位置」这种不依赖断言的写法。
  next.splice(index, 1)
  const moved = next[target]
  if (moved === undefined) return
  next.splice(target, 0, moved)
  update(next.map((line, position) => reindexLine(line, position)))
}

function patchStock(index: number, stockId: unknown): void {
  patchLine(index, { stock_id: typeof stockId === 'string' ? stockId : '' })
}

/** 耗料 / 布损 / 行可出件数：`null`（用户清空输入）按 0 存，而不是存 `NaN` 或 `''`。 */
function setFabric(index: number, value: number | null): void {
  patchLine(index, { fabric_qty: String(value ?? 0) })
}

function setWaste(index: number, value: number | null): void {
  patchLine(index, { waste_qty: String(value ?? 0) })
}

function setOutput(index: number, value: number | null): void {
  patchLine(index, { output_qty: String(value ?? 0) })
}

// ------------------------------------------------------------------ 合计与校验

// ⚠️ **合计与行余量的算术全在 `./slotTypes`**（`lineOutputOf` / `balanceOf`）——
//    本组件、颜色编辑器与新建页三处都要用，而三份实现意味着三处可能不一致：
//    页面上「行余量」那一格显示 10、旁边的问题清单说「少 10」，用户完全无法判断
//    哪个是真的。`TC-CUT-F2` 就是因为这里曾经把**行对象**当成尺码行做过乘法
//    （`undefined * undefined` → NaN → `NaN < 0` 为 false → 阻断静默失效）而写的。
const balances = computed(() => props.modelValue.map(balanceOf))

/** 布料够不够（C38 / `40006`）：提交前先说，别等后端收。 */
function fabricOver(line: LineTree[number]): string | null {
  const stock = stockOf(line.stock_id)
  if (stock === undefined) return null
  const need = Number(line.fabric_qty ?? 0)
  const available = Number(stock.available_qty)
  return need > available ? `可用 ${formatQty(stock.available_qty, 3)} 米，不够` : null
}

const problems = computed(() => {
  const list: string[] = []
  if (props.modelValue.length === 0) list.push('至少要有一个布批行')
  props.modelValue.forEach((line, index) => {
    const at = `第 ${index + 1} 行`
    if (line.stock_id === '') list.push(`${at}：还没选布批`)
    if (Number(line.fabric_qty ?? 0) <= 0) list.push(`${at}：耗料必须大于 0`)
    if (line.colors.length === 0) list.push(`${at}：至少要有一个颜色（一床可多色，C32）`)
    if (balanceOf(line) < 0) {
      list.push(`${at}：行可出件数比尺码明细合计少 ${-balanceOf(line)}，提交后端会收 30002`)
    }
  })
  return list
})

defineExpose({ problems, balances })

const lineColumns: ColumnsType<LineTree[number]> = [
  { key: 'line_no', title: '行', dataIndex: 'line_no', width: 48 },
  { key: 'stock', title: '布批（缸号/匹号 · 门幅 · 可用量）', width: 400 },
  { key: 'fabric_qty', title: '本次耗料(m)', width: 116 },
  { key: 'waste_qty', title: '布头/布损(m)', width: 116 },
  { key: 'output_qty', title: '行可出件数', width: 116 },
  { key: 'balance', title: '行余量', width: 84 },
  { key: 'colors', title: '颜色 → 尺码明细' },
  { key: 'ops', title: '操作', width: 150, fixed: 'right' },
]
</script>

<template>
  <div class="line-editor">
    <Space direction="vertical" size="small" style="width: 100%">
      <Space>
        <Button type="primary" ghost @click="addLine">添一行布批</Button>
        <span class="hint">
          已 {{ modelValue.length }} / {{ MAX_LINES }} 行 ·
          {{ plyCount > 1 ? `出数为「含 ${plyCount} 层」的总件数（C4）` : '单层铺布' }}
        </span>
      </Space>

      <Table
        :columns="lineColumns"
        :data-source="modelValue"
        :row-key="(row: LineTree[number], index?: number) => row.stock_id || `new-${index}`"
        size="small"
        :pagination="false"
        :scroll="{ x: 1180 }"
      >
        <!--
          ⚠️ antd 的 `record` 是 `Record<string, any>`。**读**它不报错（那是 `any` 的
          固有性质），而**写**一律走本文件里的处理函数 —— 那些函数收窄 `unknown` 之后
          才动手。这么分是因为「出口收紧」比「进来处处校验」便宜：新增一行时构造的
          对象是字面量，多一个键 `vue-tsc` 就会报（见 `addLine`）。
        -->
        <template #bodyCell="{ column, record, index }">
          <template v-if="column.key === 'stock'">
            <Space direction="vertical" size="small" style="width: 100%">
              <Combo
                :model-value="stockIdText(lineOf(record))"
                :fetch-options="fetchAndRemember"
                placeholder="按缸号 / 匹号搜布批"
                @update:model-value="(value: string | null) => patchStock(index, value ?? '')"
              />
              <!--
                ⚠️ 选完之后**主动记下候选详情**：Combo 只回 value（UUID），
                门幅与可用量不在回显里 —— 不显示的话用户看不出这匹布还剩多少米。
              -->
              <span v-if="stockOf(lineOf(record).stock_id)" class="hint">
                缸号 {{ stockOf(lineOf(record).stock_id)?.dye_lot_no }} / 匹
                {{ stockOf(lineOf(record).stock_id)?.bolt_no }} · 门幅
                {{ formatQty(stockOf(lineOf(record).stock_id)?.width_cm, 2) }} cm · 可用
                {{ formatQty(stockOf(lineOf(record).stock_id)?.available_qty, 3) }} m
                <!-- BR-ST-25：返修布（REWORK_RECEIPT）可正常领用但成本走 5403，标出来 -->
                <Tag v-if="stockOf(lineOf(record).stock_id)?.purpose !== 'NORMAL'" color="orange">
                  {{ stockOf(lineOf(record).stock_id)?.purpose }}
                </Tag>
              </span>
              <span v-else-if="fabricOver(lineOf(record))" class="over">
                {{ fabricOver(lineOf(record)) }}
              </span>
            </Space>
          </template>

          <template v-else-if="column.key === 'fabric_qty'">
            <Space direction="vertical" size="small" style="width: 100%">
              <InputNumber
                :value="Number(lineOf(record).fabric_qty ?? 0)"
                :min="0.001"
                :precision="3"
                :step="0.5"
                style="width: 100%"
                @change="
                  (value: string | number | null) =>
                    setFabric(index, num(value))
                "
              />
              <span v-if="fabricOver(lineOf(record))" class="over">
                {{ fabricOver(lineOf(record)) }}
              </span>
            </Space>
          </template>

          <template v-else-if="column.key === 'waste_qty'">
            <InputNumber
              :value="Number(lineOf(record).waste_qty ?? 0)"
              :min="0"
              :precision="3"
              style="width: 100%"
              @change="
                (value: string | number | null) => setWaste(index, num(value))
              "
            />
          </template>

          <template v-else-if="column.key === 'output_qty'">
            <InputNumber
              :value="Number(lineOf(record).output_qty ?? 0)"
              :min="0"
              :precision="0"
              style="width: 100%"
              @change="
                (value: string | number | null) => setOutput(index, num(value))
              "
            />
          </template>

          <template v-else-if="column.key === 'balance'">
            <span :class="(balances[index] ?? 0) < 0 ? 'over' : 'balance'">
              {{ balances[index] ?? 0 }}
            </span>
          </template>

          <template v-else-if="column.key === 'colors'">
            <ColorEditor
              :model-value="lineOf(record).colors"
              @update:model-value="(next: LineColorTree[]) => setColors(index, next)"
            />
          </template>

          <template v-else-if="column.key === 'ops'">
            <Space>
              <Button size="small" :disabled="index === 0" @click="moveLine(index, -1)">
                上移
              </Button>
              <Button
                size="small"
                :disabled="index === modelValue.length - 1"
                @click="moveLine(index, 1)"
              >
                下移
              </Button>
              <Button size="small" danger type="link" @click="removeLine(index)">移除</Button>
            </Space>
          </template>
        </template>
      </Table>

      <ul v-if="problems.length > 0" class="problems">
        <li v-for="(problem, i) in problems" :key="i">{{ problem }}</li>
      </ul>
    </Space>
  </div>
</template>

<style scoped>
.hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

/* ⚠️ 「不够 / 会报错」用警示色（docs/06 §1：状态色固定，其余语义色同理）。 */
.over {
  font-size: var(--font-size-sm);
  color: var(--color-danger);
}

.balance {
  font-variant-numeric: tabular-nums;
  color: var(--color-text-second);
}

.problems {
  margin: 0;
  padding-left: var(--space-4);
  font-size: var(--font-size-sm);
  color: var(--color-danger);
}
</style>