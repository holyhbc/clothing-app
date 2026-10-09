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
  AvailableOutputOut,
} from '@garment/shared'
import {
  createBundlingOrder,
  listAvailableOutputs,
  searchStyleOptionsById,
} from '@/api/bundling'
import { baseApi } from '@/api/base'
import PageLayout from '@/components/PageLayout.vue'
import HeaderFields from './components/HeaderFields.vue'
import type { HeaderFieldsValue } from './components/HeaderFields.vue'
import LineEditor from './components/LineEditor.vue'

defineOptions({ name: 'BundlingOrderForm' })

const router = useRouter()

const header = ref<HeaderFieldsValue>({
  source_cutting_order_id: null,
  style_id: null,
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
const availableSources = ref<AvailableOutputOut[]>([])
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
  if (head.style_id === null) list.push('款号必填')
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
    if (line.planned_qty !== undefined && line.planned_qty < 1) list.push(`${at}：计划件数必须 ≥ 1`)
  })
  return list
})

function buildPayload(): BundlingOrderCreateIn {
  const head = header.value
  return {
    workshop_id: head.workshop_id ?? '',
    style_no: head.style_id ?? '',
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
      color_code: line.color_code,
      operation_no: line.operation_no,
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

async function onSourceCuttingOrderChange(): Promise<void> {
  if (!header.value.source_cutting_order_id || !header.value.style_id || !header.value.color_code) return
  loadingSources.value = true
  try {
    availableSources.value = await listAvailableOutputs(header.value.source_cutting_order_id, {
      style_no: header.value.style_id,
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

async function loadStyleOptions(): Promise<void> {
  try {
    const opts = await searchStyleOptionsById('')
    styleOptions.value = opts.map((o) => ({ value: o.value, label: o.label }))
  } catch {
    styleOptions.value = []
  }
}

async function loadOperationOptions(): Promise<void> {
  try {
    operationOptions.value = (await baseApi.operations.optionsById('')).map((o) => ({
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
        :available-sources="availableSources"
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
