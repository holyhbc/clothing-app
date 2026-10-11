<script setup lang="ts">
/**
 * 打菲单列表页（T-BUND-009）。
 *
 * 顶部为单据列表，行内展开即按手列码（手号 / `bundle_no` / 件数 / 打印 / 计件）。
 * 复用 `ResourceList` 结构：PageHeader + FilterCard + TableCard（docs/06 §2.2）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, DatePicker, Select, Space, Spin, Table } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import type { ExpandedRowRender } from 'ant-design-vue/es/vc-table/interface'
import { h } from 'vue'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { PERM, formatQty } from '@garment/shared'
import type { BundlingOrderListOut, LineOut } from '@garment/shared'
import { listBundlingOrders, getBundlingOrder } from '@/api/bundling'
import type { BundlingOrderListQuery } from '@/api/bundling'
import { baseApi } from '@/api/base'
import { searchStyleOptionsById } from '@/api/cutting'
import { usePageList } from '@/composables/usePageList'
import EmptyState from '@/components/EmptyState.vue'
import PageLayout from '@/components/PageLayout.vue'
import StatusTag from '@/components/StatusTag.vue'
import TableToolbar from '@/components/TableToolbar.vue'

defineOptions({ name: 'BundlingOrderList' })

const router = useRouter()

const list = usePageList<BundlingOrderListQuery, BundlingOrderListOut>({
  fetch: listBundlingOrders,
  initialQuery: { page: 1, size: 20 },
})

const statusFilter = ref<string | undefined>(undefined)
const styleFilter = ref<string | undefined>(undefined)
const operationFilter = ref<string | undefined>(undefined)
const colorFilter = ref<string | undefined>(undefined)
const workshopFilter = ref<string | undefined>(undefined)
const dateRange = ref<[Dayjs, Dayjs] | null>(null)

const dateRangeProps = computed<Record<string, unknown>>(() =>
  dateRange.value === null ? {} : { value: dateRange.value },
)

const styleOptions = ref<{ value: string; label: string }[]>([])
const operationOptions = ref<{ value: string; label: string }[]>([])
const workshopOptions = ref<{ value: string; label: string }[]>([])

const statusOptions = computed(() => [
  { value: 'DRAFT', label: '草稿' },
  { value: 'SUBMITTED', label: '待审核' },
  { value: 'APPROVED', label: '已审核' },
  { value: 'REJECTED', label: '已驳回' },
  { value: 'CANCELLED', label: '已作废' },
])

async function loadStyleOptions(): Promise<void> {
  try {
    const opts = await searchStyleOptionsById('')
    styleOptions.value = opts.map((option) => ({
      value: option.value,
      label: option.label,
    }))
  } catch {
    styleOptions.value = []
  }
}

async function loadOperationOptions(): Promise<void> {
  try {
    operationOptions.value = (await baseApi.operations.optionsById('')).map((option) => ({
      value: option.value,
      label: option.label,
    }))
  } catch {
    operationOptions.value = []
  }
}

async function loadWorkshopOptions(): Promise<void> {
  try {
    workshopOptions.value = (await baseApi.workshops.optionsById()).map((option) => ({
      value: option.value,
      label: option.label,
    }))
  } catch {
    workshopOptions.value = []
  }
}

function toDateString(value: Dayjs): string {
  return value.format('YYYY-MM-DD')
}

function onSearch(): void {
  list.applyFilter({
    status: statusFilter.value,
    style_no: styleFilter.value,
    operation_no: operationFilter.value,
    color_code: colorFilter.value,
    workshop_id: workshopFilter.value,
    doc_date_from: dateRange.value ? toDateString(dateRange.value[0]) : undefined,
    doc_date_to: dateRange.value ? toDateString(dateRange.value[1]) : undefined,
  })
}

function onReset(): void {
  statusFilter.value = undefined
  styleFilter.value = undefined
  operationFilter.value = undefined
  colorFilter.value = undefined
  workshopFilter.value = undefined
  dateRange.value = null
  list.applyFilter({})
}

function onStatusChange(raw: unknown): void {
  statusFilter.value = typeof raw === 'string' && raw !== '' ? raw : undefined
  onSearch()
}

function onStyleChange(raw: unknown): void {
  styleFilter.value = typeof raw === 'string' && raw !== '' ? raw : undefined
  onSearch()
}

function onOperationChange(raw: unknown): void {
  operationFilter.value = typeof raw === 'string' && raw !== '' ? raw : undefined
  onSearch()
}

function onColorChange(raw: unknown): void {
  colorFilter.value = typeof raw === 'string' && raw !== '' ? raw : undefined
  onSearch()
}

function onWorkshopChange(raw: unknown): void {
  workshopFilter.value = typeof raw === 'string' && raw !== '' ? raw : undefined
  onSearch()
}

function onDateRangeChange(raw: unknown): void {
  if (
    Array.isArray(raw) &&
    raw.length === 2 &&
    dayjs.isDayjs(raw[0]) &&
    dayjs.isDayjs(raw[1])
  ) {
    dateRange.value = [raw[0] as Dayjs, raw[1] as Dayjs]
  } else {
    dateRange.value = null
  }
  onSearch()
}

function openDetail(orderId: string): void {
  void router.push({ name: 'bundling-orders-detail', params: { orderId } })
}

function create(): void {
  void router.push({ name: 'bundling-orders-new' })
}

function onTableChange(paginationInfo: { current?: number; pageSize?: number }): void {
  const size = paginationInfo.pageSize
  if (size !== undefined && size !== list.query.size) {
    list.changeSize(size)
    return
  }
  if (paginationInfo.current !== undefined) list.changePage(paginationInfo.current)
}

const pagination = computed(() => ({
  total: list.total,
  current: list.query.page ?? 1,
  pageSize: list.query.size ?? 20,
  showTotal: (value: number) => `共 ${value} 张打菲单`,
}))

const columns: ColumnsType<BundlingOrderListOut> = [
  { key: 'doc_no', title: '打菲单号', dataIndex: 'doc_no', width: 160 },
  { key: 'doc_date', title: '单据日期', dataIndex: 'doc_date', width: 110 },
  { key: 'status', title: '状态', dataIndex: 'status', width: 100 },
  { key: 'style_no', title: '款号', dataIndex: 'style_no', width: 140 },
  { key: 'operation_no', title: '工序', dataIndex: 'operation_no', width: 100 },
  { key: 'color_code', title: '商品分类', dataIndex: 'color_code', width: 100 },
  { key: 'hands_total', title: '码数(手)', dataIndex: 'hands_total', width: 100, align: 'right' },
  { key: 'output_qty', title: '出数', dataIndex: 'output_qty', width: 100, align: 'right' },
  { key: 'balance_qty', title: '余数', dataIndex: 'balance_qty', width: 90, align: 'right' },
  { key: 'label_print_qty', title: '已打印', dataIndex: 'label_print_qty', width: 90, align: 'right' },
  { key: 'actions', title: '操作', width: 120, fixed: 'right' },
]

const expandedRowKeys = ref<string[]>([])
const handsCache = ref<Record<string, LineOut[]>>({})

async function onExpand(expanded: boolean, record: BundlingOrderListOut): Promise<void> {
  if (!expanded) return
  if (handsCache.value[record.id] !== undefined) return
  try {
    const detail = await getBundlingOrder(record.id)
    handsCache.value[record.id] = detail.lines ?? []
  } catch {
    handsCache.value[record.id] = []
  }
}

const handsColumns: ColumnsType<LineOut> = [
  { key: 'line_no', title: '行号', dataIndex: 'line_no', width: 60 },
  { key: 'size_code', title: '尺码', dataIndex: 'size_code', width: 80 },
  { key: 'hands', title: '手数', dataIndex: 'hands', width: 80, align: 'right' },
  { key: 'planned_qty', title: '计划件数', dataIndex: 'planned_qty', width: 100, align: 'right' },
  { key: 'available_qty_before', title: '可打菲量', dataIndex: 'available_qty_before', width: 100, align: 'right' },
]

/**
 * ⚠️ antd 的 `ExpandedRowRender` 收的是**单个对象** `{ record, index, indent, expanded }`，
 * 不是 4 个位置参数（那是 `@expand` **事件**的签名，两者别混）。
 *
 * 之前写成位置参数，于是 `record` 实际拿到的是那个包装对象，
 * `record.id` 为 `undefined` → `handsCache[undefined]` 永远 miss
 * → **展开行永远显示「加载中…」**，数据其实早就被 `onExpand` 缓存好了。
 */
const renderHands: ExpandedRowRender<BundlingOrderListOut> = ({ record }) => {
  const lines = handsCache.value[record.id] ?? []
  if (lines.length === 0) return h('span', { class: 'cell-muted' }, '加载中…')
  return h(Table, {
    class: 'hands-table',
    columns: handsColumns,
    dataSource: lines,
    rowKey: (row: LineOut) => row.id,
    size: 'small',
    pagination: false,
  })
}

onMounted(() => {
  void list.reload()
  void loadStyleOptions()
  void loadOperationOptions()
  void loadWorkshopOptions()
})
</script>

<template>
  <PageLayout title="打菲单" :description="`共 ${list.total} 张打菲单`">
    <template #extra>
      <Button v-can="PERM.BUNDLING_CREATE" type="primary" @click="create">新建打菲单</Button>
    </template>

    <template #filter="{ collapsed }">
      <Space direction="vertical" size="small" style="width: 100%">
        <Space wrap>
          <Select
            :value="statusFilter"
            placeholder="按状态"
            style="width: 130px"
            allow-clear
            :options="statusOptions"
            @change="onStatusChange"
          />
          <Select
            :value="styleFilter"
            placeholder="按款号"
            style="width: 180px"
            allow-clear
            show-search
            :options="styleOptions"
            @change="onStyleChange"
          />
          <Select
            :value="operationFilter"
            placeholder="按工序"
            style="width: 140px"
            allow-clear
            :options="operationOptions"
            @change="onOperationChange"
          />
          <Select
            :value="colorFilter"
            placeholder="按色码"
            style="width: 120px"
            allow-clear
            @change="onColorChange"
          />
          <Select
            :value="workshopFilter"
            placeholder="按车间"
            style="width: 150px"
            allow-clear
            :options="workshopOptions"
            @change="onWorkshopChange"
          />
          <DatePicker.RangePicker
            v-bind="dateRangeProps"
            style="width: 240px"
            @update:value="onDateRangeChange"
          />
          <Button type="primary" @click="onSearch">查询</Button>
          <Button @click="onReset">重置</Button>
        </Space>
        <span v-if="collapsed === false" class="filter-hint">
          款号、工序、色码、车间为精确匹配（后端无模糊搜索）；日期区间含首尾两天
        </span>
      </Space>
    </template>

    <template #toolbar>
      <TableToolbar :selected-count="0" :total="list.total">
        <template #actions="{ enabled }">
          <span v-if="!enabled" class="toolbar-hint">
            行内展开查看「按手列表」（手号 / 码 / 件数 / 打印 / 计件）；点「详情」看溯源面包屑
          </span>
        </template>
      </TableToolbar>
    </template>

    <Spin :spinning="list.loading">
      <Table
        :columns="columns"
        :data-source="list.items"
        :row-key="(row: BundlingOrderListOut) => row.id"
        size="small"
        :pagination="pagination"
        :scroll="{ x: 1500 }"
        :expanded-row-keys="expandedRowKeys"
        :expanded-row-render="renderHands"
        @change="onTableChange"
        @expand="onExpand"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'doc_no'">
            <span class="code-cell" :title="record.doc_no">{{ record.doc_no }}</span>
          </template>

          <template v-else-if="column.key === 'status'">
            <StatusTag :status="record.status" />
          </template>

          <template v-else-if="column.key === 'output_qty'">
            {{ formatQty(record.output_qty, 0) }}
          </template>

          <template v-else-if="column.key === 'balance_qty'">
            <span v-if="Number(record.balance_qty) > 0" class="cell-warn">
              {{ formatQty(record.balance_qty, 0) }}
            </span>
            <span v-else class="cell-muted">—</span>
          </template>

          <template v-else-if="column.key === 'label_print_qty'">
            {{ record.label_print_qty }}
          </template>

          <template v-else-if="column.key === 'actions'">
            <Button
              v-can="PERM.BUNDLING_READ"
              size="small"
              type="link"
              @click="openDetail(record.id)"
            >
              详情
            </Button>
          </template>
        </template>

        <template #emptyText>
          <EmptyState
            v-if="list.isEmpty"
            title="还没有打菲单"
            hint="点右上角「新建打菲单」建第一张草稿；表头与明细一次提交"
            secondary-action-text="清空筛选"
            @secondary-action="onReset"
          />
          <EmptyState
            v-else-if="list.hasError"
            title="打菲单列表加载失败"
            :hint="list.error?.message ?? '请稍后重试'"
            action-text="重试"
            @action="list.reload()"
          />
        </template>
      </Table>
    </Spin>
  </PageLayout>
</template>

<style scoped>
.filter-hint,
.toolbar-hint,
.cell-muted {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.cell-warn {
  font-weight: 600;
  color: var(--color-warning);
}

.hands-table {
  margin-top: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.code-cell {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block
  max-width: 160px;
  font-family: var(--font-mono);
}
</style>
