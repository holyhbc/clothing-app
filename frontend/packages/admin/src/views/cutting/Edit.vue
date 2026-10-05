<script setup lang="ts">
/**
 * 裁剪单**编辑页**（改草稿：表头 + 三层明细一次全量替换，T-CUT-001c-3c）。
 *
 * ## 保存为什么是「`PATCH` 表头 + `PUT /lines` 明细」两次调用
 *
 * 后端的三个明细写接口里，**`PUT /lines` 的 `items` 是完整的 `OrderLineIn`**
 * （含 `colors[].size_lines[]`），service 会 `_soft_delete_subtree` 掉整棵子树再按
 * `items` 重建 —— 所以一次 `PUT /lines` 就能落完整棵树，**只需要一次版本链**。
 * 而 `PUT /lines/{line_id}/colors` 与 `PUT /size-lines` 是为「只改其中一层」准备的：
 * 它们会保留别的行的数据与 `ratio_snapshot`（C29 禁止前端传入快照，所以全量替换
 * 会把快照清掉 —— 这条是将来接「按比例带出」时必须知道的约束，见 `docs/12`）。
 *
 * ⚠️ **版本链**：每个写接口都带表头的 `version`，成功后 `version + 1`。所以
 * 「先 PATCH 再 PUT /lines」必须**把 PATCH 返回的新 version 传给 PUT**，
 * 而不能用页面上的旧值 —— 否则第二次写必然 `10003`，而用户看到的只是
 * 「保存失败，不知道哪里错了」。
 *
 * ## 只读态
 *
 * 后端 `_editable_order` 只允许 **DRAFT / REJECTED** 修改。所以已提交 / 已审核 /
 * 已作废的单据这里整块只读，并提示先撤回或反审核 —— **不在前端假装能存**：
 * 按钮可点而提交后收 409，比明确置灰更让人困惑。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Space,
  Spin,
  Tag,
  message,
} from 'ant-design-vue'
import { PERM, formatQty } from '@garment/shared'
import type { CuttingOrderOut, OrderLineIn, StockBatchOptionOut } from '@garment/shared'
import {
  deleteCuttingOrder,
  getCuttingOrder,
  patchCuttingOrder,
  putCuttingOrderLines,
  searchStockBatchOptions,
} from '@/api/cutting'
import PageLayout from '@/components/PageLayout.vue'
import StatusTag from '@/components/StatusTag.vue'
import HeaderFields from './components/HeaderFields.vue'
import type { HeaderFields as HeaderValue } from './components/HeaderFields.vue'
import LineEditor from './components/LineEditor.vue'
import { balanceOf, lineOutputOf } from './components/slotTypes'
import type { LineTree } from './components/slotTypes'

defineOptions({ name: 'CuttingOrderEdit' })

const route = useRoute()
const router = useRouter()

const order = ref<CuttingOrderOut | null>(null)
const header = ref<HeaderValue>({
  workshop_id: null,
  style_id: null,
  doc_date: '',
  delivery_date: null,
  ply_count: 1,
  remark_source: '',
  remark: '',
})
const lines = ref<LineTree>([])
const loading = ref(false)
const saving = ref(false)
const loadError = ref('')

/** 表头的乐观锁版本（**每次写成功后必须用响应里的新值覆盖**）。 */
const version = ref(1)

/** 可编辑状态：后端只允许 DRAFT / REJECTED（`_editable_order`）。 */
const editable = computed(() => order.value?.status === 'DRAFT' || order.value?.status === 'REJECTED')

/**
 * 路由参数收窄。
 *
 * ⚠️ `route.params.orderId` 类型是 `string | string[]`，而接口要 `string`。
 * 断言 `as string` 会让「参数缺失」变成「把 undefined 发给后端」，用户看到 10001
 * 而不是「页面地址不对」。
 */
function orderIdOf(raw: unknown): string {
  const value = Array.isArray(raw) ? raw[0] : raw
  return typeof value === 'string' ? value : ''
}

const orderId = computed(() => orderIdOf(route.params['orderId']))

/**
 * 详情 → 可编辑树（**逐字段映射，不做 `as` 断言**）。
 *
 * ⚠️ 逐字段而不是 `...line` 展开：出参里 `size_line_no` 是必填而本地入参类型里它是
 *     可选的，两者**形状不同**，整个对象断言成 `LineTree` 会被 `vue-tsc` 拦
 *     （TS2352：两个类型「没有足够重叠」）。逐字段映射把「哪些字段是编辑要的」写成
 *     一张清单，将来后端加字段时这里是**唯一**需要考虑的地方。
 *
 * ⚠️ **必须深拷贝三层**：直接复用 `line.colors` 会让编辑器与 `order` 共享同一批对象，
 *     而 `LineEditor` 是「不可变更新」（整棵树换新引用）—— 共享对象会让「不保存就返回」
 *     无法把原来的数据还回来。
 *
 * ⚠️ `colors` / `size_lines` 在出参里是**可选**的（模型有 `default_factory`），
 *     所以 `?? []`；而本地树类型把它们收紧成必填（见 `slotTypes`）。
 *
 * ⚠️ 尺码行的 `output_qty` 在出参是**字符串**且带 `output_qty_manual` 标记，
 *     本地树要的是 `number | null` —— 只在「人工指定过」时才回填，
 *     否则让编辑器按「手数 × 每手件数」重算（C28）。
 */
function toTree(source: CuttingOrderOut): LineTree {
  return (source.lines ?? []).map((line) => ({
    line_no: line.line_no,
    stock_id: line.stock_id,
    width_cm: line.width_cm ?? null,
    fabric_qty: line.fabric_qty,
    waste_qty: line.waste_qty,
    output_qty: line.output_qty,
    color_plan: line.color_plan ?? null,
    colors: (line.colors ?? []).map((color) => ({
      color_code: color.color_code,
      entry_mode: color.entry_mode,
      // ⚠️ 颜色级 `qty_per_hand` **不回填**：它是「模式 A/B 的默认每手件数」，
      //    而**尺码明细行的才是权威值**（schemas 原文）。回填它会让界面显示一个
      //    「看起来生效但实际被下面每行覆盖」的数字。
      qty_per_hand: null,
      uniform_qty: color.uniform_qty ?? null,
      size_lines: (color.size_lines ?? []).map((row) => ({
        size_line_no: row.size_line_no,
        size_code: row.size_code,
        hands: row.hands,
        qty_per_hand: row.qty_per_hand,
        output_qty: row.output_qty_manual ? Number(row.output_qty) : null,
        hands_seq: row.hands_seq ?? null,
        remark: row.remark ?? null,
      })),
    })),
  }))
}

async function load(): Promise<void> {
  if (orderId.value === '') {
    loadError.value = '缺少裁剪单 ID（页面地址不对）'
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const detail = await getCuttingOrder(orderId.value)
    order.value = detail
    version.value = detail.version
    lines.value = toTree(detail)
    header.value = {
      // ⚠️ `String(...)` 不是多余：出参里这两个是 **UUID 类型**，而 `HeaderFields`
      //    存的是「Combo 选出来的字符串」。留成 UUID 类型也能跑，但 `exactOptionalPropertyTypes`
      //    下会把 `string | null | undefined` 传到 `string | null` 上直接报错 ——
      //    而报错说的是「HeaderFields 的字段类型不对」，完全看不出根因在「两边都该是字符串」。
      workshop_id: String(detail.workshop_id),
      style_id: String(detail.style_id),
      doc_date: String(detail.doc_date),
      delivery_date: detail.delivery_date ?? null,
      ply_count: detail.ply_count,
      remark_source: detail.remark_source ?? '',
      remark: detail.remark ?? '',
    }
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/** 明细树 → `PUT /lines` 的 items（空串一律转 `null`，理由同新建页）。 */
function toLineItems(): OrderLineIn[] {
  return lines.value.map((line) => ({
    ...line,
    width_cm: line.width_cm ?? null,
    color_plan: line.color_plan ?? null,
    remark: null,
    colors: line.colors.map((color) => ({
      ...color,
      qty_per_hand: null,
      uniform_qty: color.uniform_qty ?? null,
      size_lines: color.size_lines.map((row) => ({
        ...row,
        output_qty: row.output_qty ?? null,
        hands_seq: null,
        remark: row.remark === '' ? null : (row.remark ?? null),
      })),
    })),
  }))
}

const blocking = computed(() => {
  const list: string[] = []
  if (!editable.value) list.push('这张单据当前状态不可修改')
  if (header.value.doc_date === '') list.push('单据日期必填')
  if (lines.value.length === 0) list.push('至少要有一个布批行')
  lines.value.forEach((line, index) => {
    const at = `第 ${index + 1} 行`
    if (line.stock_id === '') list.push(`${at}：还没选布批`)
    if (Number(line.fabric_qty ?? 0) <= 0) list.push(`${at}：耗料必须大于 0`)
    if (line.colors.length === 0) list.push(`${at}：至少要有一个颜色`)
    const balance = balanceOf(line)
    if (balance < 0) list.push(`${at}：行可出件数比尺码明细合计少 ${-balance}（后端 30002）`)
    line.colors.forEach((color, colorIndex) => {
      if (color.color_code === '') list.push(`${at} 第 ${colorIndex + 1} 色：还没选色码`)
      color.size_lines.forEach((row, sizeIndex) => {
        if (row.size_code === '') {
          list.push(`${at} 第 ${colorIndex + 1} 色 第 ${sizeIndex + 1} 行：还没选尺码`)
        }
        if (row.hands < 1 || row.qty_per_hand < 1) {
          list.push(`${at} 第 ${colorIndex + 1} 色 第 ${sizeIndex + 1} 行：手数与每手件数必须 ≥ 1`)
        }
      })
    })
  })
  return list
})

/**
 * 保存：表头 `PATCH` → 明细 `PUT /lines`，**串行且传递新 version**。
 *
 * ⚠️ 两个写接口各自 bump version，所以**必须串行**：并行的话第二个请求带的是同一个
 * 旧 version，两个里必然有一个 `10003`。
 *
 * ⚠️ 每一步都用**上一步响应里的** `version`。用页面上的旧值是这类页面最经典的 bug：
 * 单看代码「传了 version」没问题，而用户每点一次保存就失败一次。
 */
async function save(): Promise<void> {
  // ⚠️ **防重入**：按钮上的 `loading` 只是「显示转圈」，它挡不住第二次点击
  //    （antd 的 loading 按钮仍会触发 click）。两次并发保存的后果不是「多写一次」而是
  //    「两个请求带同一个 version」—— 后到的那个必然 `10003`，而用户看到的只是
  //    「我明明第一次存成功了」。`docs/06 §2.4` 要求「提交后按钮 loading 禁用防重复」。
  if (saving.value) return
  if (blocking.value.length > 0) {
    message.error(`还有 ${blocking.value.length} 处没填好：${blocking.value[0]}`)
    return
  }
  saving.value = true
  try {
    const afterPatch = await patchCuttingOrder(orderId.value, {
      doc_date: header.value.doc_date,
      delivery_date: header.value.delivery_date,
      ply_count: header.value.ply_count,
      remark_source: header.value.remark_source === '' ? null : header.value.remark_source,
      remark: header.value.remark === '' ? null : header.value.remark,
      version: version.value,
    })
    version.value = afterPatch.version
    const afterLines = await putCuttingOrderLines(orderId.value, {
      items: toLineItems(),
      version: version.value,
    })
    version.value = afterLines.version
    // ⚠️ **用响应重建本地树**：`PUT /lines` 是软删旧行 + 插新行 → **行 id 全变了**。
    //    继续用旧 id 的话，下一次保存会收到 `10001`「第 xxx 行不属于这张裁剪单」。
    order.value = afterLines
    lines.value = toTree(afterLines)
    message.success(`已保存 ${afterLines.doc_no}（第 ${afterLines.version} 版）`)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

/** 删草稿：只有草稿能删，且要二次确认（不可恢复的整单删除，`docs/06 §5`）。 */
async function remove(): Promise<void> {
  if (saving.value) return
  if (order.value?.status !== 'DRAFT') {
    message.warning('只有草稿可以删除；已提交的单请走「撤回」')
    return
  }
  const confirmed = window.confirm(`确定删除草稿 ${order.value.doc_no}？三层明细会一并作废。`)
  if (!confirmed) return
  saving.value = true
  try {
    await deleteCuttingOrder(orderId.value, version.value)
    message.success('已删除')
    await router.push({ name: 'cutting-orders' })
  } catch (error) {
    message.error(error instanceof Error ? error.message : '删除失败')
  } finally {
    saving.value = false
  }
}

function fetchStockOptions(keyword: string): Promise<StockBatchOptionOut[]> {
  return searchStockBatchOptions({ q: keyword })
}

const totals = computed(() => ({
  fabric: lines.value.reduce((t, l) => t + Number(l.fabric_qty ?? 0), 0),
  output: lines.value.reduce((t, l) => t + lineOutputOf(l), 0),
  hands: order.value?.hands_total ?? 0,
}))

onMounted(() => {
  void load()
})
</script>

<template>
  <PageLayout :title="order?.doc_no ?? '编辑裁剪单'">
    <template #extra>
      <Space>
        <Button @click="router.back()">返回</Button>
        <Button v-if="editable" v-can="PERM.CUTTING_UPDATE" danger @click="remove">删除草稿</Button>
        <Button
          v-can="PERM.CUTTING_UPDATE"
          type="primary"
          :loading="saving"
          :disabled="blocking.length > 0"
          @click="save"
        >
          保存
        </Button>
      </Space>
    </template>

    <Alert v-if="loadError !== ''" type="error" show-icon message="加载失败" :description="loadError">
      <template #action>
        <Button size="small" @click="load">重试</Button>
      </template>
    </Alert>

    <Alert
      v-else-if="order !== null && !editable"
      type="info"
      show-icon
      style="margin-bottom: var(--space-3)"
      :message="`当前状态「${order.status}」不可修改`"
      description="后端只允许草稿 / 已驳回的单据修改；已提交的单要先撤回，已审核的要先反审核（T-CUT-001b-3 的状态机端点还没实现）。"
    />

    <Spin :spinning="loading">
      <template v-if="order !== null">
        <Card size="small" title="表头" style="margin-bottom: var(--space-4)">
          <HeaderFields v-model="header" :disabled="!editable" lock-identity />
          <Descriptions size="small" :column="4" style="margin-top: var(--space-3)">
            <DescriptionsItem label="状态">
              <Space size="small">
                <StatusTag :status="order.status" />
                <Tag>第 {{ version }} 版</Tag>
              </Space>
            </DescriptionsItem>
            <DescriptionsItem label="耗料合计">{{ totals.fabric }} m</DescriptionsItem>
            <DescriptionsItem label="尺码出数合计">{{ totals.output }} 件</DescriptionsItem>
            <DescriptionsItem label="计件手数（后端）">{{ totals.hands }}</DescriptionsItem>
          </Descriptions>
          <small class="hint">
            ⚠️ 合计只是让你对得上录入内容；落库由后端 C6 重算。明细保存是
            **全量替换**（不在表里的行被作废），行 id 会变。
          </small>
        </Card>

        <Card size="small" title="三层明细（布批行 → 颜色 → 尺码明细）">
          <LineEditor
            v-model="lines"
            :ply-count="header.ply_count"
            :fetch-stock-options="fetchStockOptions"
          />
        </Card>

        <Alert v-if="blocking.length > 0" class="block-alert" type="warning" show-icon>
          <div v-for="(problem, i) in blocking" :key="i" class="block-line">{{ problem }}</div>
        </Alert>

        <p class="hint">
          单号 {{ order.doc_no }} · 已审核时尾数 {{ formatQty(order.balance_qty, 0) }} 件（不足件，不入库）
        </p>
      </template>
    </Spin>
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