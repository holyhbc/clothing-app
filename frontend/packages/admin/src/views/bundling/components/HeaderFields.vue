<script setup lang="ts">
/**
 * 打菲单表头字段（新建页）。
 *
 * 字段：
 * - 车间（Select，value = `workshops.id` UUID —— 入参 `workshop_id` 要的就是 UUID）
 * - 款号（Select，value = 款号**字符串** —— 入参 `style_no`，不是 UUID）
 * - 单据日期
 * - 一扎件数（参考值，InputNumber）
 * - 色码（文本）
 * - 色组（文本）
 * - 工序（Select，value = 工序号）
 * - 来源裁剪单（Select，显示手数/出数/每手件数）
 * - 备注
 *
 * ⚠️ **车间要 UUID、工序要编码** —— 两个 Select 都是「从候选里取 `value`」，
 *   但提交给后端的类型相反（`workshop_id` 是 UUID / `operation_no` 是业务编码，
 *   见 05 §9.5.2）。所以候选分别走 `workshops.optionsById` 与 `operations.options`，
 *   换任何一个都会让建单在 422 上死掉。
 */
import { Input, InputNumber, Select } from 'ant-design-vue'
import type { SourceOrderOut } from '@garment/shared'

export interface HeaderFieldsValue {
  workshop_id: string | null
  /** 款号**字符串**（不是 `styles.id`）。表头/来源候选/提交体都用它。 */
  style_no: string
  doc_date: string
  bundle_qty: number
  color_code: string
  color_group: string
  operation_no: string
  source_cutting_order_id: string | null
  remark: string
}

interface Props {
  modelValue: HeaderFieldsValue
  styleOptions: { value: string; label: string }[]
  operationOptions: { value: string; label: string }[]
  workshopOptions: { value: string; label: string }[]
  /** 候选**裁剪单**（不是尺码明细行）—— 03 §5.1：打菲单只挂一张裁剪单（B8）。 */
  sourceOrders: SourceOrderOut[]
  loadingSources: boolean
}

defineOptions({ name: 'BundlingHeaderFields' })

const props = defineProps<Props>()
const emit = defineEmits<{ 'update:modelValue': [value: HeaderFieldsValue]; sourceChange: [] }>()

/**
 * antd `Select` 的取值可能是数组 / LabeledValue / undefined，
 * 而本表单的这几个字段是**单值字符串**（UUID 或业务编码，见文件头注释）。
 * 这里统一收窄，免得每个 handler 里各写一遍 `typeof v === 'string'`
 * （款号与工序走的是同一套判断，只是没走本函数 —— 它们的空值是 `''`）。
 */
function asId(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function update<K extends keyof HeaderFieldsValue>(key: K, value: HeaderFieldsValue[K]): void {
  const next = { ...props.modelValue, [key]: value }
  emit('update:modelValue', next)
  // 车间也要触发：候选裁剪单按车间过滤（且车间主管的数据范围也按它限定，INV-8）。
  // 少这一条会出现「切了车间，下拉里还是别的车间的单」。
  if (
    key === 'source_cutting_order_id' ||
    key === 'style_no' ||
    key === 'color_code' ||
    key === 'workshop_id'
  ) {
    emit('sourceChange')
  }
}

</script>

<template>
  <div style="display: flex; flex-wrap: wrap; gap: var(--space-4); padding: var(--space-3);">
    <div style="flex: 1; min-width: 280px;">
      <label class="form-label">车间</label>
      <Select
        :value="modelValue.workshop_id ?? undefined"
        :options="workshopOptions"
        placeholder="输入关键字搜索车间…"
        allow-clear
        show-search
        option-filter-prop="label"
        style="width: 100%"
        @update:value="(v: unknown) => update('workshop_id', asId(v))"
      />
    </div>

    <div style="flex: 1; min-width: 280px;">
      <label class="form-label">款号</label>
      <Select
        :value="modelValue.style_no || undefined"
        :options="styleOptions"
        placeholder="输入货号或款名搜索…"
        allow-clear
        show-search
        option-filter-prop="label"
        style="width: 100%"
        @update:value="(v: unknown) => update('style_no', typeof v === 'string' ? v : '')"
      />
    </div>

    <div style="flex: 1; min-width: 200px;">
      <label class="form-label">单据日期</label>
      <Input
        :value="modelValue.doc_date"
        placeholder="YYYY-MM-DD"
        style="width: 100%"
        @update:value="(v: string) => update('doc_date', v)"
      />
    </div>

    <div style="flex: 1; min-width: 160px;">
      <label class="form-label">一扎件数（参考）</label>
      <InputNumber
        :value="modelValue.bundle_qty"
        :min="1"
        :precision="0"
        style="width: 100%"
        @update:value="(v: unknown) => update('bundle_qty', typeof v === 'number' ? v : 1)"
      />
    </div>

    <div style="flex: 1; min-width: 160px;">
      <label class="form-label">色码</label>
      <Input
        :value="modelValue.color_code"
        placeholder="如 WHT / BLK"
        style="width: 100%"
        @update:value="(v: string) => update('color_code', v)"
      />
    </div>

    <div style="flex: 1; min-width: 160px;">
      <label class="form-label">色组</label>
      <Input
        :value="modelValue.color_group"
        placeholder="如 WHT-GRP"
        style="width: 100%"
        @update:value="(v: string) => update('color_group', v)"
      />
    </div>

    <div style="flex: 1; min-width: 200px;">
      <label class="form-label">工序</label>
      <Select
        :value="modelValue.operation_no"
        :options="operationOptions"
        placeholder="请选择工序"
        allow-clear
        show-search
        style="width: 100%"
        @update:value="(v: unknown) => update('operation_no', typeof v === 'string' ? v : '')"
      />
    </div>

    <div style="flex: 1; min-width: 320px;">
      <label class="form-label">来源裁剪单</label>
      <Select
        :value="modelValue.source_cutting_order_id ?? undefined"
        :options="sourceOrders.map(o => ({ value: o.id, label: `${o.doc_no} · ${o.doc_date}` }))"
        :loading="loadingSources"
        placeholder="先选款号/色码，再选来源裁剪单…"
        allow-clear
        show-search
        style="width: 100%"
        @update:value="(v: unknown) => update('source_cutting_order_id', asId(v))"
      />
    </div>

    <div style="flex: 1; min-width: 320px; width: 100%;">
      <label class="form-label">备注</label>
      <Input
        :value="modelValue.remark"
        placeholder="选填"
        style="width: 100%"
        @update:value="(v: string) => update('remark', v)"
      />
    </div>
  </div>
</template>

<style scoped>
.form-label {
  display: block;
  font-size: var(--font-size-sm);
  color: var(--color-text-second);
  margin-bottom: var(--space-1);
}
</style>