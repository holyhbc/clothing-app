<script setup lang="ts">
/**
 * 工序单价：区间列表 + 设价 / 调价（ADR-0026 三档、ADR-0009 调价口径）。
 *
 * ## 三条不写清楚就会出事的口径
 *
 * 1. **只追加，永不改历史价**（R11 / INV-P0-3）：调价 = 旧行只关 `effective_to` +
 *    插入新区间。所以「改今天的价」必须换一个生效日 —— 界面给的是"生效日"而不是
 *    "单价"单字段修改。
 * 2. **调价必填原因**（R20 → `10006`）：后端会拒，但界面上先禁掉更好（TC-W35 同类）。
 *    ⚠️ **首次设价可以空** —— 后端只在"真的要关旧区间"时才要求原因。
 * 3. **必须显示 `rate_source`**（ADR-0020 后果列强制）：款号价 / 分类价 / 工序通用价
 *    三档的适用范围完全不同，看不出档位的单价表等于没有。
 *
 * ## 「被款号价覆盖」的标记怎么算
 *
 * ADR-0026 §2 的取价优先级是 `款号价 > 分类价 > 工序通用价`。所以**同一道工序**下，
 * 只要存在 `rate_source=STYLE` 且 `is_current` 的行，同工序的 `CATEGORY` / `OPERATION`
 * 当前行就**不生效**（被遮蔽）。这个判定用**当前页数据**算，不额外调 `resolve` ——
 * 后端那个接口是给"按工作日预演"用的，列表页逐行调它会把 N 行变成 N 次请求。
 *
 * ⚠️ 页面上要说清"标记只覆盖当前页看到的行"：翻页后别处的行不在判定范围内。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert,
  Button,
  DatePicker,
  Form,
  FormItem,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import dayjs from 'dayjs'
import type { Dayjs } from 'dayjs'
import { PERM, formatDate, formatMoney } from '@garment/shared'
import type { OperationRateOut, RateSource } from '@garment/shared'
import { baseApi } from '@/api/base'
import { listRates, searchStyleOptions, setOperationRate } from '@/api/styles'
import { usePageList } from '@/composables/usePageList'
import Combo from '@/components/Combo.vue'
import EmptyState from '@/components/EmptyState.vue'
import PageLayout from '@/components/PageLayout.vue'

defineOptions({ name: 'OperationRateList' })

const route = useRoute()
const router = useRouter()

/**
 * ⚠️ 必须 `extends Record<string, QueryValue>`：`usePageList<TQuery extends PageQuery>`
 *    要求 TQuery 是索引签名类型，否则 `applyFilter({style_no})` 传不进去。
 */
interface RateQuery extends Record<string, string | number | boolean | null | undefined> {
  style_no?: string
  operation_no?: string
  page: number
  size: number
}

const list = usePageList<RateQuery, OperationRateOut>({
  fetch: listRates,
  initialQuery: { page: 1, size: 20 },
})

/** 档位中文（取值来自后端枚举，前端只做中文映射，docs/06 §1）。 */
const RATE_SOURCE_LABELS: Readonly<Record<RateSource, string>> = {
  STYLE: '款号价',
  CATEGORY: '分类价',
  OPERATION: '工序通用价',
}

function rateSourceLabel(source: RateSource | null | undefined): string {
  return source === null || source === undefined ? '—' : RATE_SOURCE_LABELS[source]
}

/**
 * 被遮蔽的档位：同工序下有款号价当前行时，分类价 / 工序通用价的当前行都不生效。
 *
 * ⚠️ 只看**当前页**的行 —— 翻页后不在判定范围内的行不会被标记，页面上如实说明。
 */
const shadowedRows = computed(() => {
  const styleOps = new Set(
    list.items
      .filter((row) => row.is_current && row.rate_source === 'STYLE')
      .map((row) => row.operation_no),
  )
  const shadowed = new Set<string>()
  for (const row of list.items) {
    if (!row.is_current) continue
    if (row.rate_source !== 'CATEGORY' && row.rate_source !== 'OPERATION') continue
    if (styleOps.has(row.operation_no)) shadowed.add(row.id)
  }
  return shadowed
})

const columns: ColumnsType<OperationRateOut> = [
  { key: 'style_no', title: '款号', dataIndex: 'style_no', width: 140 },
  { key: 'operation_no', title: '工序号', dataIndex: 'operation_no', width: 110 },
  { key: 'unit_price', title: '单价（元/件）', dataIndex: 'unit_price', width: 140 },
  { key: 'rate_source', title: '档位', dataIndex: 'rate_source', width: 130 },
  { key: 'range', title: '生效区间', width: 210 },
  { key: 'is_current', title: '状态', dataIndex: 'is_current', width: 130 },
  { key: 'reason', title: '原因', dataIndex: 'reason', width: 160 },
]

/**
 * 表格行键。⚠️ 必须是具名函数：antd 把 columns 泛型写成 `any` 后插槽里的 record 是
 * `Record<string, any>`，而模板表达式里写不了类型标注（`(row: X) => …` 报 TS1109）。
 */
function rateRowKey(row: unknown): string {
  return String((row as Record<string, unknown>)['id'] ?? '')
}

/**
 * 收口 antd 插槽里的 `record`。
 *
 * ⚠️ 与系统管理两页同一个理由：antd 把 Table 的 columns 泛型在 props 上写死成
 * `any`，插槽里的 `record` 就是 `Record<string, any>` —— 不收口的话模板里任何一处
 * 字段名笔误都不会报红，而症状是**那一列是空的**（页面上零报错）。
 */
function asRate(record: unknown): OperationRateOut {
  return record as OperationRateOut
}

/** 生效区间（`effective_to` 为空 = 长期有效 = 当前档）。 */
function rangeText(row: unknown): string {
  const rate = asRate(row)
  const from = formatDate(rate.effective_from)
  return rate.effective_to === null
    ? `${from} 起（长期）`
    : `${from} ~ ${formatDate(rate.effective_to)}`
}

// ------------------------------------------------------------------ 设价 / 调价

const modalOpen = ref(false)
const saving = ref(false)
const submitError = ref<string | null>(null)

/** 档位：三选一。⚠️ `style_no` 与 `product_category_id` **不能同时给**（ADR-0026）。 */
type RateTier = 'STYLE' | 'CATEGORY' | 'OPERATION'

const form = ref({
  tier: 'STYLE' as RateTier,
  style_no: null as string | null,
  product_category_id: null as string | null,
  operation_no: null as string | null,
  unit_price: null as number | null,
  effective_from: null as string | null,
  reason: '',
})

/** `effective_from` 存字符串（后端要 `YYYY-MM-DD`），DatePicker 要 dayjs —— 双向转换。 */
const effectiveFromDayjs = computed<Dayjs | ''>(() =>
  form.value.effective_from === null ? '' : dayjs(form.value.effective_from),
)

const categoryOptions = ref<{ value: string; label: string }[]>([])

/**
 * 工序候选。
 *
 * ⚠️ `/options`（`value` = **工序号**）而不是 `optionsById`（`value` = UUID）：
 *    `OperationRateCreate.operation_no` 是工序号字符串，写 UUID 会被后端 10001 拒。
 */
function searchOperations(keyword: string) {
  return baseApi.operations.options(keyword)
}

function onTierChange(raw: unknown): void {
  form.value.tier = raw === 'CATEGORY' || raw === 'OPERATION' ? raw : 'STYLE'
  // 换档位就清掉另外两档的值 —— 留着会让 payload 里同时出现两个目标维度，
  // 后端 10001（`ck_operation_rates_target` 的语义）
  form.value.style_no = null
  form.value.product_category_id = null
}

/** 调价必填原因（R20）。⚠️ 只在"**已有该档位的行**"时才必填 —— 首次设价后端不要求。 */
const isAdjustment = computed(() =>
  list.items.some((row) => row.operation_no === form.value.operation_no),
)

const canSubmit = computed(
  () =>
    form.value.tier === 'OPERATION' ||
    (form.value.tier === 'STYLE' && form.value.style_no !== null) ||
    (form.value.tier === 'CATEGORY' && form.value.product_category_id !== null),
)

/**
 * dayjs 对象 → `YYYY-MM-DD`（后端要 `date`，docs/05 §3）。
 *
 * ⚠️ **dayjs 已显式列入 `package.json` 依赖**：它本来只是 antd-vue 的传递依赖，而
 *    直接 import 一个不在自己清单里的包，在 pnpm 严格模式下装不上（而本地开发因为
 *    hoisting 能跑 —— 于是「本地好好的、Docker 构建失败」）。antd 的 DatePicker 就是
 *    dayjs 对象，所以这是绕不开的依赖，不如显式声明。
 */
function formatDayjs(value: string | Dayjs): string {
  return typeof value === 'string' ? value : value.format('YYYY-MM-DD')
}

function openModal(): void {
  const queryStyleNo = typeof route.query['style_no'] === 'string' ? route.query['style_no'] : null
  form.value = {
    tier: queryStyleNo === null ? 'OPERATION' : 'STYLE',
    style_no: queryStyleNo,
    product_category_id: null,
    operation_no: null,
    unit_price: null,
    effective_from: null,
    reason: '',
  }
  submitError.value = null
  modalOpen.value = true
}

async function onSubmit(): Promise<void> {
  if (saving.value) return
  saving.value = true
  submitError.value = null
  try {
    const result = await setOperationRate({
      operation_no: form.value.operation_no ?? '',
      // ⚠️ `unit_price` 用字符串传：docs/05 §3 规定金额 / 单价一律字符串
      //    （JS 的 number 表示不了 `0.378000`）
      unit_price: String(form.value.unit_price ?? 0),
      effective_from: form.value.effective_from ?? '',
      style_no: form.value.tier === 'STYLE' ? form.value.style_no : null,
      product_category_id: form.value.tier === 'CATEGORY' ? form.value.product_category_id : null,
      reason: form.value.reason.trim() === '' ? null : form.value.reason.trim(),
    })
    const closed = result.closed_rates ?? []
    void message.success(closed.length > 0 ? `已保存，关闭了 ${closed.length} 个旧区间` : '已保存')
    modalOpen.value = false
    await list.reload()
  } catch (caught) {
    submitError.value = caught instanceof Error ? caught.message : '保存失败'
  } finally {
    saving.value = false
  }
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
  showTotal: (value: number) => `共 ${value} 条单价记录`,
}))

/** 从款号详情跳过来时带上款号筛选。 */
onMounted(() => {
  const queryStyleNo = typeof route.query['style_no'] === 'string' ? route.query['style_no'] : null
  void list.applyFilter({ style_no: queryStyleNo === null ? undefined : queryStyleNo })
  void baseApi['product-categories']
    .optionsById()
    .then((options) => {
      categoryOptions.value = options.map((option) => ({
        value: option.value,
        label: option.label,
      }))
    })
    .catch(() => {
      categoryOptions.value = []
    })
})
</script>

<template>
  <PageLayout title="工序单价" :description="`共 ${list.total} 条单价记录`">
    <template #extra>
      <Button @click="router.push({ name: 'base-styles' })">回款号列表</Button>
      <Button v-can="PERM.PIECEWORK_RATE_MANAGE" type="primary" @click="openModal">
        设价 / 调价
      </Button>
    </template>

    <template #filter="{ collapsed }">
      <Space direction="vertical" size="small" style="width: 100%">
        <Space wrap>
          <!--
            ⚠️ 款号筛选用 `Combo` 而不是 `Select`：款号会累积到几千条，裸下拉必须
            肉眼滚动（docs/06 §10）。候选默认按 `last_used_at DESC`（常用优先）。
          -->
          <Combo
            :model-value="list.query.style_no ?? null"
            :fetch-options="searchStyleOptions"
            placeholder="按款号筛选（留空 = 全部档位）"
            style="width: 260px"
            @update:model-value="
              (value: string | null) => list.applyFilter({ style_no: value ?? undefined })
            "
          />
          <Button @click="list.reload()">刷新</Button>
        </Space>
        <span v-if="collapsed === false" class="filter-hint">
          档位三选一：款号价（限一个款） / 分类价（限一个分类） / 工序通用价（全厂）
        </span>
      </Space>
    </template>

    <template #toolbar>
      <div class="rate-legend">
        <Tag color="blue">款号价</Tag>
        <Tag>分类价</Tag>
        <Tag>工序通用价</Tag>
        <span class="filter-hint">
          取价优先级：款号价 &gt; 分类价 &gt; 工序通用价。被款号价覆盖的行会标「被覆盖」。
        </span>
      </div>
    </template>

    <Table
      :columns="columns"
      :data-source="list.items"
      :row-key="rateRowKey"
      size="small"
      :pagination="pagination"
      :scroll="{ x: 1020 }"
      @change="onTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'style_no'">
          <span v-if="record.style_no" class="code-cell">{{ record.style_no }}</span>
          <span v-else class="cell-muted">全厂</span>
        </template>

        <template v-else-if="column.key === 'unit_price'">
          <span class="money">{{ formatMoney(record.unit_price, 6) }}</span>
        </template>

        <template v-else-if="column.key === 'rate_source'">
          <Tag :color="record.rate_source === 'STYLE' ? 'blue' : 'default'">
            {{ rateSourceLabel(record.rate_source) }}
          </Tag>
          <!--
            ⚠️ 「被覆盖」标记：同一道工序下已经有款号价当前行时，本行不参与取价。
            这是 ADR-0026 §2 三档优先级的直接后果，**必须显示** ——
            不显示的话用户会去改一条根本不生效的分类价，改完发现工资没变。
          -->
          <Tooltip
            v-if="shadowedRows.has(record.id)"
            title="该款号在这道工序上已有款号价，本行不参与取价（款号价 &gt; 分类价 &gt; 工序通用价）"
          >
            <Tag color="warning">被覆盖</Tag>
          </Tooltip>
        </template>

        <template v-else-if="column.key === 'range'">
          <span>{{ rangeText(record) }}</span>
        </template>

        <template v-else-if="column.key === 'is_current'">
          <Tag :color="record.is_current ? 'success' : 'default'">
            {{ record.is_current ? '当前生效' : '历史' }}
          </Tag>
        </template>

        <template v-else-if="column.key === 'reason'">
          <span>{{ record.reason ?? '—' }}</span>
        </template>
      </template>

      <template #emptyText>
        <EmptyState
          v-if="list.isEmpty"
          title="还没有单价记录"
          hint="未设价的工序在计件落库时会报 20004（取价未命中）"
          action-text="去设价"
          @action="openModal"
        />
        <EmptyState
          v-else-if="list.hasError"
          title="单价列表加载失败"
          :hint="list.error?.message ?? '请稍后重试'"
          action-text="重试"
          @action="list.reload()"
        />
      </template>
    </Table>

    <!-- ------------------------------------------------ 设价 / 调价 -->
    <Modal
      :open="modalOpen"
      title="设价 / 调价"
      :confirm-loading="saving"
      :ok-button-props="{ disabled: !canSubmit }"
      @ok="onSubmit"
      @cancel="modalOpen = false"
    >
      <Space direction="vertical" size="small" style="width: 100%">
        <Alert v-if="submitError" type="error" :message="submitError" show-icon />

        <Alert type="info" show-icon>
          <template #message>单价只追加区间，永不改历史价</template>
          <template #description>
            调价 = 旧行关闭生效日 + 插入新区间。所以「改今天的价」要换一个生效日。
          </template>
        </Alert>

        <Form layout="vertical">
          <FormItem label="档位（必选）">
            <Select
              :value="form.tier"
              :options="[
                { value: 'STYLE', label: '款号价（限一个款号）' },
                { value: 'CATEGORY', label: '分类价（限一个分类）' },
                { value: 'OPERATION', label: '工序通用价（全厂）' },
              ]"
              @update:value="onTierChange"
            />
          </FormItem>

          <FormItem v-if="form.tier === 'STYLE'" label="款号（必填）">
            <Combo
              :model-value="form.style_no"
              :fetch-options="searchStyleOptions"
              placeholder="输入货号或款名搜索…"
              @update:model-value="(value: string | null) => (form = { ...form, style_no: value })"
            />
          </FormItem>

          <FormItem v-if="form.tier === 'CATEGORY'" label="商品分类（必填）">
            <Select
              :value="form.product_category_id ?? undefined"
              :options="categoryOptions"
              placeholder="选分类 —— 分类价只对该分类下的款号生效"
              allow-clear
              @update:value="
                (value: unknown) =>
                  (form = {
                    ...form,
                    product_category_id: typeof value === 'string' ? value : null,
                  })
              "
            />
          </FormItem>

          <FormItem label="工序（必填）">
            <Combo
              :model-value="form.operation_no"
              :fetch-options="searchOperations"
              placeholder="输入工序号或名称搜索…"
              @update:model-value="
                (value: string | null) => (form = { ...form, operation_no: value })
              "
            />
          </FormItem>

          <FormItem label="单价（元/件，6 位小数）">
            <InputNumber
              :value="form.unit_price ?? ''"
              :min="0"
              :precision="6"
              :step="0.01"
              style="width: 100%"
              @update:value="
                (value: string | number | null) =>
                  (form = { ...form, unit_price: value === null ? null : Number(value) })
              "
            />
          </FormItem>

          <FormItem label="生效日（必填，含当天）">
            <!--
              ⚠️ `DatePicker` 的值是 **dayjs 对象**，而后端要 `YYYY-MM-DD`。直接传
              dayjs 会被 JSON 序列化成带时区偏移的 ISO 串，后端 `date` 解析直接 422。
            -->
            <DatePicker
              :value="effectiveFromDayjs"
              style="width: 100%"
              placeholder="从哪一天开始生效"
              @update:value="
                (value: string | Dayjs) =>
                  (form = {
                    ...form,
                    effective_from: value === '' || value === null ? null : formatDayjs(value),
                  })
              "
            />
          </FormItem>

          <FormItem :required="isAdjustment" label="调价原因">
            <!--
              ⚠️ **调价必填**（R20 → 10006），首次设价可填可不填。
              后端只在"真的要关旧区间"时要求，所以界面上按 `isAdjustment` 决定红星。
            -->
            <Input
              :value="form.reason"
              :placeholder="isAdjustment ? '调价必填：说明为什么调' : '首次设价可不填'"
              allow-clear
              @update:value="(value: string) => (form = { ...form, reason: value })"
            />
          </FormItem>
        </Form>

        <!--
          ⚠️ 档位必选且目标维度必填 —— `style_no` 与 `product_category_id` 不能同时给
          （ADR-0026 的 `ck_operation_rates_target`），漏了会被 10001 拒。
          没选满时按钮禁用，并把缺什么说清楚。
        -->
        <Typography.Text v-if="!canSubmit" type="secondary" class="filter-hint">
          还差：{{
            form.tier === 'STYLE' ? '款号' : form.tier === 'CATEGORY' ? '商品分类' : '（无需）'
          }}
        </Typography.Text>
      </Space>
    </Modal>
  </PageLayout>
</template>

<style scoped>
.rate-legend {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.filter-hint,
.cell-muted {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.money {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
