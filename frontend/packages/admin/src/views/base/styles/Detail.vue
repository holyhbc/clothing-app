<script setup lang="ts">
/**
 * 款号详情（docs/06 §2.3 的详情页标准结构）：描述列表 + 4 个 Tab + 变更历史抽屉。
 *
 * ## 四个 Tab 与它们各自的写入语义（**不是都"全量替换"**）
 *
 * | Tab | 读 | 写 | 语义 |
 * | --- | --- | --- | --- |
 * | 色组 | `Detail.colors` | `POST /styles/{no}/colors` | **追加** —— 加一行不影响别的 |
 * | 尺码 | `Detail.sizes` | `POST /styles/{no}/sizes` | **追加**（单码或整套带出） |
 * | 款号工序 | `Detail.operations` | `PUT /styles/{no}/operations` | **全量替换** ⚠️ |
 * | 现行价 | `Detail.current_rates` | `POST /operation-rates` | **只追加区间**，永不改历史价 |
 *
 * ⚠️ 款号工序是全量替换：不在 `items` 里的旧行会被删掉。所以那个 Tab 的编辑器必须
 *    以"现有配置"为初值整体提交，而不是"只提交用户改的那几行" —— 后者会删掉其余工序。
 *
 * ## 为什么「最后一道工序」用徽标而不是下拉
 *
 * B-CAT-05 / ADR-0018：业务确认**所有款号最后一道都是整烫**，且这道工序决定成衣入库
 * 时机。所以它是一个**要被看见的事实**，不是可自由选择的开关。后端只校验"至多一道"
 * （`replace_style_operations` 里 `10001`），识别不到就让人工指定 —— 界面必须把它
 * 显示出来，否则成衣入库时机就错了，而症状是"某些款永远不入库"。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Empty,
  Input,
  InputNumber,
  Modal,
  RadioGroup,
  Space,
  Textarea,
  Switch,
  Table,
  TabPane,
  Tabs,
  Tag,
  Typography,
  message,
} from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { PERM, formatMoney } from '@garment/shared'
import type {
  OperationRateOut,
  StyleColorOut,
  StyleDetailOut,
  StyleOperationOut,
  StyleSizeOut,
} from '@garment/shared'
import { baseApi } from '@/api/base'
import {
  addStyleColor,
  addStyleSize,
  disableStyle,
  getStyle,
  replaceStyleOperations,
} from '@/api/styles'
import type { StyleOperationItemIn } from '@/api/styles'
import Combo from '@/components/Combo.vue'
import CopyDialog from '@/views/base/styles/CopyDialog.vue'
import HistoryDrawer from '@/views/base/styles/HistoryDrawer.vue'

defineOptions({ name: 'StyleDetail' })

const route = useRoute()
const router = useRouter()

const styleNo = computed(() => {
  const raw = route.params['styleNo']
  return typeof raw === 'string' && raw !== '' ? raw : null
})

const detail = ref<StyleDetailOut | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)

async function load(): Promise<void> {
  if (styleNo.value === null) return
  loading.value = true
  loadError.value = null
  try {
    detail.value = await getStyle(styleNo.value)
  } catch (caught) {
    loadError.value = caught instanceof Error ? caught.message : '加载款号失败'
    detail.value = null
  } finally {
    loading.value = false
  }
}

const colors = computed<StyleColorOut[]>(() => detail.value?.colors ?? [])
const sizes = computed<StyleSizeOut[]>(() => detail.value?.sizes ?? [])
const operations = computed<StyleOperationOut[]>(() => detail.value?.operations ?? [])
const currentRates = computed<OperationRateOut[]>(() => detail.value?.current_rates ?? [])

/** 款号工序的当前生效版本（`PUT` 全量替换要传）。 */
const styleVersion = computed(() => detail.value?.style.version ?? 1)

/** 档位中文（ADR-0026 三档）。⚠️ 取值来自后端 PG enum，前端只做中文映射。 */
const RATE_SOURCE_LABELS: Readonly<Record<string, string>> = {
  STYLE: '款号价',
  CATEGORY: '分类价',
  OPERATION: '工序通用价',
}

function rateSourceLabel(source: string | null | undefined): string {
  return source === null || source === undefined ? '—' : (RATE_SOURCE_LABELS[source] ?? source)
}

// ------------------------------------------------------------------ 色组（追加）

const colorModalOpen = ref(false)
const colorSaving = ref(false)
const newColor = ref({ color_group: '', color_code: '', color_name: '', material_color_code: '' })

async function saveColor(): Promise<void> {
  if (colorSaving.value || styleNo.value === null) return
  colorSaving.value = true
  try {
    await addStyleColor(styleNo.value, {
      color_group: newColor.value.color_group.trim(),
      color_code: newColor.value.color_code.trim().toUpperCase(),
      color_name: newColor.value.color_name.trim(),
      material_color_code:
        newColor.value.material_color_code.trim() === ''
          ? null
          : newColor.value.material_color_code.trim(),
    })
    void message.success('已新增色组行')
    colorModalOpen.value = false
    newColor.value = { color_group: '', color_code: '', color_name: '', material_color_code: '' }
    await load()
  } catch (caught) {
    void message.error(caught instanceof Error ? caught.message : '新增色组失败')
  } finally {
    colorSaving.value = false
  }
}

const colorColumns: ColumnsType<StyleColorOut> = [
  { key: 'color_group', title: '色组', dataIndex: 'color_group', width: 140 },
  { key: 'color_code', title: '色码', dataIndex: 'color_code', width: 120 },
  { key: 'color_name', title: '色名', dataIndex: 'color_name', width: 160 },
  {
    key: 'material_color_code',
    title: '面料对应色/缸别',
    dataIndex: 'material_color_code',
    width: 180,
  },
]

// ------------------------------------------------------------------ 尺码（追加）

const sizeModalOpen = ref(false)
const sizeSaving = ref(false)
/** 两种互斥模式（modules/01 §5.3）：单码 或 整套带出。 */
const newSize = ref<{
  mode: 'single' | 'group'
  size_code: string
  size_name: string
  size_group_name: string | null
}>({
  mode: 'single',
  size_code: '',
  size_name: '',
  size_group_name: null,
})

async function saveSize(): Promise<void> {
  if (sizeSaving.value || styleNo.value === null) return
  sizeSaving.value = true
  try {
    if (newSize.value.mode === 'single') {
      await addStyleSize(styleNo.value, {
        size_code: newSize.value.size_code.trim().toUpperCase(),
        size_name: newSize.value.size_name.trim(),
      })
    } else {
      // ⚠️ 整套带出：后端按码表的 `sort_order` 批量建，所以这里**不传** size_code
      await addStyleSize(styleNo.value, {
        size_group_name: newSize.value.size_group_name,
      })
    }
    void message.success('已新增尺码')
    sizeModalOpen.value = false
    newSize.value = { mode: 'single', size_code: '', size_name: '', size_group_name: null }
    await load()
  } catch (caught) {
    void message.error(caught instanceof Error ? caught.message : '新增尺码失败')
  } finally {
    sizeSaving.value = false
  }
}

/**
 * 尺码组候选。
 *
 * ⚠️ 必须走 `/options`（`value` = **业务编码**）而不是 `optionsById`（`value` = UUID）：
 *    `StyleSizeCreate.size_group_name` 要的是**码表名**，而尺码组的路径参数就是
 *    `/size-groups/{name}`。拿 UUID 去提交会被后端 10001 拒（找不到那个码表）。
 */
function searchSizeGroups(keyword: string) {
  return baseApi['size-groups'].options(keyword)
}

const sizeColumns: ColumnsType<StyleSizeOut> = [
  { key: 'sort_no', title: '排序', dataIndex: 'sort_no', width: 90 },
  { key: 'size_code', title: '尺码码', dataIndex: 'size_code', width: 120 },
  { key: 'size_name', title: '实际尺码', dataIndex: 'size_name', width: 180 },
]

// ------------------------------------------------------------------ 款号工序（全量替换）

const operationsEditing = ref(false)
const operationsSaving = ref(false)
const draftOperations = ref<StyleOperationItemIn[]>([])

function startEditOperations(): void {
  // ⚠️ 初值必须是**现有配置整体**，不是"用户改的那几行" —— PUT 是全量替换
  draftOperations.value = operations.value.map((row) => ({
    operation_no: row.operation_no,
    sequence: row.sequence,
    bundle_qty: Number(row.bundle_qty),
    is_piecework: row.is_piecework,
    is_final_operation: row.is_final_operation,
    // ⚠️ `remark` 是**可选可空**的（`StyleOperationOut.remark?: string | null`），
    //    原样带上 `undefined` 会让 TS 在 spread 后收窄失败；统一成 `null`（PATCH
    //    的 None 语义是"不改"，见后端 payload_dict）
    remark: row.remark ?? null,
  }))
  operationsEditing.value = true
}

function addOperationRow(): void {
  draftOperations.value = [
    ...draftOperations.value,
    {
      operation_no: '',
      sequence: draftOperations.value.length + 1,
      bundle_qty: 1,
      is_piecework: true,
      is_final_operation: false,
    },
  ]
}

function removeOperationRow(index: number): void {
  draftOperations.value = draftOperations.value.filter((_, position) => position !== index)
  // 重排序号：工序顺序是**业务字段**（1,2,3…），删一行后必须重排，
  // 否则留下的行序号会跳号，而取价/派工按 sequence 排序
  draftOperations.value = draftOperations.value.map((row, position) => ({
    ...row,
    sequence: position + 1,
  }))
}

/** 至少一道整烫（B-CAT-05）。⚠️ **只提示不拦**：规范说"识别不到则标红要求人工指定"，
 *  但"没指定"与"指定错了"在业务后果上不一样（前者是不入库，后者是入库时机错），
 *  这里如实拦下并说清后果。 */
const finalOperationCount = computed(
  () => draftOperations.value.filter((row) => row.is_final_operation).length,
)

const finalOperationMissing = computed(
  () =>
    operationsEditing.value && draftOperations.value.length > 0 && finalOperationCount.value === 0,
)

async function saveOperations(): Promise<void> {
  if (operationsSaving.value || styleNo.value === null) return
  if (draftOperations.value.length === 0) {
    void message.warning('至少配一道工序')
    return
  }
  if (finalOperationMissing.value) {
    void message.warning('必须指定最后一道工序（整烫）：它决定成衣入库时机')
    return
  }
  operationsSaving.value = true
  try {
    await replaceStyleOperations(styleNo.value, {
      version: styleVersion.value,
      items: draftOperations.value.map((row, position) => ({
        ...row,
        operation_no: row.operation_no.trim(),
        sequence: position + 1,
      })),
    })
    void message.success('已保存款号工序')
    operationsEditing.value = false
    await load()
  } catch (caught) {
    void message.error(caught instanceof Error ? caught.message : '保存工序失败')
  } finally {
    operationsSaving.value = false
  }
}

const operationColumns: ColumnsType<StyleOperationOut> = [
  { key: 'sequence', title: '顺序', dataIndex: 'sequence', width: 80 },
  { key: 'operation_no', title: '工序号', dataIndex: 'operation_no', width: 110 },
  { key: 'operation_name', title: '工序名', dataIndex: 'operation_name', width: 150 },
  { key: 'bundle_qty', title: '一扎件数', dataIndex: 'bundle_qty', width: 110, align: 'right' },
  { key: 'is_piecework', title: '计件', dataIndex: 'is_piecework', width: 90 },
  { key: 'is_final_operation', title: '最后一道', dataIndex: 'is_final_operation', width: 110 },
]

/**
 * 工序候选（`Combo`，docs/06 §10 强制：工序会累积到几百条）。
 *
 * ⚠️ 走 `/options`（`value` = **工序号**）：`StyleOperationItemIn.operation_no` 要的是
 *    工序号字符串（`01`/`02`…），不是 UUID。`optionsById` 的 `value` 是主键 —— 用错
 *    的话界面上能选中、能提交，后端 10001 才拒，用户完全看不出是哪错了。
 */
function searchOperations(keyword: string) {
  return baseApi.operations.options(keyword)
}

const rateColumns: ColumnsType<OperationRateOut> = [
  { key: 'operation_no', title: '工序号', dataIndex: 'operation_no', width: 110 },
  { key: 'unit_price', title: '当前单价', dataIndex: 'unit_price', width: 120, align: 'right' },
  { key: 'rate_source', title: '来源', dataIndex: 'rate_source', width: 130 },
  { key: 'effective_from', title: '生效日', dataIndex: 'effective_from', width: 120 },
  { key: 'reason', title: '原因', dataIndex: 'reason', width: 180 },
]

/**
 * 跟单归属的显示。
 *
 * ⚠️ 提到 computed 而不是写在模板里：模板表达式太长时 prettier 会把它折成多行
 * `{{ ... }}`，而 eslint 的 `vue/html-indent` 对折行插值的缩进要求与 prettier 的
 * 折行策略**不一致** —— 两条规则会互相打架（`pnpm format` 改完 `pnpm lint` 就红）。
 * 只有「模板里放得下」才都过。
 */
const merchandiserLabel = computed(() => {
  const row = detail.value?.style
  if (row === undefined || row === null) return '—'
  if (row.merchandiser_name !== null && row.merchandiser_name !== undefined) {
    return row.merchandiser_name
  }
  // 姓名取不到（理论上不会：接口已 LEFT JOIN users.name）时退回 id 判据，
  // 至少让用户知道「指定了人」而不是「未指定」
  return row.merchandiser_id ? '已指定（姓名不可见）' : '未指定'
})

// ------------------------------------------------------------------ 停用 / 复制 / 历史

const copyOpen = ref(false)
const historyOpen = ref(false)

// ------------------------------------------------------------------ 停用

const disableOpen = ref(false)
const disableReason = ref('')
const disabling = ref(false)

/**
 * ⚠️ 停用必须**填原因**（docs/06 §5），而且原因要一直带到提交那一刻：
 * 用户可能打开弹窗后去查了点什么再回来。用 `disableReason` 存而不是把弹窗关掉重来，
 * 那正是「弹窗里的输入被清空，用户以为没填」这类问题的来源。
 */
function openDisable(): void {
  disableReason.value = ''
  disableOpen.value = true
}

/** 没填原因就不让提交 —— 后端也会拒（`10001`），但那时已经点过一次了。 */
const canSubmitDisable = computed(() => disableReason.value.trim() !== '')

async function submitDisable(): Promise<void> {
  if (detail.value === null || !canSubmitDisable.value || disabling.value) return
  disabling.value = true
  try {
    // ⚠️ `version` 必传：款号是共享档案，别人的页面可能刚改过这一行。
    //    后端不匹配回 10003，界面会提示刷新（与编辑态同一条路）
    await disableStyle(detail.value.style.style_no, {
      version: detail.value.style.version,
      reason: disableReason.value.trim(),
    })
    disableOpen.value = false
    void message.success('款号已停用：不允许新建裁剪/打菲单，历史单据照常')
    await load()
  } catch (caught) {
    void message.error(caught instanceof Error ? caught.message : '停用失败')
  } finally {
    disabling.value = false
  }
}

watch(
  () => styleNo.value,
  () => {
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <div class="style-detail">
    <Alert v-if="loadError" type="error" :message="loadError" show-icon>
      <template #action>
        <Button size="small" @click="router.back()">返回列表</Button>
      </template>
    </Alert>

    <Card v-else :bordered="false" class="detail-card">
      <Space direction="vertical" size="middle" style="width: 100%">
        <div class="detail-header">
          <div>
            <Typography.Title :level="4" style="margin: 0">
              <span class="code-cell">{{ detail?.style.style_no ?? styleNo }}</span>
              <span class="detail-name">{{ detail?.style.name ?? '' }}</span>
            </Typography.Title>
            <Space class="detail-tags">
              <Tag :color="detail?.style.is_active ? 'success' : 'default'">
                {{ detail?.style.is_active ? '启用中' : '已停用' }}
              </Tag>
              <Tag v-if="detail?.style.category_name" color="blue">
                {{ detail.style.category_name }}
              </Tag>
              <Tag v-if="detail?.style.customer_name">{{ detail.style.customer_name }}</Tag>
            </Space>
          </div>
          <Space>
            <Button
              v-can="PERM.BASE_UPDATE"
              @click="router.push({ name: 'base-styles-edit', params: { styleNo: styleNo ?? '' } })"
            >
              编辑
            </Button>
            <Button v-can="PERM.BASE_RATE_TEMPLATE_MANAGE" @click="copyOpen = true">
              模板复制
            </Button>
            <Button
              v-can="PERM.BASE_UPDATE"
              @click="
                router.push({ name: 'base-styles-ratios', params: { styleNo: styleNo ?? '' } })
              "
            >
              尺码比例
            </Button>
            <Button @click="historyOpen = true">变更历史</Button>
            <!--
              ⚠️ 「停用」只在**启用中**时出现，且要 `base:disable` 权限：
              已经停用的款号再给一个停用按钮，后端会回 10008「已经是停用状态」——
              那是在浪费用户一次点击。恢复走「编辑」里改「启用」（后端不另开 enable 端点）。
            -->
            <Button
              v-if="detail?.style.is_active"
              v-can="PERM.BASE_DISABLE"
              danger
              @click="openDisable"
            >
              停用
            </Button>
          </Space>
        </div>

        <Descriptions :column="3" size="small" bordered>
          <DescriptionsItem label="款号">
            <span class="code-cell">{{ detail?.style.style_no }}</span>
          </DescriptionsItem>
          <DescriptionsItem label="款名">{{ detail?.style.name }}</DescriptionsItem>
          <DescriptionsItem label="商品分类">
            {{ detail?.style.category_name ?? '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="归属客户">
            {{ detail?.style.customer_name ?? '—' }}
            <!-- ⚠️ 用 `span` 而不是 `Typography.Text`：prettier 会把超宽的行折成
                 `>`\n 文本\n`</Typography.Text\n> 这种形态，eslint 的
                 `vue/multiline-html-element-content-newline` / `html-closing-bracket-newline`
                 对这种形态**必然报警** —— 两条规则互相打架，只有「单行放得下」才都过。
                 旁边有 `class="field-hint"`，视觉上与 Typography.Text 一致。 -->
            <span class="field-hint">（仅用于建议号分组与筛选）</span>
          </DescriptionsItem>
          <DescriptionsItem label="客户货号备注">
            {{ detail?.style.customer_style_no || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="大货数量">
            {{ detail?.style.bulk_qty ?? '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="跟单归属">
            {{ merchandiserLabel }}
            <!-- ⚠️ 用 `span` 而不是 `Typography.Text`：prettier 会把超宽的行折成
                 `>`\n 文本\n`</Typography.Text\n> 这种形态，eslint 的
                 `vue/multiline-html-element-content-newline` / `html-closing-bracket-newline`
                 对这种形态**必然报警** —— 两条规则互相打架，只有「单行放得下」才都过。
                 旁边有 `class="field-hint"`，视觉上与 Typography.Text 一致。 -->
            <span class="field-hint">（SELF 数据范围按它隔离）</span>
          </DescriptionsItem>
        </Descriptions>

        <Tabs>
          <!-- ------------------------------------------------ 色组 -->
          <TabPane key="colors" :tab="`色组（${colors.length}）`">
            <Space direction="vertical" size="small" style="width: 100%">
              <Space>
                <Button v-can="PERM.BASE_UPDATE" type="primary" @click="colorModalOpen = true">
                  新增色组行
                </Button>
                <Typography.Text type="secondary" class="field-hint">
                  色组是**追加**：加一行不影响已有行
                </Typography.Text>
              </Space>
              <Table
                :columns="colorColumns"
                :data-source="colors"
                :row-key="(row: StyleColorOut) => row.id"
                size="small"
                :pagination="false"
              >
                <template #bodyCell="{ column, record }">
                  <span v-if="column.key === 'material_color_code'">
                    {{ record.material_color_code ?? '—' }}
                  </span>
                </template>
                <template #emptyText>
                  <Empty description="还没有色组行" />
                </template>
              </Table>
            </Space>
          </TabPane>

          <!-- ------------------------------------------------ 尺码 -->
          <TabPane key="sizes" :tab="`尺码（${sizes.length}）`">
            <Space direction="vertical" size="small" style="width: 100%">
              <Space>
                <Button v-can="PERM.BASE_UPDATE" type="primary" @click="sizeModalOpen = true">
                  新增尺码
                </Button>
                <Typography.Text type="secondary" class="field-hint">
                  可以单码录入，也可以选一套**尺码模板**一键带出整套
                </Typography.Text>
              </Space>
              <Table
                :columns="sizeColumns"
                :data-source="sizes"
                :row-key="(row: StyleSizeOut) => row.id"
                size="small"
                :pagination="false"
              >
                <template #emptyText>
                  <Empty description="还没有尺码" />
                </template>
              </Table>
            </Space>
          </TabPane>

          <!-- ------------------------------------------------ 款号工序 -->
          <TabPane key="operations" :tab="`款号工序（${operations.length}）`">
            <Space direction="vertical" size="small" style="width: 100%">
              <Space>
                <Button
                  v-if="!operationsEditing"
                  v-can="PERM.BASE_UPDATE"
                  type="primary"
                  :disabled="operations.length === 0"
                  @click="startEditOperations"
                >
                  编辑工序
                </Button>
                <template v-else>
                  <Button type="primary" :loading="operationsSaving" @click="saveOperations">
                    保存
                  </Button>
                  <Button @click="operationsEditing = false">取消</Button>
                </template>
                <Typography.Text type="secondary" class="field-hint">
                  ⚠️ 保存是**全量替换**：不在列表里的工序会被删掉
                </Typography.Text>
              </Space>

              <Alert
                v-if="finalOperationMissing"
                type="warning"
                show-icon
                message="必须指定最后一道工序（整烫）"
                description="业务确认所有款号最后一道都是整烫，而这道工序决定成衣入库时机（B-CAT-05 / ADR-0018）。不指定的话这个款永远不会触发成衣入库。"
              />

              <!-- 编辑态：可编辑行（docs/06 §2.4「明细行用可编辑表格」） -->
              <Table
                v-if="operationsEditing"
                :columns="operationColumns"
                :data-source="draftOperations"
                :row-key="
                  (row: StyleOperationItemIn, index?: number) => `${row.operation_no}-${index ?? 0}`
                "
                size="small"
                :pagination="false"
              >
                <template #bodyCell="{ column, index }">
                  <template v-if="column.key === 'sequence'">
                    <span class="cell-muted">{{ index + 1 }}</span>
                  </template>
                  <Combo
                    v-else-if="column.key === 'operation_no'"
                    :model-value="draftOperations[index]?.operation_no ?? null"
                    :fetch-options="searchOperations"
                    placeholder="输入工序号或名称搜索"
                    @update:model-value="
                      (value: string | null) => {
                        if (draftOperations[index] !== undefined) {
                          draftOperations[index] = {
                            ...draftOperations[index],
                            operation_no: value ?? '',
                          }
                        }
                      }
                    "
                  />
                  <InputNumber
                    v-else-if="column.key === 'bundle_qty'"
                    :value="draftOperations[index]?.bundle_qty ?? 1"
                    :min="0.001"
                    :precision="3"
                    style="width: 100%"
                    @update:value="
                      (value: string | number | null) => {
                        if (draftOperations[index] !== undefined) {
                          draftOperations[index] = {
                            ...draftOperations[index],
                            bundle_qty: value === null ? 1 : Number(value),
                          }
                        }
                      }
                    "
                  />
                  <Switch
                    v-else-if="column.key === 'is_piecework'"
                    :checked="draftOperations[index]?.is_piecework ?? true"
                    @update:checked="
                      (checked: boolean | string | number) => {
                        if (draftOperations[index] !== undefined) {
                          draftOperations[index] = {
                            ...draftOperations[index],
                            is_piecework: checked === true,
                          }
                        }
                      }
                    "
                  />
                  <Switch
                    v-else-if="column.key === 'is_final_operation'"
                    :checked="draftOperations[index]?.is_final_operation ?? false"
                    @update:checked="
                      (checked: boolean | string | number) => {
                        if (draftOperations[index] !== undefined) {
                          draftOperations[index] = {
                            ...draftOperations[index],
                            is_final_operation: checked === true,
                          }
                        }
                      }
                    "
                  />
                  <Button
                    v-else-if="column.key === 'actions'"
                    danger
                    size="small"
                    @click="removeOperationRow(index)"
                  >
                    删除
                  </Button>
                </template>
              </Table>

              <!-- 只读态 -->
              <Table
                v-else
                :columns="operationColumns"
                :data-source="operations"
                :row-key="(row: StyleOperationOut) => row.id"
                size="small"
                :pagination="false"
              >
                <template #bodyCell="{ column, record }">
                  <span v-if="column.key === 'bundle_qty'">{{ record.bundle_qty }}</span>
                  <span v-else-if="column.key === 'operation_name'">
                    {{ record.operation_name ?? record.operation_no }}
                  </span>
                  <Tag
                    v-else-if="column.key === 'is_piecework'"
                    :color="record.is_piecework ? 'blue' : 'default'"
                  >
                    {{ record.is_piecework ? '计件' : '不计件' }}
                  </Tag>
                  <!--
                    最后一道工序：**醒目徽标**（docs/06 §1 危险色）。
                    它决定成衣入库时机，认错了会导致库存对不上。
                  -->
                  <Tag
                    v-else-if="column.key === 'is_final_operation'"
                    :color="record.is_final_operation ? 'warning' : 'default'"
                  >
                    {{ record.is_final_operation ? '最后一道（整烫）' : '—' }}
                  </Tag>
                </template>
                <template #emptyText>
                  <Empty description="还没有配工序 —— 计件与工资都按工序结构算" />
                </template>
              </Table>

              <Button v-if="operationsEditing" size="small" @click="addOperationRow">
                + 加一道工序
              </Button>
            </Space>
          </TabPane>

          <!-- ------------------------------------------------ 现行价 -->
          <TabPane key="rates" :tab="`现行价（${currentRates.length}）`">
            <Space direction="vertical" size="small" style="width: 100%">
              <Space>
                <Button
                  v-can="PERM.PIECEWORK_RATE_MANAGE"
                  type="primary"
                  @click="
                    router.push({
                      name: 'base-operation-rates',
                      query: { style_no: styleNo ?? '' },
                    })
                  "
                >
                  去设价 / 调价
                </Button>
                <Typography.Text type="secondary" class="field-hint">
                  ⚠️ 单价**只追加区间、永不改历史价**（R11）：调价 = 旧行关闭 + 插入新区间
                </Typography.Text>
              </Space>
              <Table
                :columns="rateColumns"
                :data-source="currentRates"
                :row-key="(row: OperationRateOut) => row.id"
                size="small"
                :pagination="false"
              >
                <template #bodyCell="{ column, record }">
                  <span v-if="column.key === 'unit_price'" class="money">
                    {{ formatMoney(record.unit_price, 6) }}
                  </span>
                  <Tag v-else-if="column.key === 'rate_source'" color="blue">
                    {{ rateSourceLabel(record.rate_source) }}
                  </Tag>
                  <span v-else-if="column.key === 'reason'">{{ record.reason ?? '—' }}</span>
                </template>
                <template #emptyText>
                  <Empty description="还没有设价 —— 未设价的工序在计件时会报 20004" />
                </template>
              </Table>
            </Space>
          </TabPane>
        </Tabs>
      </Space>
    </Card>

    <!-- ------------------------------------------------ 新增色组 -->
    <Modal
      :open="colorModalOpen"
      title="新增色组行"
      :confirm-loading="colorSaving"
      @ok="saveColor"
      @cancel="colorModalOpen = false"
    >
      <Space direction="vertical" size="small" style="width: 100%">
        <Input v-model:value="newColor.color_group" placeholder="色组，如 A" />
        <Input v-model:value="newColor.color_code" placeholder="色码，如 NVY（来自颜色字典）" />
        <Input v-model:value="newColor.color_name" placeholder="色名，如 藏青" />
        <Input
          v-model:value="newColor.material_color_code"
          placeholder="面料对应色 / 缸别（可不填）"
        />
      </Space>
    </Modal>

    <!-- ------------------------------------------------ 新增尺码 -->
    <Modal
      :open="sizeModalOpen"
      title="新增尺码"
      :confirm-loading="sizeSaving"
      @ok="saveSize"
      @cancel="sizeModalOpen = false"
    >
      <Space direction="vertical" size="middle" style="width: 100%">
        <RadioGroup
          :value="newSize.mode"
          :options="[
            { value: 'single', label: '单码录入' },
            { value: 'group', label: '选尺码模板，一键带出整套' },
          ]"
          option-type="button"
          button-style="solid"
          @update:value="
            (value: unknown) =>
              (newSize = { ...newSize, mode: value === 'group' ? 'group' : 'single' })
          "
        />
        <!--
          ⚠️ 两种模式**互斥**（modules/01 §5.3）：都传或都不传后端都是 `10001`。
          所以界面上一次只显示一种输入，不给两个空框让用户自己猜哪个要填。
        -->
        <template v-if="newSize.mode === 'single'">
          <Input v-model:value="newSize.size_code" placeholder="尺码码，如 XL" />
          <Input v-model:value="newSize.size_name" placeholder="实际尺码，如 XL(170/92A)" />
        </template>
        <Combo
          v-else
          :model-value="newSize.size_group_name"
          :fetch-options="searchSizeGroups"
          placeholder="输入尺码模板名搜索…"
          @update:model-value="
            (value: string | null) => (newSize = { ...newSize, size_group_name: value })
          "
        />
      </Space>
    </Modal>

    <CopyDialog
      v-if="styleNo !== null"
      :open="copyOpen"
      :target-style-no="styleNo"
      @close="copyOpen = false"
    />

    <HistoryDrawer
      :open="historyOpen"
      doc-type="Style"
      :doc-no="styleNo ?? ''"
      @close="historyOpen = false"
    />

    <!--
      ⚠️ 停用弹窗要**说清后果**：停用不是删除，历史单据照常。不写清楚的话，
      现场看到「停用」会以为款号连同历史一起没了，于是永远不敢点（docs/06 §5）。
    -->
    <Modal
      :open="disableOpen"
      title="停用款号"
      ok-text="确认停用"
      cancel-text="取消"
      :ok-button-props="{ disabled: !canSubmitDisable }"
      :confirm-loading="disabling"
      @ok="submitDisable"
      @cancel="disableOpen = false"
    >
      <Space direction="vertical" size="small" style="width: 100%">
        <Typography.Text type="secondary">
          停用后<strong>不允许新建</strong>裁剪/打菲单，<strong>历史单据照常</strong>。
          已有工序、单价、尺码比例都不受影响；恢复要把「启用」改回打开。
        </Typography.Text>
        <Typography.Text>
          停用原因 <Typography.Text type="danger">*</Typography.Text>
        </Typography.Text>
        <!--
          ⚠️ 用 `Textarea` 而不是 `Input`：停用原因往往是「客户取消 XX 款」这种带
          日期与订单号的长句，单行输入框会被截断，而截断的原因事后查不出来。
        -->
        <Textarea
          v-model:value="disableReason"
          :rows="3"
          :maxlength="200"
          placeholder="例：客户取消该款，不再下单"
          show-count
        />
        <span v-if="!canSubmitDisable" class="field-hint">停用必须填写原因</span>
      </Space>
    </Modal>
  </div>
</template>

<style scoped>
.detail-card {
  box-shadow: var(--shadow-card);
}

.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.detail-name {
  margin-left: var(--space-3);
  font-size: var(--font-size-lg);
  font-weight: 400;
  color: var(--color-text-second);
}

.detail-tags {
  margin-top: var(--space-2);
}

.field-hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.cell-muted {
  color: var(--color-text-third);
}

.money {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
