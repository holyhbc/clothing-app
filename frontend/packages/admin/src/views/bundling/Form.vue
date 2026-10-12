<script setup lang="ts">
/**
 * 打菲单新建页（T-BUND-009）。
 *
 * 表头 + 明细一次提交；从 `available-outputs` 拉来源行（显示 `hands`/`output_qty`/每手件数预览/缸号匹号）。
 * 明细行用 `OrderedSizeItems` 展示 `hands`/`bundle_qty`/`planned_qty`/`output_qty`/`balance_qty`。
 *
 * ⚠️ 表头汇总不接受传入（`hands_total` / `output_qty` / `balance_qty` / `planned_qty`）：
 * 模块/03 §4 明确「差异由 service 重算并覆盖入参，不信任前端」。
 */
import { computed, onMounted, ref } from 'vue'
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
import type {
  BundlingOrderCreateIn,
  LineIn,
  SourceOrderOut,
  SourceLineOut,
} from '@garment/shared'
import { createBundlingOrder, listSourceLines, listSourceOrders } from '@/api/bundling'
import { baseApi } from '@/api/base'
import { searchStyleOptions } from '@/api/styles'
import PageLayout from '@/components/PageLayout.vue'
import HeaderFields from './components/HeaderFields.vue'
import type { HeaderFieldsValue } from './components/HeaderFields.vue'
import LineEditor from './components/LineEditor.vue'

defineOptions({ name: 'BundlingOrderForm' })

const router = useRouter()

const header = ref<HeaderFieldsValue>({
  source_cutting_order_id: null,
  style_no: '',
  doc_date: dayjs().format('YYYY-MM-DD'),
  bundle_qty: 1,
  color_code: '',
  color_group: '',
  operation_no: '',
  workshop_id: null,
  remark: '',
})

const lines = ref<LineIn[]>([])
const submitting = ref(false)
const loadingSources = ref(false)
const sourceOrders = ref<SourceOrderOut[]>([])
const availableSources = ref<SourceLineOut[]>([])
const sourceOptions = computed(() =>
  availableSources.value.map((src) => ({
    value: src.cutting_size_line_id,
    label: `${src.size_code} · 手数:${src.hands} · 出数:${src.output_qty} · 每手:${src.qty_per_hand}`,
  })),
)

const blocking = computed(() => {
  const list: string[] = []
  const head = header.value
  if (head.workshop_id === null) list.push('车间必填')
  if (head.style_no === '') list.push('款号必填')
  if (head.doc_date === '') list.push('单据日期必填')
  if (head.operation_no === '') list.push('工序必填')
  if (head.color_code === '') list.push('色码必填')
  if (head.color_group === '') list.push('色组必填')
  if (head.source_cutting_order_id === null) list.push('来源裁剪单必填')
  if (lines.value.length === 0) list.push('至少要有一行明细（尺码）')
  lines.value.forEach((line: LineIn, index: number) => {
    const at = `第 ${index + 1} 行`
    if (line.size_code === '') list.push(`${at}：还没选尺码`)
    if (line.cutting_size_line_id === '') list.push(`${at}：还没选来源裁剪明细行`)
    if (line.hands < 1) list.push(`${at}：手数必须 ≥ 1（整数，ADR-0020）`)
    // ⚠️ 必须同时排除 `null` 与 `undefined`：`null < 1` 在 JS 里是 **true**
    // （null 转 0），只判 `!== undefined` 会把 `planned_qty: null`
    // 误判成「计划件数必须 ≥ 1」。而 `null` 恰恰是 `addLine()` 新建行的默认值
    // —— 后端也明确定义为 `int | None = None`（「预览用，服务端重算」），
    // 本就该放过。于是每一行刚建出来就红着，**表单根本提交不了**。
    // （写成 `!= null` 更短，但 eslint `eqeqeq` 不让，故显式两条。）
    const planned = line.planned_qty
    if (planned !== undefined && planned !== null && planned < 1) {
      list.push(`${at}：计划件数必须 ≥ 1`)
    }
  })
  return list
})

/**
 * 组提交体。
 *
 * ⚠️ **`style_no` 要的是款号字符串，不是 `styles.id`（UUID）**（`BundlingOrderCreateIn`
 *   的字段名就是 `style_no`，`max_length=32`）。传 UUID 时后端 422
 *   `string_too_long`（36 > 32）—— 实测 E2E 就死在这儿，页面上只弹一句
 *   「参数校验失败」，完全看不出「款号选对了但传错了类型」。
 *   所以表单里存的就是款号本身，候选走 `searchStyleOptions`（`value` = 款号，
 *   05 §9.5.2），**不是**裁剪那条链路用的 `searchStyleOptionsById`（`value` = UUID）。
 *
 * ⚠️ **明细行的 `color_code` / `operation_no` 由表头下发，不由行自己填**：
 *   `LineEditor` 里这两个字段既没有输入框（新建行恒为 `''`），后端 schema 也写明
 *   是「行级冗余，与表头一致」。留着空串落库的话，审核生成的每个 `bundles` 都会
 *   带着空色码 —— 直接违反 B2/B3（一码一色），标签上印出来的也是空的。
 */
function buildPayload(): BundlingOrderCreateIn {
  const head = header.value
  return {
    workshop_id: head.workshop_id ?? '',
    style_no: head.style_no,
    doc_date: head.doc_date,
    bundle_qty: head.bundle_qty,
    color_code: head.color_code,
    color_group: head.color_group,
    operation_no: head.operation_no,
    source_cutting_order_id: head.source_cutting_order_id ?? '',
    remark: head.remark === '' ? null : head.remark,
    lines: lines.value.map((line) => ({
      line_no: line.line_no,
      size_code: line.size_code,
      color_code: head.color_code,
      operation_no: head.operation_no,
      cutting_size_line_id: line.cutting_size_line_id,
      hands: line.hands,
      planned_qty: line.planned_qty ?? null,
      group_no: line.group_no ?? null,
      workstation_no: line.workstation_no ?? null,
      remark: line.remark === '' ? null : (line.remark ?? null),
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
    const created = await createBundlingOrder(buildPayload())
    message.success(`已保存草稿 ${created.doc_no}`)
    await router.push({ name: 'bundling-orders-detail', params: { orderId: created.id } })
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    submitting.value = false
  }
}

function cancel(): void {
  void router.push({ name: 'bundling-orders' })
}

/**
 * 候选来源裁剪单：款号 + 车间定了就列（B8 + 03 §5.1「只列 APPROVED」）。
 *
 * ⚠️ 不按色筛裁剪单本身 —— 色码挂在「裁剪尺码明细行」上（ADR-0016 一码一色），
 * 裁剪单层面没有「这张单是什么色」这回事，按色筛会把有该色明细的单整体漏掉。
 */
async function loadSourceOrders(): Promise<void> {
  const styleNo = header.value.style_no
  if (!styleNo) {
    sourceOrders.value = []
    return
  }
  loadingSources.value = true
  try {
    sourceOrders.value = await listSourceOrders({
      style_no: styleNo,
      ...(header.value.workshop_id ? { workshop_id: header.value.workshop_id } : {}),
    })
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载来源裁剪单失败')
    sourceOrders.value = []
  } finally {
    loadingSources.value = false
  }
}

/**
 * 选定来源裁剪单后，拉该色下各尺码的可打菲来源明细（填明细行的下拉）。
 *
 * ⚠️ 旧实现在这里是**循环依赖**：它要求 `source_cutting_order_id` 已存在才去加载，
 * 而那个字段正是这个下拉要选的 —— 于是下拉永远空着，整页无法新建。
 * 且它调的是 `available-outputs`（以**已有打菲单**为根），新建时压根没有打菲单 id。
 */
async function onSourceCuttingOrderChange(): Promise<void> {
  // 车间/款号变了 → 候选裁剪单要重列；没到选来源那一步就先把明细来源清空
  await loadSourceOrders()
  const styleNo = header.value.style_no
  if (!header.value.source_cutting_order_id || !styleNo || !header.value.color_code) {
    availableSources.value = []
    return
  }
  loadingSources.value = true
  try {
    availableSources.value = await listSourceLines({
      cutting_order_id: header.value.source_cutting_order_id,
      style_no: styleNo,
      color_code: header.value.color_code,
    })
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载来源明细失败')
    availableSources.value = []
  } finally {
    loadingSources.value = false
  }
}

const styleOptions = ref<{ value: string; label: string }[]>([])
const operationOptions = ref<{ value: string; label: string }[]>([])
const workshopOptions = ref<{ value: string; label: string }[]>([])

/**
 * 款号候选：`value` = 款号**字符串**（`/styles/options`，05 §9.5.2）。
 *
 * ⚠️ **不要用裁剪那条链路的 `searchStyleOptionsById`**：那个的 `value` 是
 * `styles.id`（UUID），而打菲单入参字段叫 `style_no`（`max_length=32`）——
 * 传 UUID 会被 422 挡下。两者只差一个「ById」，选错就是建不出单。
 */
async function loadStyleOptions(): Promise<void> {
  try {
    const opts = await searchStyleOptions('')
    styleOptions.value = opts.map((o) => ({ value: o.value, label: o.label }))
  } catch {
    styleOptions.value = []
  }
}

/**
 * 工序候选：`value` = **工序号**（`/operations/options`，05 §9.5.2「`value` 是业务编码」）。
 *
 * ⚠️ **不能用 `optionsById`**：那个的 `value` 是 `operations.id`（UUID），
 * 而 `BundlingOrderCreateIn.operation_no` 是 `max_length=16` 的工序号字符串。
 * `workshops.optionsById()` 是对的（`workshop_id` 本来就要 UUID）——
 * 一张表里两个字段一个要 UUID 一个要编码，正好是最容易抄错的那种地方。
 */
async function loadOperationOptions(): Promise<void> {
  try {
    operationOptions.value = (await baseApi.operations.options('')).map((o) => ({
      value: o.value,
      label: o.label,
    }))
  } catch {
    operationOptions.value = []
  }
}

async function loadWorkshopOptions(): Promise<void> {
  try {
    workshopOptions.value = (await baseApi.workshops.optionsById()).map((o) => ({
      value: o.value,
      label: o.label,
    }))
  } catch {
    workshopOptions.value = []
  }
}

const summary = computed(() => {
  const totalHands = lines.value.reduce((t: number, l) => t + (l.hands ?? 0), 0)
  const totalPlanned = lines.value.reduce((t: number, l) => t + (l.planned_qty ?? 0), 0)
  return { totalHands, totalPlanned }
})

onMounted(() => {
  void loadStyleOptions()
  void loadOperationOptions()
  void loadWorkshopOptions()
})
</script>

<template>
  <PageLayout title="新建打菲单" description="表头与明细一次提交，保存为草稿；从裁剪明细带出手数">
    <template #extra>
      <Space>
        <Button @click="cancel">取消</Button>
        <Button
          v-can="PERM.BUNDLING_CREATE"
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
      <HeaderFields
        v-model="header"
        :style-options="styleOptions"
        :operation-options="operationOptions"
        :workshop-options="workshopOptions"
        :source-orders="sourceOrders"
        :loading-sources="loadingSources"
        @source-change="onSourceCuttingOrderChange"
      />

      <Descriptions size="small" :column="3" style="margin-top: var(--space-3)">
        <DescriptionsItem label="明细行数">{{ lines.length }}</DescriptionsItem>
        <DescriptionsItem label="手数合计">{{ summary.totalHands }}</DescriptionsItem>
        <DescriptionsItem label="计划件数合计">{{ summary.totalPlanned }} 件</DescriptionsItem>
      </Descriptions>
      <small class="hint">
        ⚠️ 这三个数只是**让用户对得上自己录入的**；落库时由后端重算覆盖（模块/03 §4），
        表头汇总四列（`hands_total` / `output_qty` / `balance_qty` / `planned_qty`）**不接受传入**
      </small>
    </Card>

    <Card size="small" title="明细（每行 = 一尺码，必带来源裁剪尺码明细行）">
      <LineEditor
        v-model="lines"
        :source-options="sourceOptions"
        :loading-sources="loadingSources"
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
