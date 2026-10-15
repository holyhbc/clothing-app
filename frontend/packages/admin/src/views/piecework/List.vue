<script setup lang="ts">
/**
 * 计件流水台账页（T-PIECE-005；docs/06 §2.2 列表页标准结构）。
 *
 * 主管视图：按日期 / 款号 / 工序 / 车间 / 类型筛出流水，看**手数与件数并列**
 * 的台账（BR-PW-31），点「详情」看码信息与红冲关联，点「导出」拿到同筛选的 CSV。
 *
 * ⚠️ 本页**纯读**：不含红冲 / 补录入口（写操作要权限 + 二次确认 + 原因必填，独立卡）。
 */
import { computed, onMounted, ref } from 'vue'
import { Button, DatePicker, Input, Select, Space, Spin, Table, Tag } from 'ant-design-vue'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { PERM, formatMoney, formatQty, formatUnitPrice } from '@garment/shared'
import type { PieceworkLogRowOut } from '@garment/shared'
import { listPieceworkLogs } from '@/api/piecework'
import type { PieceworkLogQuery } from '@/api/piecework'
import { baseApi } from '@/api/base'
import { searchStyleOptionsById } from '@/api/cutting'
import { usePageList } from '@/composables/usePageList'
import EmptyState from '@/components/EmptyState.vue'
import PageLayout from '@/components/PageLayout.vue'
import TableToolbar from '@/components/TableToolbar.vue'
import { columns } from './columns'
import { LOG_TYPE_LABEL, LOG_TYPE_OPTIONS, employeeText, handsText } from './presentation'
import { usePieceworkExport } from './usePieceworkExport'
import LogDetailDrawer from './components/LogDetailDrawer.vue'

defineOptions({ name: 'PieceworkLogList' })

const list = usePageList<PieceworkLogQuery, PieceworkLogRowOut>({
  fetch: listPieceworkLogs,
  initialQuery: { page: 1, size: 20, sort: '-work_date' },
})

const { exporting, onExport } = usePieceworkExport(() => list.query)

const dateRange = ref<[Dayjs, Dayjs] | null>(null)
const styleFilter = ref<string | undefined>(undefined)
const operationFilter = ref<string | undefined>(undefined)
const workshopFilter = ref<string | undefined>(undefined)
const logTypeFilter = ref<string | undefined>(undefined)
const bundleFilter = ref<string | undefined>(undefined)
const settledFilter = ref<string | undefined>(undefined)

const dateRangeProps = computed<Record<string, unknown>>(() =>
  dateRange.value === null ? {} : { value: dateRange.value },
)

const styleOptions = ref<{ value: string; label: string }[]>([])
const operationOptions = ref<{ value: string; label: string }[]>([])
const workshopOptions = ref<{ value: string; label: string }[]>([])
const settledOptions = [
  { value: 'true', label: '已结算' },
  { value: 'false', label: '未结算' },
]

async function loadStyleOptions(): Promise<void> {
  try {
    styleOptions.value = (await searchStyleOptionsById('')).map((o) => ({
      value: o.value,
      label: o.label,
    }))
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

function toDateString(value: Dayjs): string {
  return value.format('YYYY-MM-DD')
}

function asString(raw: unknown): string | undefined {
  return typeof raw === 'string' && raw !== '' ? raw : undefined
}

function onSearch(): void {
  list.applyFilter({
    work_date_from: dateRange.value ? toDateString(dateRange.value[0]) : undefined,
    work_date_to: dateRange.value ? toDateString(dateRange.value[1]) : undefined,
    style_no: styleFilter.value,
    operation_no: operationFilter.value,
    workshop_id: workshopFilter.value,
    log_type: logTypeFilter.value as PieceworkLogQuery['log_type'],
    bundle_no: bundleFilter.value,
    is_settled: settledFilter.value === undefined ? undefined : settledFilter.value === 'true',
  })
}

function onReset(): void {
  dateRange.value = null
  styleFilter.value = undefined
  operationFilter.value = undefined
  workshopFilter.value = undefined
  logTypeFilter.value = undefined
  bundleFilter.value = undefined
  settledFilter.value = undefined
  list.applyFilter({})
}

function onDateRangeChange(raw: unknown): void {
  if (Array.isArray(raw) && raw.length === 2 && dayjs.isDayjs(raw[0]) && dayjs.isDayjs(raw[1])) {
    dateRange.value = [raw[0] as Dayjs, raw[1] as Dayjs]
  } else {
    dateRange.value = null
  }
  onSearch()
}

function onSettledChange(raw: unknown): void {
  settledFilter.value = asString(raw)
  onSearch()
}

function onBundleInput(event: Event): void {
  bundleFilter.value = asString((event.target as HTMLInputElement).value)
  onSearch()
}

const detailId = ref<string | null>(null)

function openDetail(row: PieceworkLogRowOut): void {
  detailId.value = row.log_id
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
  showTotal: (value: number) => `共 ${value} 条流水`,
}))

onMounted(() => {
  void list.reload()
  void loadStyleOptions()
  void loadOperationOptions()
  void loadWorkshopOptions()
})
</script>

<template>
  <PageLayout title="计件流水" :description="`共 ${list.total} 条流水`">
    <template #extra>
      <Button
        v-can="PERM.PIECEWORK_EXPORT"
        :loading="exporting"
        :disabled="list.isEmpty"
        @click="onExport"
      >
        导出 CSV
      </Button>
    </template>

    <template #filter>
      <Space direction="vertical" size="small" style="width: 100%">
        <Space wrap>
          <DatePicker.RangePicker
            v-bind="dateRangeProps"
            style="width: 240px"
            @update:value="onDateRangeChange"
          />
          <Select
            :value="styleFilter"
            placeholder="按款号"
            style="width: 180px"
            allow-clear
            show-search
            :options="styleOptions"
            @change="styleFilter = asString($event); onSearch()"
          />
          <Select
            :value="operationFilter"
            placeholder="按工序"
            style="width: 150px"
            allow-clear
            :options="operationOptions"
            @change="operationFilter = asString($event); onSearch()"
          />
          <Select
            :value="workshopFilter"
            placeholder="按车间"
            style="width: 150px"
            allow-clear
            :options="workshopOptions"
            @change="workshopFilter = asString($event); onSearch()"
          />
          <Select
            :value="logTypeFilter"
            placeholder="按类型"
            style="width: 130px"
            allow-clear
            :options="LOG_TYPE_OPTIONS"
            @change="logTypeFilter = asString($event); onSearch()"
          />
          <Select
            :value="settledFilter"
            placeholder="结算状态"
            style="width: 130px"
            allow-clear
            :options="settledOptions"
            @change="onSettledChange"
          />
          <Input
            :value="bundleFilter ?? ''"
            placeholder="按打菲号"
            style="width: 220px"
            allow-clear
            @change="onBundleInput"
          />
          <Button type="primary" @click="onSearch">查询</Button>
          <Button @click="onReset">重置</Button>
        </Space>
        <span class="filter-hint">
          款号 / 工序 / 车间 / 打菲号为精确匹配（后端无模糊搜索）；日期区间含首尾两天
        </span>
      </Space>
    </template>

    <template #toolbar>
      <TableToolbar :selected-count="0" :total="list.total">
        <template #actions="{ enabled }">
          <span v-if="!enabled" class="toolbar-hint">
            台账按「手」与「件」并列展示；点「详情」看该条的码信息与红冲关联
          </span>
        </template>
      </TableToolbar>
    </template>

    <Spin :spinning="list.loading">
      <Table
        :columns="columns"
        :data-source="list.items"
        :row-key="(row: PieceworkLogRowOut) => row.log_id"
        size="small"
        :pagination="pagination"
        :scroll="{ x: 1560 }"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'employee'">
            <span :title="employeeText(record as PieceworkLogRowOut)">{{
              employeeText(record as PieceworkLogRowOut)
            }}</span>
          </template>

          <template v-else-if="column.key === 'operation_no'">
            {{ record.operation_no }} {{ record.operation_name ?? '' }}
          </template>

          <template v-else-if="column.key === 'bundle_no'">
            <span class="code-cell" :title="record.bundle_no">{{ record.bundle_no }}</span>
          </template>

          <template v-else-if="column.key === 'color_size'">
            {{ record.color_name ?? '—' }} / {{ record.size_code ?? '—' }}
          </template>

          <template v-else-if="column.key === 'hands'">
            {{ handsText(record as PieceworkLogRowOut) }}
          </template>

          <template v-else-if="column.key === 'qty'">
            <span class="num">{{ formatQty(record.qty, 0) }}</span>
          </template>

          <template v-else-if="column.key === 'unit_price'">
            <span class="num">¥{{ formatUnitPrice(record.unit_price) }}</span>
          </template>

          <template v-else-if="column.key === 'amount'">
            <span class="num">¥{{ formatMoney(record.amount, 2) }}</span>
          </template>

          <template v-else-if="column.key === 'log_type'">
            <Tag :color="LOG_TYPE_LABEL[record.log_type]?.color ?? 'default'">
              {{ LOG_TYPE_LABEL[record.log_type]?.text ?? record.log_type }}
            </Tag>
          </template>

          <template v-else-if="column.key === 'is_settled'">
            <span v-if="record.is_settled" class="cell-settled">已结算</span>
            <span v-else class="cell-muted">未结算</span>
          </template>

          <template v-else-if="column.key === 'actions'">
            <Button
              v-can="PERM.PIECEWORK_READ"
              size="small"
              type="link"
              @click="openDetail(record as PieceworkLogRowOut)"
            >
              详情
            </Button>
          </template>
        </template>

        <template #emptyText>
          <EmptyState
            v-if="list.isEmpty"
            title="还没有计件流水"
            hint="工位机扫码计件后，流水会出现在这里；也可以放宽筛选条件"
            secondary-action-text="清空筛选"
            @secondary-action="onReset"
          />
          <EmptyState
            v-else-if="list.hasError"
            title="计件流水加载失败"
            :hint="list.error?.message ?? '请稍后重试'"
            action-text="重试"
            @action="list.reload()"
          />
        </template>
      </Table>
    </Spin>

    <LogDetailDrawer :log-id="detailId" @close="detailId = null" />
  </PageLayout>
</template>

<style scoped>
.filter-hint,
.toolbar-hint,
.cell-muted {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.cell-settled {
  color: var(--color-success);
}

.code-cell {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  max-width: 210px;
  font-family: var(--font-mono);
}

.num {
  font-variant-numeric: tabular-nums;
}
</style>
