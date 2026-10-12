<script setup lang="ts">
/**
 * 打菲单详情页的「按手列表」= **真实的打菲码**（T-BUND-010）。
 *
 * ## 为什么单独一个文件
 *
 * 原来这块在 `Detail.vue` 里是个**占位**：直接拿明细行（`LineOut`）当码渲染，
 * 页面自己都写着「此处应通过 `GET /bundles?doc_id={orderId}` 获取完整码列表」。
 * 两者不是一回事 ——
 *
 * | | 明细行（`LineOut`） | 码（`BundleListOut`） |
 * | --- | --- | --- |
 * | 一行代表 | 一个**尺码**（XL 打 2 手 = 1 行） | 一**手**（2 行） |
 * | 何时有 | 建单就有 | **审核后**才生成 |
 * | 有打菲号吗 | 没有 | 有 `bundle_no`（标签/扫码都靠它） |
 *
 * 占位版看着像码列表、实际一行手都没有，于是「审核后码数 = 手数」这条
 * 全系统最核心的不变量（B21）在界面上**根本验不了** —— 单测验不了（它把
 * `/bundles` 整个 mock 掉了），E2E 也只能验到 `hands_total` 那个汇总数。
 *
 * ## 拉取时机
 *
 * ⚠️ 靠 `reloadKey`（父级传单据 `version`）而不是「挂载时拉一次」：
 *   审核是**在当前页面**点按钮完成的，`Detail.vue` 重新 `load()` 单据后状态
 *   从「待审核」变成「已审核」，码这时才出现 —— 组件若只在挂载时拉一次，
 *   页面会一直停在「审核后才有码」的空态，直到用户手动刷新。
 *   单据每次状态迁移 `version` 必 +1（乐观锁），所以它是可靠的信号。
 */
import { onMounted, ref, watch } from 'vue'
import { Table, Tooltip } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { formatQty } from '@garment/shared'
import type { BundleListOut } from '@garment/shared'
import { listBundlesByOrder } from '@/api/bundling'

defineOptions({ name: 'BundlingHandsTable' })

const props = defineProps<{
  orderId: string
  /** 单据版本号；每次状态迁移 +1，用来触发重拉（见文件头「拉取时机」）。 */
  reloadKey: number
}>()

const bundles = ref<BundleListOut[]>([])
const loading = ref(false)
const failed = ref('')

async function load(): Promise<void> {
  if (props.orderId === '') return
  loading.value = true
  failed.value = ''
  try {
    // ⚠️ **不过滤 status**：作废的码也要看得见。B12「码不可恢复」——
    //   一张单上曾经打过几手、其中一手作废了，界面上必须看得出少了哪一手；
    //   只列 ACTIVE 的话这个信息就彻底消失了。
    bundles.value = (await listBundlesByOrder(props.orderId)).items
  } catch (error) {
    failed.value = error instanceof Error ? error.message : '加载打菲码失败'
    bundles.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
watch(() => [props.orderId, props.reloadKey], () => void load())

/** 「有没有计件」看 `counted_at`，不看 `counted_qty` */
function countedText(record: BundleListOut): string {
  // ⚠️ 契约里 `counted_at` 是 `unknown | null`（OpenAPI 的 `format: date-time`
  //   到 pydantic 导出时降级成了 `Any`），所以 `== null` 与 `=== undefined`
  //   两种「没有」都要排掉 —— 只判一个会漏。
  const at = record.counted_at
  if (at === null || at === undefined) return '未计件'
  return `${record.counted_by_name ?? '—'} · ${formatQty(record.counted_qty, 0)} 件`
}

const columns: ColumnsType<BundleListOut> = [
  {
    key: 'hands_text',
    title: '手号',
    width: 160,
    // 「第 N 手 / 共 M 手」——与标签上印的文案同一口径（03 §11.5.2）。
    customRender: ({ record }: { record: BundleListOut }) =>
      `第 ${record.hands} 手 / 共 ${record.hands_total_of_size} 手`,
  },
  { key: 'bundle_no', title: '打菲号', dataIndex: 'bundle_no', width: 240 },
  { key: 'size_code', title: '尺码', dataIndex: 'size_code', width: 70 },
  { key: 'color_code', title: '色码', dataIndex: 'color_code', width: 80 },
  { key: 'operation_no', title: '工序', dataIndex: 'operation_no', width: 80 },
  {
    key: 'bundle_qty',
    title: '件数',
    dataIndex: 'bundle_qty',
    width: 90,
    align: 'right',
    customRender: ({ record }: { record: BundleListOut }) => formatQty(record.bundle_qty, 0),
  },
  {
    key: 'counted',
    title: '计件',
    width: 140,
    customRender: ({ record }: { record: BundleListOut }) => countedText(record),
  },
]

/** 作废的码：整行压暗 + 划掉（docs/06 §1「已作废加删除线」）。 */
function rowClass(record: BundleListOut): string {
  return record.status === 'VOIDED' ? 'row-voided' : ''
}
</script>

<template>
  <div class="hands-table">
    <p v-if="failed !== ''" class="hint hint--error">{{ failed }}</p>
    <p v-else-if="!loading && bundles.length === 0" class="hint">
      ⚠️ 一码 = 一手 = 一个员工；打菲码在**审核**时才生成，当前还没有码。
    </p>
    <Table
      :columns="columns"
      :data-source="bundles"
      :row-key="(row: BundleListOut) => row.bundle_no"
      :row-class-name="rowClass"
      :loading="loading"
      size="small"
      :pagination="false"
      :scroll="{ x: 860 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'bundle_no'">
          <Tooltip :title="record.qr_content">
            <span class="code-cell">{{ record.bundle_no }}</span>
          </Tooltip>
        </template>
      </template>

      <!-- eslint-disable vue/valid-v-slot -->
      <template #emptyText>
        <span class="hint">暂无打菲码</span>
      </template>
      <!-- eslint-enable vue/valid-v-slot -->
    </Table>
  </div>
</template>

<style scoped>
.hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
  margin-bottom: var(--space-2);
}

.hint--error {
  color: var(--color-danger);
}

.code-cell {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  max-width: 220px;
  font-family: var(--font-mono);
}

/* 作废行：整行压暗并划掉 —— 「这张码已经废了」要一眼看得出 */
:deep(.row-voided) {
  opacity: 0.55;
  text-decoration: line-through;
}
</style>