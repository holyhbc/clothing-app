<script setup lang="ts">
/**
 * 裁剪单详情页 —— **只读**（T-CUT-001c-2）。
 *
 * ## 为什么这一版只读
 *
 * 三层的写接口全是**全量替换**（`PUT /lines` `PUT /colors` `PUT /size-lines`），
 * 页面必须处理两件事才敢开放编辑：
 *
 * 1. **版本链**：每次成功写之后要把响应里的新 `version` 存下来继续用，用旧的
 *    下一次就是 `10003`。而 `PUT` 三个接口都可能软删旧行 → 行 `id` 会变 → 三层
 *    都要按新结构重挂。
 * 2. **比例建议的副作用**：`GET /suggest-lines` 会把比例快照写进行内颜色，
 *    所以它必须绑在用户明确的「按比例带出」动作上，不能是「打开详情就拉一次」。
 *
 * 这两件事属于 T-CUT-001c-3。这一版先把三层结构**准确呈现**出来（行 → 颜色 →
 * 尺码，与 ADR-0017 一一对应），编辑能力在下一张卡接上。
 *
 * ⚠️ 页面**不**做任何汇总计算：表头五列与各层小计都由后端重算并返回（C6），
 * 前端再算一遍就是第二份真相（`modules/02 §2`）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Alert, Button, Descriptions, DescriptionsItem, Spin, Table, Tag } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { PERM, formatDateTime, formatQty } from '@garment/shared'
import type { CuttingOrderOut, LineColorOut, OrderLineOut, SizeLineOut } from '@garment/shared'
import { getCuttingOrder } from '@/api/cutting'
import PageLayout from '@/components/PageLayout.vue'
import StatusTag from '@/components/StatusTag.vue'

defineOptions({ name: 'CuttingOrderDetail' })

const route = useRoute()
const router = useRouter()

const order = ref<CuttingOrderOut | null>(null)
const loading = ref(false)
const errorMessage = ref('')

/**
 * 路由参数收窄。
 *
 * ⚠️ `route.params.orderId` 的类型是 `string | string[]`（vue-router 对可重复参数
 *   就是这个联合），而接口要 `string`。断言 `as string` 会把「参数缺失」变成
 *   「把 undefined 发给后端」，于是用户在详情页看到的是 10001 而不是「页面参数不对」。
 */
function orderIdOf(raw: unknown): string {
  const value = Array.isArray(raw) ? raw[0] : raw
  return typeof value === 'string' ? value : ''
}

const orderId = computed(() => orderIdOf(route.params['orderId']))

async function load(): Promise<void> {
  if (orderId.value === '') {
    errorMessage.value = '缺少裁剪单 ID（页面地址不对）'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    order.value = await getCuttingOrder(orderId.value)
  } catch (error) {
    // ⚠️ 只取 message 展示，不 `as ApiError` 再读 `code`：错误对象形状由 shared 的
    // 客户端保证，这里不需要它的额外字段，读错字段会掩盖真正的失败原因。
    errorMessage.value = error instanceof Error ? error.message : '加载失败'
    order.value = null
  } finally {
    loading.value = false
  }
}

/** 录入模式中文（C 档名字是业务词，页面必须给中文，见 `modules/02 §5`）。 */
const ENTRY_MODE_TEXT: Record<string, string> = {
  MASTER: '按比例带出',
  UNIFORM: '统一件数',
  CUSTOM: '自定义明细',
}

function entryModeText(mode: string): string {
  return ENTRY_MODE_TEXT[mode] ?? mode
}

const lineColumns: ColumnsType<OrderLineOut> = [
  { key: 'line_no', title: '行', dataIndex: 'line_no', width: 60 },
  { key: 'dye_lot_no', title: '缸号', dataIndex: 'dye_lot_no', width: 140 },
  { key: 'bolt_no', title: '布卷号', dataIndex: 'bolt_no', width: 140 },
  { key: 'width_cm', title: '门幅(cm)', dataIndex: 'width_cm', width: 100 },
  { key: 'fabric_qty', title: '用布量', dataIndex: 'fabric_qty', width: 110 },
  { key: 'waste_qty', title: '裁损', dataIndex: 'waste_qty', width: 100 },
  { key: 'output_qty', title: '录入出数', dataIndex: 'output_qty', width: 110 },
  { key: 'size_line_sum_qty', title: '尺码合计', dataIndex: 'size_line_sum_qty', width: 110 },
  { key: 'balance_qty', title: '行余量', dataIndex: 'balance_qty', width: 100 },
  { key: 'colors', title: '颜色 / 尺码明细' },
]

const colorColumns: ColumnsType<LineColorOut> = [
  { key: 'color_code', title: '色码', dataIndex: 'color_code', width: 90 },
  { key: 'entry_mode', title: '录入模式', dataIndex: 'entry_mode', width: 110 },
  { key: 'hands_total', title: '手数', dataIndex: 'hands_total', width: 90 },
  { key: 'output_qty_total', title: '出数', dataIndex: 'output_qty_total', width: 90 },
  { key: 'balance_qty_total', title: '余量', dataIndex: 'balance_qty_total', width: 90 },
  { key: 'size_lines', title: '尺码明细' },
]

const sizeColumns: ColumnsType<SizeLineOut> = [
  { key: 'size_line_no', title: '行号', dataIndex: 'size_line_no', width: 70 },
  { key: 'size_code', title: '尺码', dataIndex: 'size_code', width: 80 },
  { key: 'hands', title: '手数', dataIndex: 'hands', width: 80 },
  { key: 'qty_per_hand', title: '每手件数', dataIndex: 'qty_per_hand', width: 100 },
  { key: 'output_qty', title: '出数', dataIndex: 'output_qty', width: 90 },
  { key: 'output_qty_manual', title: '人工指定', dataIndex: 'output_qty_manual', width: 100 },
  { key: 'balance_qty', title: '余量', dataIndex: 'balance_qty', width: 90 },
]

/**
 * 第二层 / 第三层的取数。
 *
 * ⚠️ 后端出参里 `colors` / `size_lines` 是**可选**字段（有 `default_factory` 的
 * 模型在生成类型里就是 `T[] | undefined`），所以这里 `?? []` 而不是直接返回 ——
 * 直接返回的写法在「三层为空」的那一版数据上会得到 `undefined`，
 * 而 `:data-source="undefined"` 会被 antd 当成非数组，渲染出一张空表且不报错。
 *
 * ⚠️ 入参是 `unknown` + 收窄：antd 插槽给的 `record` 是 `Record<string, any>`，
 *   直接标注成 `OrderLineOut` 是断言（形状不对时炸在一个离根因很远的地方）。
 */
function colorsOf(raw: unknown): LineColorOut[] {
  if (typeof raw !== 'object' || raw === null || !('colors' in raw)) return []
  const colors = (raw as { colors?: unknown }).colors
  return Array.isArray(colors) ? (colors as LineColorOut[]) : []
}

/** 第三层：尺码明细（行内颜色的子表）。 */
function sizeLinesOf(raw: unknown): SizeLineOut[] {
  if (typeof raw !== 'object' || raw === null || !('size_lines' in raw)) return []
  const sizeLines = (raw as { size_lines?: unknown }).size_lines
  return Array.isArray(sizeLines) ? (sizeLines as SizeLineOut[]) : []
}

/**
 * 尾数 / 余量的展示口径。
 *
 * ⚠️ 只在 **> 0** 时高亮：这两列是「不足件 / 尾数」，为 0 是常态（正好吃满），
 * 满屏 0 会把真正要看的数字淹掉。
 */
function isPositive(value: string | null | undefined): boolean {
  return Number(value ?? 0) > 0
}

onMounted(() => {
  void load()
})

watch(orderId, () => {
  void load()
})
</script>

<template>
  <PageLayout :title="order?.doc_no ?? '裁剪单详情'">
    <template #extra>
      <Button @click="router.back()">返回</Button>
      <!--
        ⚠️ 审核 / 作废 / 删除按钮**刻意不放在这一版**：那是状态迁移
        （`docs/08`），要校验前置状态与记录原因，与只读详情不是同一件事。
      -->
      <Button
        v-can="PERM.CUTTING_UPDATE"
        type="primary"
        @click="router.push({ name: 'cutting-orders-edit', params: { orderId } })"
      >
        编辑
      </Button>
    </template>

    <Alert
      v-if="errorMessage !== ''"
      type="error"
      show-icon
      message="裁剪单加载失败"
      :description="errorMessage"
      style="margin-bottom: var(--space-4)"
    >
      <template #action>
        <Button size="small" @click="load()">重试</Button>
      </template>
    </Alert>

    <Spin :spinning="loading">
      <template v-if="order !== null">
        <Descriptions bordered size="small" :column="3" style="margin-bottom: var(--space-4)">
          <DescriptionsItem label="单据状态">
            <StatusTag :status="order.status" />
          </DescriptionsItem>
          <DescriptionsItem label="单据日期">{{ order.doc_date }}</DescriptionsItem>
          <DescriptionsItem label="交期">
            {{ order.delivery_date ?? '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="款号">{{ order.style_no }}</DescriptionsItem>
          <DescriptionsItem label="颜色">{{ order.color_codes || '—' }}</DescriptionsItem>
          <DescriptionsItem label="铺布层数">{{ order.ply_count }}</DescriptionsItem>
          <DescriptionsItem label="默认录入模式">
            {{ entryModeText(order.entry_mode_default) }}
          </DescriptionsItem>
          <DescriptionsItem label="用布量合计">
            {{ formatQty(order.fabric_qty, 3) }}
          </DescriptionsItem>
          <DescriptionsItem label="出数合计">{{ formatQty(order.output_qty, 0) }}</DescriptionsItem>
          <DescriptionsItem label="裁损合计">{{ formatQty(order.cut_waste_qty, 3) }}</DescriptionsItem>
          <DescriptionsItem label="尾数">
            <span :class="{ 'cell-warn': isPositive(order.balance_qty) }">
              {{ formatQty(order.balance_qty, 0) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="计件手数合计">{{ order.hands_total }}</DescriptionsItem>
          <DescriptionsItem label="版本">{{ order.version }}</DescriptionsItem>
          <DescriptionsItem label="创建时间">
            {{ formatDateTime(order.created_at) }}
          </DescriptionsItem>
          <DescriptionsItem label="更新时间">
            {{ formatDateTime(order.updated_at) }}
          </DescriptionsItem>
          <DescriptionsItem label="备注" :span="3">{{ order.remark ?? '—' }}</DescriptionsItem>
          <DescriptionsItem v-if="order.remark_source" label="来源备注" :span="3">
            {{ order.remark_source }}
          </DescriptionsItem>
          <DescriptionsItem v-if="order.rejected_reason" label="驳回原因" :span="3">
            {{ order.rejected_reason }}
          </DescriptionsItem>
          <DescriptionsItem v-if="order.cancelled_reason" label="作废原因" :span="3">
            {{ order.cancelled_reason }}
          </DescriptionsItem>
        </Descriptions>

        <Table
          :columns="lineColumns"
          :data-source="order.lines ?? []"
          :row-key="(row: OrderLineOut) => row.id"
          size="small"
          :pagination="false"
          :scroll="{ x: 1100 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'width_cm'">
              {{ record.width_cm ?? '—' }}
            </template>

            <template v-else-if="column.key === 'output_qty'">
              {{ formatQty(record.output_qty, 0) }}
            </template>

            <template v-else-if="column.key === 'balance_qty'">
              <!-- 行余量 = 录入出数 - 尺码合计（C34 口径 A） -->
              <span :class="{ 'cell-warn': isPositive(record.balance_qty) }">
                {{ formatQty(record.balance_qty, 0) }}
              </span>
            </template>

            <template v-else-if="column.key === 'size_line_sum_qty'">
              {{ formatQty(record.size_line_sum_qty, 0) }}
            </template>

            <template v-else-if="column.key === 'colors'">
              <!--
                ⚠️ 第二层（行内颜色）用 `Table` 而不是文本串：色码与手数是**逐色**的
                （ADR-0017 一床多色），压成一行字符串就看不出哪个色欠多少手。
              -->
              <Table
                class="layer-colors"
                :columns="colorColumns"
                :data-source="colorsOf(record)"
                :row-key="(row: LineColorOut) => row.id"
                size="small"
                :pagination="false"
              >
                <template #bodyCell="{ column: subColumn, record: subRecord }">
                  <template v-if="subColumn.key === 'entry_mode'">
                    <Tag>{{ entryModeText(String(subRecord.entry_mode)) }}</Tag>
                  </template>

                  <template v-else-if="subColumn.key === 'balance_qty_total'">
                    <span :class="{ 'cell-warn': isPositive(String(subRecord.balance_qty_total)) }">
                      {{ formatQty(String(subRecord.balance_qty_total), 0) }}
                    </span>
                  </template>

                  <template v-else-if="subColumn.key === 'size_lines'">
                    <!-- 第三层：尺码明细，出数的**权威来源**（`modules/02 §2` C5） -->
                    <Table
                      class="layer-sizes"
                      :columns="sizeColumns"
                      :data-source="sizeLinesOf(subRecord)"
                      :row-key="(row: SizeLineOut) => row.id"
                      size="small"
                      :pagination="false"
                    >
                      <template #bodyCell="{ column: leafColumn, record: leafRecord }">
                        <template v-if="leafColumn.key === 'output_qty_manual'">
                          <Tag v-if="leafRecord.output_qty_manual" color="orange">人工</Tag>
                          <span v-else class="cell-muted">比例</span>
                        </template>

                        <template v-else-if="leafColumn.key === 'output_qty'">
                          {{ formatQty(String(leafRecord.output_qty), 0) }}
                        </template>

                        <template v-else-if="leafColumn.key === 'balance_qty'">
                          <span :class="{ 'cell-warn': isPositive(String(leafRecord.balance_qty)) }">
                            {{ formatQty(String(leafRecord.balance_qty), 0) }}
                          </span>
                        </template>
                      </template>
                    </Table>
                  </template>
                </template>
              </Table>
            </template>
          </template>
        </Table>
      </template>
    </Spin>
  </PageLayout>
</template>

<style scoped>
.cell-muted {
  color: var(--color-text-third);
}

/* ⚠️ 尾数 / 余量 > 0 高亮：它是「要注意」的信号（docs/06 §1）。 */
.cell-warn {
  font-weight: 600;
  color: var(--color-warning);
}
</style>
