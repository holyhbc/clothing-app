<script setup lang="ts">
/**
 * 裁剪单列表页（T-CUT-001c-2）。
 *
 * ## 这一页只做「找到单子 + 打开详情」
 *
 * 三层明细的**增删改与新建在 T-CUT-001c-3**。这里刻意**不放**新建按钮、不放行内编辑、
 * 不放审核按钮、不放导出：
 *
 * - 行内编辑要处理 `version` 乐观锁与全量替换的语义（一行保存失败时其他行怎么办），
 *   在列表里塞进去等于把详情页的逻辑复制一份，两处迟早不同步。
 * - 审核 / 反审核是**状态迁移**，后端要过前置状态校验（`docs/08`）；页面上一个
 *   「审核」按钮如果漏了前置提示，用户点完只看到一句 `10004`。
 * - 导出等后端 `/exports` 端点与 `useExport` 一起上（T-CUT-001c-3）。
 *
 * ## 为什么筛选是「状态 + 款号 + 车间 + 日期」而不是一个模糊搜索框
 *
 * 后端 `list_cutting_orders` 只有等值筛选：`style_no` 是**精确匹配**，没有 `q`。
 * 所以这里**不放搜索框** —— 放一个看着像模糊搜、实际只匹配全等的输入框比不放更糟：
 * 用户输 `A-12` 搜不到 `A-1234` 的单，会以为单丢了。款号改用候选下拉。
 *
 * ## 表里为什么没有「车间」「颜色」两列
 *
 * 后端 `CuttingOrderListOut` 只给了 `workshop_id`（**UUID**）与 `style_no`，
 * **没有车间名、没有 `color_codes`**（色码汇总只在 `CuttingOrderOut` 上）。
 * 把 UUID 显示给用户毫无意义，而在列表里补这两列要**动后端 DTO** —— 那属于
 * T-CUT-001c-3 的范围（连同详情页一起做），这一版刻意只渲染后端真的给了的字段，
 * 不在前端编一个不存在的字段。车间筛选仍然可用（候选取 `optionsById`，value 是 UUID）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, DatePicker, Select, Space, Spin, Table, Tooltip } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { PERM, formatDate, formatDateTime, formatQty } from '@garment/shared'
import type { CuttingOrderListOut } from '@garment/shared'
import { baseApi } from '@/api/base'
import { listCuttingOrders } from '@/api/cutting'
import type { CuttingOrderListQuery } from '@/api/cutting'
import { searchStyleOptions } from '@/api/styles'
import { usePageList } from '@/composables/usePageList'
import EmptyState from '@/components/EmptyState.vue'
import PageLayout from '@/components/PageLayout.vue'
import StatusTag from '@/components/StatusTag.vue'
import TableToolbar from '@/components/TableToolbar.vue'

defineOptions({ name: 'CuttingOrderList' })

const router = useRouter()

const list = usePageList<CuttingOrderListQuery, CuttingOrderListOut>({
  fetch: listCuttingOrders,
  initialQuery: { page: 1, size: 20 },
})

const statusFilter = ref<string | undefined>(undefined)
const styleFilter = ref<string | undefined>(undefined)
const workshopFilter = ref<string | undefined>(undefined)
/**
 * 存 dayjs（DatePicker 的原生类型），提交前转字符串。
 *
 * ⚠️ 空值是 **`null`** 而不是 `undefined`：antd 的 `RangeValue` 定义就是
 * `[...,...] | null`，而本仓开了 `exactOptionalPropertyTypes` —— 给一个可选 prop
 * 显式传 `undefined` 是编译错误（「有这个键但没值」）。
 */
const dateRange = ref<[Dayjs, Dayjs] | null>(null)

/**
 * RangePicker 的 `value` 只能整包传。
 *
 * ⚠️ 本仓开了 `exactOptionalPropertyTypes`，而 antd 把 RangePicker 的 `value` 标成
 *    `[string,string] | [Dayjs,Dayjs]`（**不含 `null`**）—— 于是 `:value="dateRange"`
 *    在清空时是编译错误，「不传」与「传 null」都被类型系统挡住。用 `v-bind` 一个
 *    **要么没有 `value` 键**的对象才是唯一正确写法：组件自己会用内部空值。
 */
const dateRangeProps = computed<Record<string, unknown>>(() =>
  dateRange.value === null ? {} : { value: dateRange.value },
)

const styleOptions = ref<{ value: string; label: string }[]>([])
const workshopOptions = ref<{ value: string; label: string }[]>([])

/**
 * 状态候选。
 *
 * ⚠️ 取值与后端 `DocumentStatus` 一致（由 `openapi.json` → shared 生成），
 * 页面里不重新定义取值（`docs/06 §7`）。`PAID` 在裁剪单里不会出现但也不列 ——
 * 多列一档无害，少列一档会让「筛出来是空的」，而那正是用户最需要看到筛选生效的时刻。
 */
const statusOptions = computed(() => [
  { value: 'DRAFT', label: '草稿' },
  { value: 'SUBMITTED', label: '待审核' },
  { value: 'APPROVED', label: '已审核' },
  { value: 'REJECTED', label: '已驳回' },
  { value: 'CANCELLED', label: '已作废' },
])

/**
 * 款号候选。
 *
 * ⚠️ `/styles/options` 的 `value` 是**款号字符串**（05 §9.5.2：value = 业务编码），
 * 而列表筛选用 `style_no`（也是款号字符串）—— 两边天然对齐，**不要**用
 * `optionsById`（那个给的是 UUID，提交 `style_no=A-12` 会查不到任何单）。
 */
async function loadStyleOptions(): Promise<void> {
  try {
    styleOptions.value = (await searchStyleOptions('')).map((option) => ({
      value: option.value,
      label: option.label,
    }))
  } catch {
    styleOptions.value = []
  }
}

/**
 * 车间候选。
 *
 * ⚠️ **必须用 `optionsById`**（value = UUID）：列表筛选参数是 `workshop_id`（UUID），
 * 用 `/options`（value = 车间编码）会传错值 —— 不报错，只是永远筛不出结果。
 */
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

/** dayjs → `YYYY-MM-DD`（后端收 `date`，docs/05 §3）。 */
function toDateString(value: Dayjs): string {
  return value.format('YYYY-MM-DD')
}

function onSearch(): void {
  list.applyFilter({
    status: statusFilter.value,
    style_no: styleFilter.value,
    workshop_id: workshopFilter.value,
    doc_date_from: dateRange.value ? toDateString(dateRange.value[0]) : undefined,
    doc_date_to: dateRange.value ? toDateString(dateRange.value[1]) : undefined,
  })
}

function onReset(): void {
  statusFilter.value = undefined
  styleFilter.value = undefined
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

function onWorkshopChange(raw: unknown): void {
  workshopFilter.value = typeof raw === 'string' && raw !== '' ? raw : undefined
  onSearch()
}

/**
 * 日期区间。
 *
 * ⚠️ **收窄而不是 `as`**：`raw` 是 `unknown`，断言等于告诉编译器「我保证它是对的
 * 两元组」，而实际可能是 `null`（清空）—— 下游取下标就会炸。docs/06 §7 禁 `any`
 * 逃逸，断言逃逸是同一种病。
 *
 * ⚠️ 清空时 `doc_date_from/to` 传 `undefined` 而不是空串：后端 `date | None` 收到
 * 空串是 422，而界面上「清空」正是用户期待的「不筛」。
 */
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
  void router.push({ name: 'cutting-orders-detail', params: { orderId } })
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
  showTotal: (value: number) => `共 ${value} 张裁剪单`,
}))

const columns: ColumnsType<CuttingOrderListOut> = [
  { key: 'doc_no', title: '裁剪单号', dataIndex: 'doc_no', width: 160 },
  { key: 'doc_date', title: '单据日期', dataIndex: 'doc_date', width: 110 },
  { key: 'status', title: '状态', dataIndex: 'status', width: 100 },
  { key: 'style_no', title: '款号', dataIndex: 'style_no', width: 140 },
  { key: 'ply_count', title: '层数', dataIndex: 'ply_count', width: 80 },
  { key: 'fabric_qty', title: '用布量', dataIndex: 'fabric_qty', width: 110 },
  { key: 'output_qty', title: '出数', dataIndex: 'output_qty', width: 100 },
  { key: 'balance_qty', title: '尾数', dataIndex: 'balance_qty', width: 90 },
  { key: 'hands_total', title: '计件手数', dataIndex: 'hands_total', width: 110 },
  { key: 'created_at', title: '创建时间', dataIndex: 'created_at', width: 110 },
  { key: 'actions', title: '操作', width: 100, fixed: 'right' },
]

onMounted(() => {
  void list.reload()
  void loadStyleOptions()
  void loadWorkshopOptions()
})
</script>

<template>
  <PageLayout title="裁剪单" :description="`共 ${list.total} 张裁剪单`">
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
            :value="workshopFilter"
            placeholder="按车间"
            style="width: 150px"
            allow-clear
            :options="workshopOptions"
            @change="onWorkshopChange"
          />
          <!--
            ⚠️ RangePicker 的值是 **dayjs 数组**，直接提交会被 JSON 序列化成带时区偏移的
            ISO 串，后端 `date` 解析直接 422 —— 所以存 dayjs、提交前 format。
          -->
          <DatePicker.RangePicker
            v-bind="dateRangeProps"
            style="width: 240px"
            @update:value="onDateRangeChange"
          />
          <Button type="primary" @click="onSearch">查询</Button>
          <Button @click="onReset">重置</Button>
        </Space>
        <span v-if="collapsed === false" class="filter-hint">
          款号与车间为精确匹配（后端无模糊搜索）；日期区间含首尾两天
        </span>
      </Space>
    </template>

    <template #toolbar>
      <TableToolbar :selected-count="0" :total="list.total">
        <template #actions="{ enabled }">
          <span v-if="!enabled" class="toolbar-hint">
            明细编辑、新建、审核与导出在 T-CUT-001c-3 落地；点「详情」看三层结构
          </span>
        </template>
      </TableToolbar>
    </template>

    <Spin :spinning="list.loading">
      <Table
        :columns="columns"
        :data-source="list.items"
        :row-key="(row: CuttingOrderListOut) => row.id"
        size="small"
        :pagination="pagination"
        :scroll="{ x: 1500 }"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'doc_no'">
            <!-- 单号是业务编码：不换行 + 等宽 + title 给全（docs/06 §2.2） -->
            <span class="code-cell" :title="record.doc_no">{{ record.doc_no }}</span>
          </template>

          <template v-else-if="column.key === 'status'">
            <!--
              ⚠️ 状态**必须走 `StatusTag`**：色值是全系统唯一映射来源
              （docs/06 §1「状态色映射固定」），页面里写 `<Tag :color="...">` 就会出现
              第二个真相 —— 已作废的删除线尤其容易漏。
            -->
            <StatusTag :status="record.status" />
          </template>

          <template v-else-if="column.key === 'output_qty'">
            {{ formatQty(record.output_qty, 0) }}
          </template>

          <template v-else-if="column.key === 'balance_qty'">
            <!--
              尾数为 0 是常态（出数正好被尺码吃满），显示「—」而不是「0」：
              尾数是**要被关注的**数字（不足件，不入库 / 不出码 / 不计件），
              满屏 0 会让它淹没在正常值里。
            -->
            <span v-if="Number(record.balance_qty) > 0" class="cell-warn">
              {{ formatQty(record.balance_qty, 0) }}
            </span>
            <span v-else class="cell-muted">—</span>
          </template>

          <template v-else-if="column.key === 'fabric_qty'">
            {{ formatQty(record.fabric_qty, 3) }}
          </template>

          <template v-else-if="column.key === 'created_at'">
            <Tooltip :title="formatDateTime(record.created_at)">
              <span>{{ formatDate(record.created_at) }}</span>
            </Tooltip>
          </template>

          <template v-else-if="column.key === 'actions'">
            <Button
              v-can="PERM.CUTTING_READ"
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
            title="还没有裁剪单"
            hint="新建裁剪单在 T-CUT-001c-3 落地（表头 + 三层明细一次提交）；在此之前可以先用筛选定位已有的单"
            secondary-action-text="清空筛选"
            @secondary-action="onReset"
          />
          <EmptyState
            v-else-if="list.hasError"
            title="裁剪单列表加载失败"
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

/* ⚠️ 尾数非 0 用警示色：它是「要注意」的信号（docs/06 §1 只对状态定色，其余语义色同理）。 */
.cell-warn {
  font-weight: 600;
  color: var(--color-warning);
}
</style>
