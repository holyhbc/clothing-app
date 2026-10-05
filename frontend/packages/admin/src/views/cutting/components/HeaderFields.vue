<script setup lang="ts">
/**
 * 裁剪单**表头字段**（新建页与编辑页共用，T-CUT-001c-3a 抽出）。
 *
 * ## 为什么抽出来
 *
 * 新建页与编辑页的表头**字段集与校验完全相同**，而唯一差别是「能不能改」
 * （编辑页在已审核时全部 `disabled`）。不抽的话两份布局各 90 行，
 * 改一次「铺布层数的提示文案」要改两处 —— 而漏一处的后果是「新建页写着含 N 层、
 * 编辑页不写」，录入员在编辑时反而不知道出数的口径。
 *
 * ⚠️ **汇总五列不在这里**（后端入参连字段都没有，C6 由 service 重算）。
 * 页面底部那三个「合计」只是让用户对得上自己录入的，不是可提交的数据。
 */
import { computed } from 'vue'
import { DatePicker, Input, InputNumber, Radio } from 'ant-design-vue'
import dayjs from 'dayjs'
import { baseApi } from '@/api/base'
import { searchStyleOptionsById } from '@/api/cutting'
import Combo from '@/components/Combo.vue'
import { num } from './slotTypes'

/** 表头的可编辑字段（⚠️ 与后端 `CuttingOrderCreateIn` 的可传字段一致）。 */
export interface HeaderFields {
  workshop_id: string | null
  style_id: string | null
  doc_date: string
  delivery_date: string | null
  ply_count: number
  remark_source: string
  remark: string
}

const props = withDefaults(
  defineProps<{
    modelValue: HeaderFields
    /** 已审核等状态整块只读（`docs/06 §2.4`：已审核单据所有字段 disabled）。 */
    disabled?: boolean
    /** 款号 / 车间建单后**不可改**（历史单据按款号引用，改了等于换了个款）。 */
    lockIdentity?: boolean
  }>(),
  { disabled: false, lockIdentity: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: HeaderFields] }>()

/**
 * 两个「能不能改」在**脚本里**算好，而不是在模板里写 `disabled || lockIdentity`。
 *
 * ⚠️ 原因不是洁癖：模板里那个表达式的类型是 `boolean | undefined`（props 在模板中
 * 未经默认值收窄），而本仓开了 `exactOptionalPropertyTypes` —— 给 antd 组件显式传
 * `undefined` 是**编译错误**（`Consider adding 'undefined' to the types of the target's
 * properties`）。那条报错与「我只想禁用一个字段」毫无关系。
 */
const isDisabled = computed(() => props.disabled)
const isLocked = computed(() => props.disabled || props.lockIdentity)

function patch(fields: Partial<HeaderFields>): void {
  emit('update:modelValue', { ...props.modelValue, ...fields })
}

/** antd `DatePicker` 的值：dayjs 对象或空串；空值统一归一成 `string | null`。 */
function toDateText(value: unknown): string | null {
  if (value === null || value === '') return null
  if (typeof value === 'string') return value
  if (typeof value === 'object' && 'format' in value) {
    const dayjsLike = value as { format: (pattern: string) => string }
    return dayjsLike.format('YYYY-MM-DD')
  }
  return null
}

const ENTRY_MODE_HINT = '取第一个颜色的模式；「按比例带出」要在保存后于详情页用（需要单号）'
</script>

<template>
  <div class="header-grid">
    <div class="field">
      <label>车间<span class="req">*</span></label>
      <Combo
        :model-value="modelValue.workshop_id"
        :fetch-options="baseApi.workshops.optionsById"
        :disabled="isLocked"
        placeholder="选车间"
        @update:model-value="(value: string | null) => patch({ workshop_id: value })"
      />
      <small class="hint">数据范围按车间过滤（INV-8），车间主管只能建自己车间的单</small>
    </div>

    <div class="field">
      <label>款号<span class="req">*</span></label>
      <Combo
        :model-value="modelValue.style_id"
        :fetch-options="searchStyleOptionsById"
        :disabled="isLocked"
        placeholder="搜款号 / 款名"
        @update:model-value="(value: string | null) => patch({ style_id: value })"
      />
      <small class="hint">提交的是款号 <b>UUID</b>（后端要 id，不是款号字符串）</small>
    </div>

    <div class="field">
      <label>单据日期<span class="req">*</span></label>
      <DatePicker
        :value="modelValue.doc_date === '' ? '' : dayjs(modelValue.doc_date)"
        :disabled="isDisabled"
        style="width: 100%"
        @update:value="(value: unknown) => patch({ doc_date: toDateText(value) ?? '' })"
      />
      <small class="hint">决定所属期间，默认今天</small>
    </div>

    <div class="field">
      <label>交期</label>
      <DatePicker
        :value="modelValue.delivery_date === null ? '' : dayjs(modelValue.delivery_date)"
        :disabled="isDisabled"
        style="width: 100%"
        @update:value="(value: unknown) => patch({ delivery_date: toDateText(value) })"
      />
    </div>

    <div class="field">
      <label>铺布层数<span class="req">*</span></label>
      <InputNumber
        :value="modelValue.ply_count"
        :min="1"
        :precision="0"
        :disabled="isDisabled"
        style="width: 100%"
        @change="(value: string | number | null) => patch({ ply_count: Math.trunc(num(value) ?? 1) })"
      />
      <small class="hint">&gt; 1 时出数是**多层合计后的总件数**（C4）</small>
    </div>

    <div class="field">
      <label>默认录入模式</label>
      <!--
        ⚠️ 这里**只读展示**、不提供可改控件：真正生效的是**颜色级** `entry_mode`
        （C26），而它在明细里逐色选。放一个可改的表头控件会让人以为改这里能改全部
        颜色的模式 —— 实际不会，于是界面与落库不一致且没有报错。
      -->
      <Radio.Group value="MANUAL" size="small" disabled>
        <Radio.Button value="MANUAL">自定义明细</Radio.Button>
        <Radio.Button value="UNIFORM">统一件数</Radio.Button>
      </Radio.Group>
      <small class="hint">{{ ENTRY_MODE_HINT }}</small>
    </div>

    <div class="field">
      <label>来源备注</label>
      <Input
        :value="modelValue.remark_source"
        :disabled="isDisabled"
        placeholder="跟单张 / 客户要求"
        @change="
          (event: Event) => patch({ remark_source: (event.target as HTMLInputElement).value })
        "
      />
    </div>

    <div class="field">
      <label>备注</label>
      <Input
        :value="modelValue.remark"
        :disabled="isDisabled"
        placeholder="可选"
        @change="(event: Event) => patch({ remark: (event.target as HTMLInputElement).value })"
      />
    </div>
  </div>
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
</style>