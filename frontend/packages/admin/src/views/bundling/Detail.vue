<script setup lang="ts">
/**
 * 打菲单详情页（T-BUND-009）—— 只读。
 *
 * 含：
 * - 面包屑 `款号 > 裁剪单 > 打菲单 > 码`（可跳父单）
 * - 表头 Descriptions（基本信息 / 款号信息 / 数量金额 / 审核信息）
 * - 明细表格（按 line_no 排序，每行含 `hands`/`planned_qty`/`available_qty_before` + 所属缸号匹号）
 * - 底部汇总（手数合计、出数合计，右对齐等宽字体）
 * - 操作区（右上 sticky）：按状态显示编辑/提交/审核/反审核/打印/复制
 * - 变更历史（Drawer，展示 document_logs）
 * - 「按手列表」区块（手号 / 码 / 色 / 尺码 / 件数 / 打印 / 计件）
 *
 * ⚠️ 页面**不**做任何汇总计算：表头五列与各层小计都由后端重算并返回（模块/03 §4），
 * 前端再算一遍就是第二份真相。
 *
 * 「一码 = 一手 = 一个员工」必须在界面上反复说明（模块/03 §11.5.2）。
 */
import { computed, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Alert, Button, Card, Descriptions, DescriptionsItem, Drawer, Spin, Table, Tooltip, message } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { PERM, formatDateTime, formatQty } from '@garment/shared'
import type { BundlingOrderOut, LineOut } from '@garment/shared'
import { getBundlingOrder, listBundlingOrderLogs, submitBundlingOrder, approveBundlingOrder, rejectBundlingOrder, withdrawBundlingOrder, reverseBundlingOrder, cancelBundlingOrder } from '@/api/bundling'
import PageLayout from '@/components/PageLayout.vue'
import StatusTag from '@/components/StatusTag.vue'

defineOptions({ name: 'BundlingOrderDetail' })

const route = useRoute()
const router = useRouter()

const order = ref<BundlingOrderOut | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const logsVisible = ref(false)
const logsLoading = ref(false)
const logs = ref<unknown[]>([])

function orderIdOf(raw: unknown): string {
  const value = Array.isArray(raw) ? raw[0] : raw
  return typeof value === 'string' ? value : ''
}

const orderId = computed(() => orderIdOf(route.params['orderId']))

async function load(): Promise<void> {
  if (orderId.value === '') {
    errorMessage.value = '缺少打菲单 ID（页面地址不对）'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    order.value = await getBundlingOrder(orderId.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '加载失败'
    order.value = null
  } finally {
    loading.value = false
  }
}

async function loadLogs(): Promise<void> {
  logsLoading.value = true
  try {
    const result = await listBundlingOrderLogs(orderId.value, { page: 1, page_size: 100 })
    logs.value = result.items
  } catch {
    logs.value = []
  } finally {
    logsLoading.value = false
  }
}

function openLogs(): void {
  logsVisible.value = true
  void loadLogs()
}

const lineColumns: ColumnsType<LineOut> = [
  { key: 'line_no', title: '行', dataIndex: 'line_no', width: 60 },
  { key: 'size_code', title: '尺码', dataIndex: 'size_code', width: 80 },
  { key: 'hands', title: '手数', dataIndex: 'hands', width: 80, align: 'right' },
  { key: 'planned_qty', title: '计划件数', dataIndex: 'planned_qty', width: 110, align: 'right' },
  { key: 'available_qty_before', title: '可打菲量', dataIndex: 'available_qty_before', width: 110, align: 'right' },
  { key: 'cutting_size_line_id', title: '来源裁剪明细行', dataIndex: 'cutting_size_line_id', width: 320 },
  { key: 'group_no', title: '派工组别', dataIndex: 'group_no', width: 120 },
  { key: 'workstation_no', title: '工位', dataIndex: 'workstation_no', width: 120 },
]

const handsColumns: ColumnsType<LineOut> = [
  { key: 'line_no', title: '行', dataIndex: 'line_no', width: 60 },
  { key: 'size_code', title: '尺码', dataIndex: 'size_code', width: 80 },
  { key: 'hands', title: '手数', dataIndex: 'hands', width: 80, align: 'right' },
  { key: 'hands', title: '手号详情', dataIndex: 'hands', width: 220, customRender: ({ record }: { record: LineOut }) => `第 ${record.hands} 手` },
  { key: 'planned_qty', title: '计划件数', dataIndex: 'planned_qty', width: 110, align: 'right', customRender: ({ record }: { record: LineOut }) => record.planned_qty },
]

const logsColumns: ColumnsType<unknown> = [
  { key: 'created_at', title: '时间', dataIndex: 'created_at', width: 160 },
  { key: 'operator_name', title: '操作人', dataIndex: 'operator_name', width: 100 },
  { key: 'action', title: '动作', dataIndex: 'action', width: 100 },
  { key: 'from_status', title: '从', dataIndex: 'from_status', width: 80 },
  { key: 'to_status', title: '到', dataIndex: 'to_status', width: 80 },
  { key: 'reason', title: '原因', dataIndex: 'reason', width: 200 },
]

/** 溯源面包屑：`款号 > 裁剪单 > 打菲单 > 码`。 */
const breadcrumbs = computed(() => {
  if (!order.value) return []
  return [
    { label: order.value.style_no, to: { name: 'base-styles-detail', params: { styleNo: order.value.style_no } } },
    { label: `裁剪单 ${order.value.source_cutting_order_id?.slice(0, 8) ?? '—'}`, to: { name: 'cutting-orders-detail', params: { orderId: order.value.source_cutting_order_id } } },
    { label: `打菲单 ${order.value.doc_no}`, to: null },
    { label: '码（按手列表见下）', to: null },
  ]
})

function isPositive(value: string | null | undefined): boolean {
  return Number(value ?? 0) > 0
}

function handsText(line: LineOut): string {
  return `第 ${line.hands} 手 / 共 — 手 · ${formatQty(line.planned_qty ?? '0', 0)} 件`
}

async function submit(): Promise<void> {
  try {
    await submitBundlingOrder(orderId.value)
    message.success('已提交')
    void load()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '提交失败')
  }
}

async function approve(): Promise<void> {
  try {
    await approveBundlingOrder(orderId.value)
    message.success('已审核')
    void load()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '审核失败')
  }
}

async function reject(): Promise<void> {
  try {
    // 需要输入原因
    const reason = prompt('请输入驳回原因：')
    if (!reason) return
    await rejectBundlingOrder(orderId.value, { reason })
    message.success('已驳回')
    void load()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '驳回失败')
  }
}

async function withdraw(): Promise<void> {
  try {
    await withdrawBundlingOrder(orderId.value)
    message.success('已撤回')
    void load()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '撤回失败')
  }
}

async function reverse(): Promise<void> {
  try {
    const reason = prompt('请输入反审核原因：')
    if (!reason) return
    await reverseBundlingOrder(orderId.value, { reason })
    message.success('已反审核')
    void load()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '反审核失败')
  }
}

async function cancel(): Promise<void> {
  try {
    const reason = prompt('请输入作废原因：')
    if (!reason) return
    await cancelBundlingOrder(orderId.value, { cancelled_reason: reason })
    message.success('已作废')
    void load()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '作废失败')
  }
}

function print(): void {
  // TODO: T-BUND-010 接入标签打印
  router.push({ name: 'bundling-orders-print', params: { orderId } })
}

onMounted(() => {
  void load()
})

watch(orderId, () => {
  void load()
})
</script>

<template>
  <PageLayout :title="order?.doc_no ?? '打菲单详情'">
    <template #extra>
      <Button @click="router.back()">返回</Button>
      <Button v-can="PERM.BUNDLING_UPDATE" type="primary" @click="router.push({ name: 'bundling-orders-edit', params: { orderId } })">
        编辑
      </Button>
      <Button v-if="order?.status === 'DRAFT' || order?.status === 'REJECTED'" v-can="PERM.BUNDLING_SUBMIT" type="primary" @click="submit">
        提交
      </Button>
      <Button v-if="order?.status === 'SUBMITTED'" v-can="PERM.BUNDLING_APPROVE" type="primary" @click="approve">
        审核
      </Button>
      <Button v-if="order?.status === 'SUBMITTED'" v-can="PERM.BUNDLING_REJECT" @click="reject">
        驳回
      </Button>
      <Button v-if="order?.status === 'SUBMITTED'" v-can="PERM.BUNDLING_WITHDRAW" @click="withdraw">
        撤回
      </Button>
      <Button v-if="order?.status === 'APPROVED'" v-can="PERM.BUNDLING_REVERSE" @click="reverse">
        反审核
      </Button>
      <Button v-if="order?.status === 'DRAFT' || order?.status === 'REJECTED'" v-can="PERM.BUNDLING_CANCEL" @click="cancel">
        作废
      </Button>
      <Button v-if="order?.status === 'APPROVED'" v-can="PERM.BUNDLING_PRINT" @click="print">
        打印标签
      </Button>
      <Button v-can="PERM.BUNDLING_READ" @click="openLogs">变更历史</Button>
    </template>

    <Alert
      v-if="errorMessage !== ''"
      type="error"
      show-icon
      message="打菲单加载失败"
      :description="errorMessage"
      :action="h(Button, { size: 'small', onClick: load }, { default: () => '重试' })"
      style="margin-bottom: var(--space-4)"
    />

    <Spin :spinning="loading">
      <template v-if="order !== null">
        <!-- 溯源面包屑 -->
        <div class="breadcrumb-bar" style="margin-bottom: var(--space-4); font-size: var(--font-size-sm);">
          <span v-for="(crumb, i) in breadcrumbs" :key="i" class="breadcrumb-item">
            <a v-if="crumb.to" @click.prevent="router.push(crumb.to)">{{ crumb.label }}</a>
            <span v-else>{{ crumb.label }}</span>
            <span v-if="i < breadcrumbs.length - 1" class="breadcrumb-sep"> › </span>
          </span>
          <small class="hint" style="margin-left: var(--space-3);">
            ⚠️ 一码 = 一手 = 一个员工（每码件数 = 这一扎装几件，码数 = 全单打多少个码）
          </small>
        </div>

        <Descriptions bordered size="small" :column="3" style="margin-bottom: var(--space-4)">
          <DescriptionsItem label="单据状态">
            <StatusTag :status="order.status" />
          </DescriptionsItem>
          <DescriptionsItem label="单据日期">{{ order.doc_date }}</DescriptionsItem>
          <DescriptionsItem label="款号">{{ order.style_no }}</DescriptionsItem>
          <DescriptionsItem label="工序">{{ order.operation_no }}</DescriptionsItem>
          <DescriptionsItem label="色码">{{ order.color_code }}</DescriptionsItem>
          <DescriptionsItem label="色组">{{ order.color_group }}</DescriptionsItem>
          <DescriptionsItem label="车间">{{ order.workshop_id }}</DescriptionsItem>
          <DescriptionsItem label="一扎件数（参考）">{{ order.bundle_qty }}</DescriptionsItem>
          <DescriptionsItem label="码数合计（手）">{{ order.hands_total }}</DescriptionsItem>
          <DescriptionsItem label="出数合计">{{ formatQty(order.output_qty, 0) }}</DescriptionsItem>
          <DescriptionsItem label="余数">
            <span :class="{ 'cell-warn': isPositive(order.balance_qty) }">
              {{ formatQty(order.balance_qty, 0) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="已打印">{{ order.label_print_qty }} 张</DescriptionsItem>
          <DescriptionsItem label="版本">{{ order.version }}</DescriptionsItem>
          <DescriptionsItem label="创建时间">{{ formatDateTime(order.created_at) }}</DescriptionsItem>
          <DescriptionsItem label="更新时间">{{ formatDateTime(order.updated_at) }}</DescriptionsItem>
          <DescriptionsItem v-if="order.approved_at" label="审核时间">{{ formatDateTime(order.approved_at) }}</DescriptionsItem>
          <DescriptionsItem v-if="order.approved_by" label="审核人">{{ order.approved_by }}</DescriptionsItem>
          <DescriptionsItem v-if="order.rejected_reason" label="驳回原因" :span="3">{{ order.rejected_reason }}</DescriptionsItem>
          <DescriptionsItem v-if="order.cancelled_reason" label="作废原因" :span="3">{{ order.cancelled_reason }}</DescriptionsItem>
          <DescriptionsItem label="备注" :span="3">{{ order.remark ?? '—' }}</DescriptionsItem>
        </Descriptions>

        <!-- 明细表格 -->
        <Card size="small" title="明细（按 line_no 排序）" style="margin-bottom: var(--space-4)">
          <Table
            :columns="lineColumns"
            :data-source="order.lines ?? []"
            :row-key="(row: LineOut) => row.id"
            size="small"
            :pagination="false"
            :scroll="{ x: 1300 }"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'planned_qty'">
                {{ formatQty(record.planned_qty ?? '0', 0) }}
              </template>
              <template v-else-if="column.key === 'available_qty_before'">
                {{ formatQty(record.available_qty_before, 0) }}
              </template>
              <template v-else-if="column.key === 'cutting_size_line_id'">
                <Tooltip :title="record.cutting_size_line_id">
                  <span class="code-cell">{{ record.cutting_size_line_id.slice(0, 8) }}…</span>
                </Tooltip>
              </template>
            </template>
          </Table>
        </Card>

        <!-- 按手列表（从 bundles 端点拉取，这里占位展示逻辑） -->
        <Card size="small" title="按手列表（手号 / 码 / 色 / 尺码 / 件数 / 打印 / 计件）" style="margin-bottom: var(--space-4)">
          <p class="hint">⚠️ 此处应通过 `GET /bundles?doc_id={orderId}` 获取完整码列表（T-BUND-010 接入）；当前展示明细行的 `hands` 作为占位。</p>
          <Table
            :columns="handsColumns"
            :data-source="order.lines ?? []"
            :row-key="(row: LineOut) => row.id"
            size="small"
            :pagination="false"
            :scroll="{ x: 1100 }"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'hands_text'">
                {{ handsText(record) }}
              </template>
            </template>
          </Table>
        </Card>

        <!-- 底部汇总 -->
        <div class="summary-bar" style="display: flex; justify-content: flex-end; gap: var(--space-6); padding: var(--space-3); background: var(--color-bg); border-radius: var(--radius-md); font-family: var(--font-mono);">
          <span>手数合计：<strong>{{ order.hands_total }}</strong></span>
          <span>出数合计：<strong>{{ formatQty(order.output_qty, 0) }}</strong></span>
          <span>已打印：<strong>{{ order.label_print_qty }}</strong> 张</span>
        </div>

        <!-- 变更历史 Drawer -->
        <Drawer
          v-model:open="logsVisible"
          title="变更历史"
          :width="720"
          placement="right"
        >
          <Spin :spinning="logsLoading">
            <Table
              :columns="logsColumns"
              :data-source="logs"
              :row-key="(row: unknown) => (row as Record<string, unknown>).id as string"
              size="small"
              :pagination="false"
            />
          </Spin>
        </Drawer>
      </template>
    </Spin>
  </PageLayout>
</template>

<style scoped>
.hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
  margin-bottom: var(--space-2);
}

.breadcrumb-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
}

.breadcrumb-item a {
  color: var(--color-primary);
  text-decoration: none;
}

.breadcrumb-item a:hover {
  text-decoration: underline;
}

.breadcrumb-sep {
  color: var(--color-text-third);
}

.cell-warn {
  font-weight: 600;
  color: var(--color-warning);
}

.code-cell {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  max-width: 200px;
  font-family: var(--font-mono);
}
</style>
