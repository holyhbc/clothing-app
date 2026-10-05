<script setup lang="ts">
/**
 * 裁剪单的第二、三层：**行内颜色 → 尺码明细**（ADR-0017 一床多色）。
 *
 * ## 为什么第二、三层是一个组件
 *
 * 改一条尺码明细的**手数**会立刻改变该颜色的件数小计，而颜色小计又是行余量的输入 ——
 * 这条链上任何一个环节不在同一个组件里，就必须做「向上通知、向下回填」的双向同步，
 * 而漏一处的表现是**界面上的合计与自己看到的行对不上，且没有任何报错**。
 *
 * ## 三个必须守住的口径
 *
 * | 口径 | 要求 | 依据 |
 * | --- | --- | --- |
 * | C26 | 录入模式是**颜色级**属性：同一行的不同颜色可以用不同模式，**完全允许** | C26 / C32 |
 * | C27 | 切走模式会清空**该行该颜色**的尺码明细 → 必须二次确认并**写明会清掉几行** | C27 |
 * | C25 | **手数可编辑（正整数）、件数只读自动算**（= 手数 × 每手件数）。禁止让用户直接录件数 | C25 / C21 |
 *
 * ⚠️ **MASTER（按比例带出）不给**：它要调 `GET /{id}/suggest-lines`，而那需要
 * `order_id` —— 新建页上单据还不存在。更要紧的是 `SuggestLinesOut.ratio` 是**小数**
 * （如 1.5 手）而 `hands` 必须**整数**（ADR-0020），取整策略业务尚未拍板
 * （`docs/12` L-082）。宁可不给这个选项，也不自己发明一条取整规则。
 */
import { computed } from 'vue'
import { Button, Input, InputNumber, Modal, Select, Space, Table, Tag, Tooltip } from 'ant-design-vue'
import { message } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import type { SizeLineIn } from '@garment/shared'
import { baseApi } from '@/api/base'
import Combo from '@/components/Combo.vue'
import { asColor, asSize, colorOutputOf, num, sizeOutputOf } from './slotTypes'
import type { LineColorTree } from './slotTypes'

const props = defineProps<{ modelValue: LineColorTree[] }>()

const emit = defineEmits<{ 'update:modelValue': [value: LineColorTree[]] }>()

const MAX_COLORS = 10
const MAX_SIZE_LINES = 50

const ENTRY_MODES: readonly { value: string; label: string }[] = [
  { value: 'MANUAL', label: '自定义明细' },
  { value: 'UNIFORM', label: '统一件数' },
]

function update(next: LineColorTree[]): void {
  emit('update:modelValue', next)
}

function addColor(): void {
  if (props.modelValue.length >= MAX_COLORS) {
    message.warning(`一行最多 ${MAX_COLORS} 个颜色（后端上限）`)
    return
  }
  update([...props.modelValue, { color_code: '', entry_mode: 'MANUAL', size_lines: [] }])
}

function removeColor(colorIndex: number): void {
  update(props.modelValue.filter((_color, i) => i !== colorIndex))
}

function patchColor(colorIndex: number, patch: Partial<LineColorTree>): void {
  update(
    props.modelValue.map((color, i) => (i === colorIndex ? { ...color, ...patch } : color)),
  )
}

// ------------------------------------------------------------------ 录入模式（C26 / C27）

/**
 * 切走模式 → **清空该颜色的尺码明细**，但必须先确认且**说清会清掉几行**。
 *
 * ⚠️ 用 `Modal.confirm` 而不是原生 `confirm()`：`docs/06 §5` 明令禁止原生弹窗
 * （它们不认 `message.error` 的常驻与可关闭，也没法统一措辞）。
 *
 * ⚠️ 确认框里**必须带数量**：只问「确定要清空吗」的话，用户点确定之后就丢了
 * 半小时的录入量，而界面上没有任何痕迹能找回。
 *
 * ⚠️ 只影响**该行该颜色**（C27 原文）。所以 patch 只针对 `colorIndex`，
 * 不碰同一行的其它颜色 —— 这是最容易写错的一处。
 */
function onModeChange(colorIndex: number, raw: unknown): void {
  // ⚠️ 收窄成生成类型里的**枚举**而不是 `string`：直接 `mode as EntryMode` 是断言，
  //    而 Select 传什么我们不控制（改版换个 value 就是 10001）。
  const mode = typeof raw === 'string' && raw !== '' ? raw : 'MANUAL'
  const color = props.modelValue[colorIndex]
  if (color === undefined || mode === color.entry_mode) return
  const affected = color.size_lines.length
  const apply = (): void =>
    patchColor(colorIndex, { entry_mode: mode as LineColorTree['entry_mode'], size_lines: [] })

  if (affected === 0) {
    apply()
    return
  }
  Modal.confirm({
    title: '切换录入模式会清空已录入的手数',
    content: `该颜色已有 ${affected} 行尺码明细，切换后**手数与件数全部清空**（只影响该行该颜色）。`,
    okText: '清空并切换',
    cancelText: '再想想',
    onOk: apply,
  })
}

// ------------------------------------------------------------------ 尺码明细（第三层）

function addSizeLine(colorIndex: number): void {
  const color = props.modelValue[colorIndex]
  if (color === undefined) return
  if (color.size_lines.length >= MAX_SIZE_LINES) {
    message.warning(`一个颜色最多 ${MAX_SIZE_LINES} 行尺码明细（后端上限）`)
    return
  }
  patchColor(colorIndex, {
    size_lines: [
      ...color.size_lines,
      { size_line_no: color.size_lines.length + 1, size_code: '', hands: 1, qty_per_hand: 1 },
    ],
  })
}

function removeSizeLine(colorIndex: number, sizeIndex: number): void {
  const color = props.modelValue[colorIndex]
  if (color === undefined) return
  patchColor(colorIndex, { size_lines: color.size_lines.filter((_s, i) => i !== sizeIndex) })
}

function patchSizeLine(
  colorIndex: number,
  sizeIndex: number,
  patch: Partial<SizeLineIn>,
): void {
  const color = props.modelValue[colorIndex]
  if (color === undefined) return
  patchColor(colorIndex, {
    size_lines: color.size_lines.map((row, i) => (i === sizeIndex ? { ...row, ...patch } : row)),
  })
}

// ------------------------------------------------------------------ 合计

function colorCodeText(raw: unknown): string | null {
  const code = asColor(raw).color_code
  return code === '' ? null : code
}

function sizeCodeText(raw: unknown): string | null {
  const code = asSize(raw).size_code
  return code === '' ? null : code
}

/** 件数列的 tooltip：说明这个数**从哪来**（C25 要求件数只读，但用户有权知道为什么）。 */
function outputTitle(raw: unknown): string {
  return asSize(raw).output_qty === undefined ? '手数 × 每手件数' : '人工指定过件数'
}

function handsSum(color: LineColorTree): number {
  return color.size_lines.reduce((total, row) => total + row.hands, 0)
}

/** 该颜色所有行的问题（页面用来汇总整页提示，而不是逐格飘红）。 */
const colorProblems = computed(() =>
  props.modelValue.flatMap((color, colorIndex) => {
    const at = `第 ${colorIndex + 1} 色`
    const list: string[] = []
    if (color.color_code === '') list.push(`${at}：还没选色码`)
    color.size_lines.forEach((row, sizeIndex) => {
      const at2 = `${at} 第 ${sizeIndex + 1} 行`
      if (row.size_code === '') list.push(`${at2}：还没选尺码`)
      if (row.hands < 1) list.push(`${at2}：手数必须 ≥ 1（整数，C13）`)
      if (row.qty_per_hand < 1) list.push(`${at2}：每手件数必须 ≥ 1（整数，C21）`)
    })
    return list
  }),
)

defineExpose({ colorProblems })

const colorColumns: ColumnsType<LineColorTree> = [
  { key: 'color_code', title: '色码', width: 200 },
  { key: 'entry_mode', title: '录入模式', width: 190 },
  { key: 'summary', title: '小计（手数 / 件数）', width: 130 },
  { key: 'size_lines', title: '尺码明细（手数可编辑，件数只读）' },
  { key: 'ops', title: '操作', width: 80 },
]

const sizeColumns: ColumnsType<SizeLineIn> = [
  { key: 'size_line_no', title: '行号', dataIndex: 'size_line_no', width: 56 },
  { key: 'size_code', title: '尺码', width: 160 },
  { key: 'hands', title: '手数', width: 96 },
  { key: 'qty_per_hand', title: '每手件数', width: 96 },
  { key: 'output', title: '件数（只读）', width: 110 },
  { key: 'remark', title: '备注', width: 150 },
  { key: 'ops', title: '', width: 70 },
]
</script>

<template>
  <div class="color-editor">
    <!--
      ⚠️ `layer-colors` 这个 class 是**给测试按层级取 DOM 用的**（与只读详情页
      同一手法）：三层是嵌套的，而空的尺码子表**不一定渲染 tbody**，
      所以「按 tbody 下标算层级」会错位、只看得到第一行的颜色（用例假绿）。
    -->
    <Table
      class="layer-colors"
      :columns="colorColumns"
      :data-source="modelValue"
      :row-key="(row: LineColorTree, index?: number) => row.color_code || `c-${index}`"
      size="small"
      :pagination="false"
    >
      <!--
        ⚠️ `record` 是 `Record<string, any>`（antd 的插槽类型）：读不报错，
        写一律走 `patchXxx(index, ...)` —— 那些函数的参数已收紧。见 `./slotTypes.ts`。
      -->
      <template #bodyCell="{ column, record: rawColor, index }">
        <template v-if="column.key === 'color_code'">
          <Combo
            :model-value="colorCodeText(rawColor)"
            :fetch-options="baseApi['colors'].options"
            placeholder="选色码"
            @update:model-value="(value: string | null) => patchColor(index, { color_code: value ?? '' })"
          />
        </template>

        <template v-else-if="column.key === 'entry_mode'">
          <Space direction="vertical" size="small" style="width: 100%">
            <Select
              :value="asColor(rawColor).entry_mode"
              style="width: 100%"
              :options="[...ENTRY_MODES]"
              @change="(value: unknown) => onModeChange(index, value)"
            />
            <!--
              B 档「统一件数」：填一个数，所有尺码出数相同。⚠️ 它存在**颜色**上而不是
              表头（`entry_mode_default` 只是新建颜色时的默认值，真正生效的是这里）。
            -->
            <InputNumber
              v-if="asColor(rawColor).entry_mode === 'UNIFORM'"
              :value="Number(asColor(rawColor).uniform_qty ?? 0)"
              :min="0"
              :precision="0"
              placeholder="统一件数"
              style="width: 100%"
              @change="
                (value: string | number | null) =>
                  patchColor(index, { uniform_qty: String(num(value) ?? 0) })
              "
            />
          </Space>
        </template>

        <template v-else-if="column.key === 'summary'">
          <span>{{ handsSum(asColor(rawColor)) }} 手 / {{ colorOutputOf(asColor(rawColor)) }} 件</span>
        </template>

        <template v-else-if="column.key === 'size_lines'">
          <Space direction="vertical" size="small" style="width: 100%">
            <Table
              class="layer-sizes"
              :columns="sizeColumns"
              :data-source="asColor(rawColor).size_lines"
              :row-key="(row: SizeLineIn, sizeIndex?: number) => row.size_code || `s-${sizeIndex}`"
              size="small"
              :pagination="false"
            >
              <template #bodyCell="{ column: leaf, record: rawSize, index: sizeIndex }">
                <template v-if="leaf.key === 'size_code'">
                  <Combo
                    :model-value="sizeCodeText(rawSize)"
                    :fetch-options="baseApi['sizes'].options"
                    placeholder="选尺码"
                    @update:model-value="
                      (value: string | null) =>
                        patchSizeLine(index, sizeIndex, { size_code: value ?? '' })
                    "
                  />
                </template>

                <template v-else-if="leaf.key === 'hands'">
                  <InputNumber
                    :value="asSize(rawSize).hands"
                    :min="1"
                    :precision="0"
                    style="width: 100%"
                    @change="
                      (value: string | number | null) =>
                        patchSizeLine(index, sizeIndex, { hands: Math.trunc(num(value) ?? 0) })
                    "
                  />
                </template>

                <template v-else-if="leaf.key === 'qty_per_hand'">
                  <InputNumber
                    :value="asSize(rawSize).qty_per_hand"
                    :min="1"
                    :precision="0"
                    style="width: 100%"
                    @change="
                      (value: string | number | null) =>
                        patchSizeLine(index, sizeIndex, { qty_per_hand: Math.trunc(num(value) ?? 0) })
                    "
                  />
                </template>

                <template v-else-if="leaf.key === 'output'">
                  <!-- C25：件数**只读**（手数 × 每手件数）；人工指定的行要标出来（C28） -->
                  <Tooltip :title="outputTitle(rawSize)">
                    <span>
                      {{ sizeOutputOf(asSize(rawSize)) }}
                      <Tag v-if="asSize(rawSize).output_qty !== undefined" color="orange">人工</Tag>
                    </span>
                  </Tooltip>
                </template>

                <template v-else-if="leaf.key === 'remark'">
                  <Input
                    :value="asSize(rawSize).remark ?? ''"
                    placeholder="可选"
                    @change="
                      (event: Event) =>
                        patchSizeLine(index, sizeIndex, {
                          remark: (event.target as HTMLInputElement).value,
                        })
                    "
                  />
                </template>

                <template v-else-if="leaf.key === 'ops'">
                  <Button
                    size="small"
                    danger
                    type="link"
                    @click="removeSizeLine(index, sizeIndex)"
                  >
                    移除
                  </Button>
                </template>
              </template>
            </Table>
            <Button size="small" @click="addSizeLine(index)">加尺码行</Button>
          </Space>
        </template>

        <template v-else-if="column.key === 'ops'">
          <Button size="small" danger type="link" @click="removeColor(index)">移除</Button>
        </template>
      </template>
    </Table>

    <Button type="primary" ghost size="small" @click="addColor">加颜色</Button>
    <ul v-if="colorProblems.length > 0" class="problems">
      <li v-for="(problem, i) in colorProblems" :key="i">{{ problem }}</li>
    </ul>
  </div>
</template>

<style scoped>
.problems {
  margin: var(--space-2) 0 0;
  padding-left: var(--space-4);
  font-size: var(--font-size-sm);
  color: var(--color-danger);
}
</style>