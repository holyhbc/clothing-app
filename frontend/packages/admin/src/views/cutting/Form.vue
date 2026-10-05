<script setup lang="ts">
/**
 * 裁剪单**新建页**（表头 + 三层明细一次提交，T-CUT-001c-3b）。
 *
 * ## 为什么是「一次提交」而不是「先建单再逐层 PUT」
 *
 * 后端的 `POST /cutting-orders` 就是这么设计的（`CuttingOrderCreateIn` 带
 * `lines[].colors[].size_lines[]`），而且**新建单上没有任何要保存的中间态价值** ——
 * 一张空的裁剪单对录入员没有意义，「建了一半的单」只会让人不知道哪些行已经进去了。
 * 逐层 PUT 的价值在**改**（保住已录内容），那是 T-CUT-001c-3c 编辑页的事。
 *
 * ## 表头必填与「不信任前端」
 *
 * - `workshop_id` / `style_id` / `doc_date` 必填；`doc_date` 默认今天。
 * - **表头五个汇总列（耗料 / 出数 / 裁损 / 尾数 / 手数）在入参里连字段都没有**：
 *   传了会被 `extra="forbid"` 挡下报 `10001`。所以这个表单**不能**让用户填合计 ——
 *   后端 C6 会重算，页面上显示的任何合计都只是「让用户对得上自己录入的」而已。
 * - ⚠️ `style_id` 要的是 **UUID** 而不是款号字符串：候选走 `searchStyleOptionsById`。
 *
 * ## 「按比例带出」为什么不在这一页
 *
 * 比例带出要调 `GET /{id}/suggest-lines`，而它需要 `order_id` —— 新建页上单据还不存在；
 * 且 `SuggestLinesOut.ratio` 是**小数**（如 1.5 手）而 `hands` 必须**整数**（ADR-0020），
 * 取整策略业务还没拍板（`docs/12` L-082）。所以新建页只给「自定义明细 / 统一件数」，
 * 带出在保存后的详情页（T-CUT-001c-3c）。**给一个做不到的选项比不给更糟**：
 * 用户填到一半才发现要保存重来。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Space,
  message,
} from 'ant-design-vue'
import { PERM } from '@garment/shared'
import type { CuttingOrderCreateIn, OrderLineIn, StockBatchOptionOut } from '@garment/shared'
import { createCuttingOrder, searchStockBatchOptions } from '@/api/cutting'
import PageLayout from '@/components/PageLayout.vue'
import HeaderFields from './components/HeaderFields.vue'
import type { HeaderFields as HeaderValue } from './components/HeaderFields.vue'
import LineEditor from './components/LineEditor.vue'
import { balanceOf, lineOutputOf } from './components/slotTypes'
import type { LineTree } from './components/slotTypes'

defineOptions({ name: 'CuttingOrderForm' })

const router = useRouter()

const header = ref<HeaderValue>({
  workshop_id: null,
  style_id: null,
  doc_date: dayjs().format('YYYY-MM-DD'),
  delivery_date: null,
  ply_count: 1,
  remark_source: '',
  remark: '',
})

const lines = ref<LineTree>([])
const submitting = ref(false)

/**
 * 提交前的整页问题清单。
 *
 * ⚠️ 「问题」与「提示」分开：这里列的都是**会阻断提交**的（必填没填、行余量为负），
 * 而「布料不够」只是提示 —— 后端会在 `40006` 里给出带数字的文案，页面上提前算的那一份
 * 可能与提交那一刻的库存不一致。
 */
const blocking = computed(() => {
  const list: string[] = []
  const head = header.value
  if (head.workshop_id === null) list.push('车间必填（C2）')
  if (head.style_id === null) list.push('款号必填')
  if (head.doc_date === '') list.push('单据日期必填')
  if (head.ply_count < 1) list.push('铺布层数必须 ≥ 1（C4）')
  if (lines.value.length === 0) list.push('至少要有一个布批行')
  lines.value.forEach((line: LineTree[number], index: number) => {
    const at = `第 ${index + 1} 行`
    if (line.stock_id === '') list.push(`${at}：还没选布批`)
    if (Number(line.fabric_qty ?? 0) <= 0) list.push(`${at}：耗料必须大于 0`)
    if (line.colors.length === 0) list.push(`${at}：至少要有一个颜色`)
    // ⚠️ **行余量为负必须在这里阻断**（C5 / C34）。第一版只把它显示在
    //    `LineEditor` 的问题清单里，而那份清单**不参与提交判定** —— 于是提交按钮
    //    是可点的，用户填完三层的全部数据之后才被后端收 `30002`。
    //    「填满一屏再被拒」是最伤录入员心情的失败方式，所以放在提交前。
    const balance = balanceOf(line)
    if (balance < 0) {
      list.push(`${at}：行可出件数比尺码明细合计少 ${-balance}，提交后端会收 30002`)
    }
    line.colors.forEach((color, colorIndex: number) => {
      if (color.color_code === '') list.push(`${at} 第 ${colorIndex + 1} 色：还没选色码`)
      color.size_lines.forEach((row, sizeIndex: number) => {
        const at2 = `${at} 第 ${colorIndex + 1} 色 第 ${sizeIndex + 1} 行`
        if (row.size_code === '') list.push(`${at2}：还没选尺码`)
        if (row.hands < 1) list.push(`${at2}：手数必须 ≥ 1`)
        if (row.qty_per_hand < 1) list.push(`${at2}：每手件数必须 ≥ 1`)
      })
    })
  })
  return list
})

/**
 * 提交体。
 *
 * ⚠️ **逐字段挑，不直接把表单对象整个丢进去**：后端是 `extra="forbid"`，
 * 多一个键就是 `10001`。用 `{...header}` + 删字段那种写法则是「下次表单加个字段就
 * 悄悄发错」。
 */
function buildPayload(): CuttingOrderCreateIn {
  const head = header.value
  return {
    workshop_id: head.workshop_id ?? '',
    style_id: head.style_id ?? '',
    doc_date: head.doc_date,
    delivery_date: head.delivery_date,
    ply_count: head.ply_count,
    // ⚠️ 表头默认模式取**第一个颜色**的模式：真正生效的是颜色级 `entry_mode`
    //    （C26），表头这个字段只是新建颜色时的默认值 —— 两者写不一致会让
    //    详情页上「默认模式」与实际录入的模式看起来矛盾。
    entry_mode_default: headerFirstMode(),
    remark_source: head.remark_source === '' ? null : head.remark_source,
    remark: head.remark === '' ? null : head.remark,
    lines: lines.value.map(normalizeLine),
  }
}

function headerFirstMode(): 'MASTER' | 'UNIFORM' | 'MANUAL' {
  return lines.value[0]?.colors[0]?.entry_mode ?? 'MANUAL'
}

/**
 * 行 → 请求体。
 *
 * ⚠️ **空的可选字段一律转 `null` 而不是空串**：后端 `str | None` 收到空串会
 * 过 Pydantic 校验、然后被当成「填了空串」存进库 —— 于是这一列永远显示空白，
 * 而录入员看不出哪里不对。
 */
function normalizeLine(line: LineTree[number]): OrderLineIn {
  return {
    ...line,
    width_cm: null,
    color_plan: null,
    remark: null,
    colors: line.colors.map((color) => ({
      ...color,
      qty_per_hand: null,
      uniform_qty: color.uniform_qty ?? null,
      size_lines: color.size_lines.map((row) => ({
        ...row,
        output_qty: row.output_qty ?? null,
        hands_seq: null,
        remark: row.remark === '' ? null : (row.remark ?? null),
      })),
    })),
  }
}

async function submit(): Promise<void> {
  if (blocking.value.length > 0) {
    message.error(`还有 ${blocking.value.length} 处没填好：${blocking.value[0]}`)
    return
  }
  submitting.value = true
  try {
    const created = await createCuttingOrder(buildPayload())
    message.success(`已保存草稿 ${created.doc_no}`)
    await router.push({ name: 'cutting-orders-detail', params: { orderId: created.id } })
  } catch (error) {
    // ⚠️ 只展示后端给的 message：10001（参数）/ 20001（布批不存在）/ 40006（布不够）
    //    三种的后端文案都说清了原因与下一步，自己拼一句「保存失败」等于把信息扔掉
    message.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    submitting.value = false
  }
}

function cancel(): void {
  void router.push({ name: 'cutting-orders' })
}

/** 布批候选：按关键字搜缸号 / 匹号（BR-ST-17 ①：裁剪最常见的是人工指定批次）。 */
function fetchStockOptions(keyword: string): Promise<StockBatchOptionOut[]> {
  return searchStockBatchOptions({ q: keyword })
}

const summary = computed(() => {
  const fabric = lines.value.reduce((t: number, l) => t + Number(l.fabric_qty ?? 0), 0)
  const waste = lines.value.reduce((t: number, l) => t + Number(l.waste_qty ?? 0), 0)
  const output = lines.value.reduce((t: number, l) => t + lineOutputOf(l), 0)
  return { fabric, waste, output }
})
</script>

<template>
  <PageLayout title="新建裁剪单" description="表头与三层明细一次提交，保存为草稿">
    <template #extra>
      <Space>
        <Button @click="cancel">取消</Button>
        <Button
          v-can="PERM.CUTTING_CREATE"
          type="primary"
          :loading="submitting"
          :disabled="blocking.length > 0"
          @click="submit"
        >
          保存草稿
        </Button>
      </Space>
    </template>

    <Card size="small" title="表头" style="margin-bottom: var(--space-4)">
      <HeaderFields v-model="header" />

      <Descriptions size="small" :column="3" style="margin-top: var(--space-3)">
        <DescriptionsItem label="耗料合计">{{ summary.fabric }} m</DescriptionsItem>
        <DescriptionsItem label="布头/布损合计">{{ summary.waste }} m</DescriptionsItem>
        <DescriptionsItem label="尺码出数合计">{{ summary.output }} 件</DescriptionsItem>
      </Descriptions>
      <small class="hint">
        ⚠️ 这三个数只是**让用户对得上自己录入的**；落库时由后端 C6 重算覆盖（C6 不信任前端），
        表头汇总五列**不接受传入**
      </small>
    </Card>

    <Card size="small" title="三层明细（布批行 → 颜色 → 尺码明细）">
      <LineEditor
        v-model="lines"
        :ply-count="header.ply_count"
        :fetch-stock-options="fetchStockOptions"
      />
    </Card>

    <Alert v-if="blocking.length > 0" class="block-alert" type="warning" show-icon>
      <div v-for="(problem, i) in blocking" :key="i" class="block-line">{{ problem }}</div>
    </Alert>
  </PageLayout>
</template>

<style scoped>
.hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.block-alert {
  margin-top: var(--space-3);
}

.block-line {
  font-size: var(--font-size-sm);
}
</style>