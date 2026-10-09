<script setup lang="ts">
/**
 * 打菲单表头字段（新建页）。
 *
 * 字段：
 * - 车间（Combo，UUID）
 * - 款号（Combo，UUID，走 `searchStyleOptionsById`）
 * - 单据日期
 * - 一扎件数（参考值，InputNumber）
 * - 色码（文本）
 * - 色组（文本）
 * - 工序（Combo，value = operation_no）
 * - 来源裁剪单（a-select，本地过滤，显示手数/出数/每手件数）
 * - 备注
 */
import { Input, InputNumber, Select } from 'ant-design-vue'
import type { AvailableOutputOut } from '@garment/shared'

export interface HeaderFieldsValue {
  workshop_id: string | null
  style_id: string | null
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
  availableSources: AvailableOutputOut[]
  loadingSources: boolean
}

defineOptions({ name: 'BundlingHeaderFields' })

const props = defineProps<Props>()
const emit = defineEmits<{ 'update:modelValue': [value: HeaderFieldsValue]; sourceChange: [] }>()

function update<K extends keyof HeaderFieldsValue>(key: K, value: HeaderFieldsValue[K]): void {
  const next = { ...props.modelValue, [key]: value }
  emit('update:modelValue', next)
  if (key === 'source_cutting_order_id' || key === 'style_id' || key === 'color_code') {
    emit('sourceChange')
  }
}

</script>

<template>
  <div style="display: flex; flex-wrap: wrap; gap: var(--space-4); padding: var(--space-3);">
    <div style="flex: 1; min-width: 280px;">
      <label class="form-label">车间</label>
      <Combo
        :model-value="modelValue.workshop_id"
        :options="workshopOptions"
        placeholder="输入关键字搜索车间…"
        allow-clear
        @update:model-value="(v: string | null) => update('workshop_id', v)"
      />
    </div>

    <div style="flex: 1; min-width: 280px;">
      <label class="form-label">款号</label>
      <Combo
        :model-value="modelValue.style_id"
        :options="styleOptions"
        placeholder="输入货号或款名搜索…"
        allow-clear
        @update:model-value="(v: string | null) => update('style_id', v)"
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
        @update:value="(v: number | null) => update('bundle_qty', v ?? 1)"
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
      <a-select
        :model-value="modelValue.source_cutting_order_id"
        :options="availableSources.map(s => ({ value: s.cutting_size_line_id, label: `${s.size_code} · 手数:${s.hands} · 出数:${s.output_qty} · 每手:${s.qty_per_hand}` }))"
        :loading="loadingSources"
        placeholder="先选款号/色码，再选来源裁剪单…"
        allow-clear
        show-search
        style="width: 100%"
        @update:model-value="(v: string | null) => update('source_cutting_order_id', v)"
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