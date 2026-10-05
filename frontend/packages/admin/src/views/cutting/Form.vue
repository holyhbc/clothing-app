<script setup lang="ts">
/**
 * 裁剪单**新建页**（表头 + 三层明细一次提交，T-CUT-001c-3a）。
 *
 * ## 为什么是「一次提交」而不是「先建单再逐层 PUT」
 *
 * 后端的 `POST /cutting-orders` 就是这么设计的（`CuttingOrderCreateIn` 带
 * `lines[].colors[].size_lines[]`），而且**新建单上没有任何要保存的中间态价值** ——
 * 一张空的裁剪单对录入员没有意义，「建了一半的单」只会让人不知道哪些行已经进去了。
 * 逐层 PUT 的价值在**改**（保住已录内容），那是 T-CUT-001c-3b 编辑页的事。
 *
 * ## 表头必填与「不信任前端」
 *
 * - `workshop_id` / `style_id` / `doc_date` 必填；`doc_date` 默认今天（`business_today`）。
 * - **表头五个汇总列（耗料 / 出数 / 裁损 / 尾数 / 手数）在入参里连字段都没有**：
 *   传了会被 `extra="forbid"` 挡下报 `10001`。所以这个表单**不能**让用户填合计 ——
 *   后端 C6 会重算，页面上显示的任何合计都只是「让用户对得上自己录入的」而已。
 * - ⚠️ `style_id` 要的是 **UUID** 而不是款号字符串：候选走 `searchStyleOptionsById`。
 *
 * ## 「按比例带出」为什么不在这一页
 *
 * 比例带出要调 `GET /{id}/suggest-lines`，而它需要 `order_id` —— 新建页上单据还不存在；
 * 且 `SuggestLinesOut.ratio` 是**小数**而 `hands` 必须**整数**（ADR-0020），
 * 取整策略业务还没拍板（`docs/12` L-082）。所以新建页只给「自定义明细 / 统一件数」，
 * 带出在保存后的详情页（T-CUT-001c-3b）。**给一个做不到的选项比不给更糟**：
 * 用户填到一半才发现要保存重来。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Descriptions,
  DescriptionsItem,
  Input,
  InputNumber,
  Radio,
  Space,
  message,
} from 'ant-design-vue'
import { PERM } from '@garment/shared'
import type {
  CuttingOrderCreateIn,
  CuttingOrderOut,
  OrderLineIn,
  StockBatchOptionOut,
} from '@garment/shared'
import { baseApi } from '@/api/base'
import {
  createCuttingOrder,
  searchStockBatchOptions,
  searchStyleOptionsById,
} from '@/api/cutting'
import Combo from '@/components/Combo.vue'
import LineEditor from './components/LineEditor.vue'
import { balanceOf, lineOutputOf, num } from './components/slotTypes'
import type { LineTree } from './components/slotTypes'
import PageLayout from '@/components/PageLayout.vue'

defineOptions({ name: 'CuttingOrderForm' })

const router = useRouter()

/** 表头。⚠️ 汇总五列**不在这里**（后端入参没有这些字段，传了报 10001）。 */
const form = ref({
  workshop_id: null as string | null,
  style_id: null as string | null,
  doc_date: dayjs().format('YYYY-MM-DD'),
  delivery_date: null as string | null,
  ply_count: 1,
  remark_source: '',
  remark: '',
})

const lines = ref<LineTree>([])
const submitting = ref(false)

/** antd `DatePicker` 的值：dayjs 对象或空串；空值统一归一成一个方向。 */
function toDateText(value: unknown): string | null {
  if (value === null || value === '') return null
  if (typeof value === 'string') return value
  if (typeof value === 'object' && 'format' in value) {
    const dayjsLike = value as { format: (pattern: string) => string }
    return dayjsLike.format('YYYY-MM-DD')
  }
  return null
}

/**
 * 提交前的整页问题清单。
 *
 * ⚠️ 「问题」与「阻断」分开：`problems` 只做**提示**（布不够、行余量为负），
 * 真正**阻断提交**的只有「必填没填」和「行余量为负」—— 后者是因为后端会收 `30002`，
 * 放着让用户提交一次再收错误是刻意的坏体验（`docs/06 §5`：说清为什么 + 下一步）。
 */
const blocking = computed(() => {
  const list: string[] = []
  if (form.value.workshop_id === null) list.push('车间必填（C2）')
  if (form.value.style_id === null) list.push('款号必填')
  if (form.value.doc_date === '') list.push('单据日期必填')
  if (form.value.ply_count < 1) list.push('铺布层数必须 ≥ 1（C4）')
  if (lines.value.length === 0) list.push('至少要有一个布批行')
  lines.value.forEach((line: LineTree[number], index: number) => {
    const at = `第 ${index + 1} 行`
    if (line.stock_id === '') list.push(`${at}：还没选布批`)
    if (Number(line.fabric_qty ?? 0) <= 0) list.push(`${at}：耗料必须大于 0`)
    if (line.colors.length === 0) list.push(`${at}：至少要有一个颜色`)
    // ⚠️ **行余量为负必须在这里阻断**（C5 / C34）。第一版只把它显示在
    //    `LineEditor` 的问题清单里，而那份清单**不参与提交判定** —— 于是提交按钮
    //    是可点的，用户填完三层的全部数据之后才被后端收 `30002`。
    //    「填满一屏再被拒」是最伤录入员心情的失败方式，所以放在提交前。
    const balance = balanceOf(line)
    if (balance < 0) {
      list.push(`${at}：行可出件数比尺码明细合计少 ${-balance}，提交后端会收 30002`)
    }
    line.colors.forEach((color: LineTree[number]['colors'][number], colorIndex: number) => {
      if (color.color_code === '') list.push(`${at} 第 ${colorIndex + 1} 色：还没选色码`)
      color.size_lines.forEach((row, sizeIndex: number) => {
        const at2 = `${at} 第 ${colorIndex + 1} 色 第 ${sizeIndex + 1} 行`
        if (row.size_code === '') list.push(`${at2}：还没选尺码`)
        if (row.hands < 1) list.push(`${at2}：手数必须 ≥ 1`)
        if (row.qty_per_hand < 1) list.push(`${at2}：每手件数必须 ≥ 1`)
      })
    })
  })
  return list
})

/**
 * 提交体。
 *
 * ⚠️ **逐字段挑，不直接把表单对象整个丢进去**：后端是 `extra="forbid"`，
 * 多一个键就是 `10001`。用 `{...form}` + 删字段那种写法则是「下次表单加个字段就
 * 悄悄发错」—— 前端少一个键后端会拒，但多一个键后端**也会**拒，只是错得更晚。
 */
function buildPayload(): CuttingOrderCreateIn {
  const header = form.value
  return {
    workshop_id: header.workshop_id ?? '',
    style_id: header.style_id ?? '',
    doc_date: header.doc_date,
    delivery_date: header.delivery_date,
    ply_count: header.ply_count,
    // ⚠️ 表头默认模式取**第一个颜色**的模式：真正生效的是颜色级 `entry_mode`
    //    （C26），表头这个字段只是新建颜色时的默认值 —— 两者写不一致会让
    //    详情页上「默认模式」与实际录入的模式看起来矛盾。
    entry_mode_default: headerFirstMode(),
    remark_source: header.remark_source === '' ? null : header.remark_source,
    remark: header.remark === '' ? null : header.remark,
    lines: lines.value.map(normalizeLine),
  }
}

function headerFirstMode(): 'MASTER' | 'UNIFORM' | 'MANUAL' {
  return lines.value[0]?.colors[0]?.entry_mode ?? 'MANUAL'
}

/**
 * 行 → 请求体。
 *
 * ⚠️ **空的可选字段一律转 `null` 而不是空串**：后端 `str | None` 收到空串会
 * 过 Pydantic 校验、然后被当成「填了空串」存进库 —— 于是这一列永远显示空白，
 * 而录入员看不出哪里不对。
 */
function normalizeLine(line: LineTree[number]): OrderLineIn {
  return {
    ...line,
    width_cm: null,
    color_plan: null,
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
  }
}

async function submit(): Promise<void> {
  if (blocking.value.length > 0) {
    message.error(`还有 ${blocking.value.length} 处没填好：${blocking.value[0]}`)
    return
  }
  submitting.value = true
  try {
    const created: CuttingOrderOut = await createCuttingOrder(buildPayload())
    message.success(`已保存草稿 ${created.doc_no}`)
    await router.push({ name: 'cutting-orders-detail', params: { orderId: created.id } })
  } catch (error) {
    // ⚠️ 只展示后端给的 message：10001（参数）/ 20001（布批不存在）/ 40006（布不够）
    //    三种的后端文案都说清了原因与下一步，自己拼一句「保存失败」等于把信息扔掉
    message.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    submitting.value = false
  }
}

function cancel(): void {
  void router.push({ name: 'cutting-orders' })
}

/** 布批候选：按关键字搜缸号 / 匹号（BR-ST-17 ①：裁剪最常见的是人工指定批次）。 */
function fetchStockOptions(keyword: string): Promise<StockBatchOptionOut[]> {
  return searchStockBatchOptions({ q: keyword })
}

const summary = computed(() => {
  const fabric = lines.value.reduce((t: number, l) => t + Number(l.fabric_qty ?? 0), 0)
  const waste = lines.value.reduce((t: number, l) => t + Number(l.waste_qty ?? 0), 0)
  const hands = lines.value.reduce((t: number, l) => t + lineOutputOf(l), 0)
  return { fabric, waste, hands }
})

</script>

<template>
  <PageLayout title="新建裁剪单" description="表头与三层明细一次提交，保存为草稿">
    <template #extra>
      <Space>
        <Button @click="cancel">取消</Button>
        <Button
          v-can="PERM.CUTTING_CREATE"
          type="primary"
          :loading="submitting"
          :disabled="blocking.length > 0"
          @click="submit"
        >
          保存草稿
        </Button>
      </Space>
    </template>

    <Card size="small" title="表头" style="margin-bottom: var(--space-4)">
      <div class="header-grid">
        <div class="field">
          <label>车间<span class="req">*</span></label>
          <Combo
            :model-value="form.workshop_id"
            :fetch-options="baseApi.workshops.optionsById"
            placeholder="选车间"
            @update:model-value="(value: string | null) => (form.workshop_id = value)"
          />
          <small class="hint">数据范围按车间过滤（INV-8），车间主管只能建自己车间的单</small>
        </div>

        <div class="field">
          <label>款号<span class="req">*</span></label>
          <Combo
            :model-value="form.style_id"
            :fetch-options="searchStyleOptionsById"
            placeholder="搜款号 / 款名"
            @update:model-value="(value: string | null) => (form.style_id = value)"
          />
          <small class="hint">提交的是款号 <b>UUID</b>（后端要 id，不是款号字符串）</small>
        </div>

        <div class="field">
          <label>单据日期<span class="req">*</span></label>
          <DatePicker
            :value="form.doc_date === '' ? '' : dayjs(form.doc_date)"
            style="width: 100%"
            @update:value="(value: unknown) => (form.doc_date = toDateText(value) ?? '')"
          />
          <small class="hint">决定所属期间，默认今天</small>
        </div>

        <div class="field">
          <label>交期</label>
          <DatePicker
            :value="form.delivery_date === null ? '' : dayjs(form.delivery_date)"
            style="width: 100%"
            @update:value="(value: unknown) => (form.delivery_date = toDateText(value))"
          />
        </div>

        <div class="field">
          <label>铺布层数<span class="req">*</span></label>
          <InputNumber
            :value="form.ply_count"
            :min="1"
            :precision="0"
            style="width: 100%"
            @change="(value: string | number | null) => (form.ply_count = Math.trunc(num(value) ?? 1))"
          />
          <small class="hint">&gt; 1 时出数是**多层合计后的总件数**（C4）</small>
        </div>

        <div class="field">
          <label>默认录入模式</label>
          <Radio.Group :value="headerFirstMode()" size="small">
            <Radio.Button value="MANUAL">自定义明细</Radio.Button>
            <Radio.Button value="UNIFORM">统一件数</Radio.Button>
          </Radio.Group>
          <small class="hint">
            取第一个颜色的模式；「按比例带出」要在保存后于详情页用（需要单号）
          </small>
        </div>

        <div class="field">
          <label>来源备注</label>
          <Input
            :value="form.remark_source"
            placeholder="跟单张 / 客户要求"
            @change="
              (event: Event) =>
                (form.remark_source = (event.target as HTMLInputElement).value)
            "
          />
        </div>

        <div class="field">
          <label>备注</label>
          <Input
            :value="form.remark"
            placeholder="可选"
            @change="(event: Event) => (form.remark = (event.target as HTMLInputElement).value)"
          />
        </div>
      </div>

      <Descriptions size="small" :column="3" style="margin-top: var(--space-3)">
        <DescriptionsItem label="耗料合计">{{ summary.fabric }} m</DescriptionsItem>
        <DescriptionsItem label="布头/布损合计">{{ summary.waste }} m</DescriptionsItem>
        <DescriptionsItem label="尺码出数合计">{{ summary.hands }} 件</DescriptionsItem>
      </Descriptions>
      <small class="hint">
        ⚠️ 这三个数只是**让用户对得上自己录入的**；落库时由后端 C6 重算覆盖（C6 不信任前端），
        表头汇总五列**不接受传入**
      </small>
    </Card>

    <Card size="small" title="三层明细（布批行 → 颜色 → 尺码明细）">
      <LineEditor
        v-model="lines"
        :ply-count="form.ply_count"
        :fetch-stock-options="fetchStockOptions"
      />
    </Card>

    <Alert v-if="blocking.length > 0" class="block-alert" type="warning" show-icon>
      <div v-for="(problem, i) in blocking" :key="i" class="block-line">{{ problem }}</div>
    </Alert>
  </PageLayout>
</template>

<style scoped>
.header-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.req {
  color: var(--color-danger);
}

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