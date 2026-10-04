<script setup lang="ts">
/**
 * 尺码比例：按颜色切换的**手数矩阵**（docs/modules/01 §3.6 的 `style_color_size_ratios`）。
 *
 * ## 比例是「建议值」（ADR-0014 / REV-2026-10 第三批）
 *
 * 它是**客户的排版要求**，与款号同生命周期；裁剪单只在「带出建议」时读它，
 * **件数一律由裁剪单行的 `hands × qty_per_hand` 算出**。所以：
 *
 * - ⚠️ **部分尺码缺配只提示不拦**（黄色的「去补比例」），只有该颜色**完全**没配比例时
 *   裁剪才报 `20006`（那是裁剪侧的判定，这里看不到也不该拦）
 * - ⚠️ **不做「偏离建议 >20%」提示** —— 那是**裁剪单页**的事（带出手数后与建议值偏离时
 *   提示），而这一页维护的**就是**建议值本身，没有"输入值"可以偏离。任务卡原来写了这一条，
 *   核对 ADR-0020 原文后确认放错位置，已从验收标准划掉。
 *
 * ## 保存是**全量替换**（按 `(style_no, color_code)`）
 *
 * ⚠️ 所以必须传**款号的 `version`**（聚合行），而不是比例行的 —— 全量替换会把旧行删光，
 *    子表自己的 version 每次从 1 重来，拿它当乐观锁等于没有锁。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert,
  Button,
  Card,
  Empty,
  InputNumber,
  Select,
  Space,
  Spin,
  Table,
  Typography,
  message,
} from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { PERM } from '@garment/shared'
import type { RatioListOut, StyleDetailOut } from '@garment/shared'
import { getStyle, getStyleRatios, replaceStyleRatios } from '@/api/styles'

defineOptions({ name: 'StyleRatios' })

const route = useRoute()
const router = useRouter()

const styleNo = computed(() => {
  const raw = route.params['styleNo']
  return typeof raw === 'string' && raw !== '' ? raw : null
})

const detail = ref<StyleDetailOut | null>(null)
const currentColor = ref<string | null>(null)
const payload = ref<RatioListOut | null>(null)
const loading = ref(false)
const saving = ref(false)
const errorText = ref<string | null>(null)

/** 编辑中的手数：`size_code` → 手数。 */
const draft = ref<Record<string, number | null>>({})

const colors = computed(() => detail.value?.colors ?? [])
const sizeCodes = computed(() => (detail.value?.sizes ?? []).map((row) => row.size_code))
const sizeCodeRows = computed<SizeCodeRow[]>(() =>
  sizeCodes.value.map((code) => ({ size_code: code })),
)
const styleVersion = computed(() => detail.value?.style.version ?? 1)

async function loadDetail(): Promise<void> {
  if (styleNo.value === null) return
  detail.value = await getStyle(styleNo.value)
  // 默认选第一个颜色：这一页没有颜色就没什么可配的
  const first = detail.value.colors[0]
  if (currentColor.value === null && first !== undefined) {
    currentColor.value = first.color_code
  }
}

async function loadRatios(): Promise<void> {
  if (styleNo.value === null || currentColor.value === null) return
  loading.value = true
  errorText.value = null
  try {
    payload.value = await getStyleRatios(styleNo.value, currentColor.value)
    // ⚠️ 查询**永远返回空集**（缺配不抛 `20006`），所以空数组是正常状态而不是错误
    draft.value = Object.fromEntries(
      payload.value.items.map((item) => [item.size_code, Number(item.ratio)]),
    )
  } catch (caught) {
    errorText.value = caught instanceof Error ? caught.message : '读取比例失败'
    payload.value = null
  } finally {
    loading.value = false
  }
}

/** 手数合计（Σratio）。**不是整数** —— 1.5 手是合法的。 */
const handsTotal = computed(() => {
  const values = Object.values(draft.value).filter((value): value is number => value !== null)
  if (values.length === 0) return '0'
  // ⚠️ `formatRatio` 只接一个参数（它是「比例格式化」不是「带精度的格式化」）；
  //    手数合计要 4 位小数就直接 toFixed —— 这里用 formatQty 才对，但 formatQty 是
  //    数量语义。所以用 MoneyText 那套不行，手写 String(...) 交给表格的等宽字体显示。
  return values.reduce((sum, value) => sum + value, 0).toFixed(4)
})

/** 该款已定义但这个颜色没配比例的尺码（只提示不拦，ADR-0014）。 */
const missingSizeCodes = computed(() => payload.value?.missing_size_codes ?? [])

interface SizeCodeRow {
  size_code: string
}

const ratioColumns: ColumnsType<SizeCodeRow> = [
  { key: 'size_code', title: '尺码码', dataIndex: 'size_code', width: 140 },
  { key: 'ratio', title: '手数（建议值）', width: 200 },
]

/**
 * 表格行键。
 *
 * ⚠️ 必须是**具名函数**：模板表达式里写不了对象类型标注
 *    （`:row-key="(row: { size_code: string }) => …"` 里的 `{…}` 被当成 JS 块，
 *    报 `TS1005: ',' expected`）。
 */
function sizeRowKey(row: SizeCodeRow): string {
  return row.size_code
}

async function save(): Promise<void> {
  if (saving.value || styleNo.value === null || currentColor.value === null) return
  const items = Object.entries(draft.value)
    .filter(([, value]) => value !== null)
    .map(([sizeCode, value]) => ({ size_code: sizeCode, ratio: String(value) }))
  if (items.length === 0) {
    void message.warning('至少给一个尺码配手数')
    return
  }
  saving.value = true
  try {
    await replaceStyleRatios({
      style_no: styleNo.value,
      color_code: currentColor.value,
      // ⚠️ 款号聚合行的 version，不是比例行的（全量替换会让子表 version 每次从 1 重来）
      version: styleVersion.value,
      items,
    })
    void message.success('已保存比例')
    await loadRatios()
  } catch (caught) {
    void message.error(caught instanceof Error ? caught.message : '保存比例失败')
  } finally {
    saving.value = false
  }
}

watch([styleNo, currentColor], () => {
  void loadRatios()
})

onMounted(async () => {
  try {
    await loadDetail()
    await loadRatios()
  } catch (caught) {
    errorText.value = caught instanceof Error ? caught.message : '加载款号失败'
  }
})
</script>

<template>
  <div class="ratios-page">
    <Card :bordered="false" class="ratio-card">
      <Space direction="vertical" size="middle" style="width: 100%">
        <div class="page-header">
          <Typography.Title :level="4" style="margin: 0">
            尺码比例 · <span class="code-cell">{{ styleNo }}</span>
          </Typography.Title>
          <Space>
            <Select
              :value="currentColor ?? undefined"
              style="width: 200px"
              placeholder="选择颜色"
              :options="
                colors.map((row) => ({
                  value: row.color_code,
                  label: `${row.color_group} / ${row.color_code} ${row.color_name}`,
                }))
              "
              @update:value="
                (value: unknown) => (currentColor = typeof value === 'string' ? value : null)
              "
            />
            <Button @click="router.back()">返回</Button>
          </Space>
        </div>

        <Alert
          type="info"
          show-icon
          message="比例是「建议值」，裁剪单只在带出建议时读它"
          description="件数一律由裁剪单行的「手数 × 每手件数」算出。部分尺码缺配只提示不拦 —— 只有该颜色完全没有比例时，裁剪才会报 20006。"
        />

        <Alert v-if="errorText" type="error" :message="errorText" show-icon />

        <Spin :spinning="loading">
          <template v-if="colors.length === 0">
            <Empty description="这个款号还没有色组行，先去详情页新增色组">
              <Button type="primary" @click="router.back()">返回详情</Button>
            </Empty>
          </template>
          <template v-else-if="sizeCodes.length === 0">
            <Empty description="这个款号还没有尺码，先去详情页新增尺码">
              <Button type="primary" @click="router.back()">返回详情</Button>
            </Empty>
          </template>
          <template v-else>
            <div class="total-bar">
              <span>手数合计</span>
              <strong class="money">{{ handsTotal }}</strong>
              <span class="field-hint">（可小数，如 1.5 手）</span>
            </div>

            <!-- ⚠️ 缺配提示：**黄色**且给「去补」入口，不拦保存 -->
            <Alert
              v-if="missingSizeCodes.length > 0"
              type="warning"
              show-icon
              :message="`${missingSizeCodes.join('、')} 还没配手数`"
              description="部分缺配只提示不拦；但这个颜色完全没配比例时，裁剪单会报 20006。"
            />

            <Table
              :columns="ratioColumns"
              :data-source="sizeCodeRows"
              :row-key="sizeRowKey"
              size="small"
              :pagination="false"
            >
              <template #bodyCell="{ column, record }">
                <InputNumber
                  v-if="column.key === 'ratio'"
                  :value="draft[record.size_code] ?? ''"
                  :min="0.0001"
                  :precision="4"
                  :step="0.5"
                  style="width: 180px"
                  :placeholder="missingSizeCodes.includes(record.size_code) ? '未配置' : '手数'"
                  @update:value="
                    (value: string | number | null) =>
                      (draft = {
                        ...draft,
                        [record.size_code]: value === null ? null : Number(value),
                      })
                  "
                />
              </template>
            </Table>

            <Space>
              <Button v-can="PERM.BASE_UPDATE" type="primary" :loading="saving" @click="save">
                保存 {{ currentColor }} 的比例
              </Button>
              <Typography.Text type="secondary" class="field-hint">
                ⚠️ 保存是**按 (款号, 颜色) 全量替换**：没填的尺码会被清掉
              </Typography.Text>
            </Space>
          </template>
        </Spin>
      </Space>
    </Card>
  </div>
</template>

<style scoped>
.ratio-card {
  box-shadow: var(--shadow-card);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.total-bar {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-primary-bg);
  border-radius: var(--radius-md);
}

.money {
  font-family: var(--font-mono);
  font-size: var(--font-size-xl);
  font-variant-numeric: tabular-nums;
  color: var(--color-primary);
}

.field-hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}
</style>
