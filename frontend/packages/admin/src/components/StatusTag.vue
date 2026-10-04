<script setup lang="ts">
/**
 * 状态标签（docs/06 §1「状态色映射固定，全系统一致」、§2.2「状态列用 `StatusTag`
 * 组件，不用纯文字」）。
 *
 * ## 为什么色值完全不在这个文件里
 *
 * 映射的唯一来源是 `shared/enums/status.ts`（数据来自后端 `DocumentStatus`，且后端
 * `test_permission_registry.py` 会断言键集合一致）。这里如果自己写一张色值表，
 * 就会出现"单据页的已审核是绿的、裁剪页的是蓝的"，而且没有任何东西会报错 ——
 * 所以组件只负责**把 token 名拼成 `var(--color-xxx)` 并渲染**，色值一个字都不写。
 */
import { computed } from 'vue'
import { statusColor, statusMeta } from '@garment/shared'

const props = withDefaults(
  defineProps<{
    /** 后端返回的状态字符串。未知值会原样显示而不是空白（见 computed）。 */
    status: string
    /** 未知状态时的额外提示（hover 可见），例如"前端版本较旧"。 */
    unknownHint?: string
  }>(),
  { unknownHint: '' },
)

/**
 * 未知状态（后端加了一档而前端没跟上）时**原样显示**。
 *
 * ⚠️ 不显示空白也不静默降级成某个已知状态：空白标签会让用户以为系统坏了，
 *    而把它错映射成「草稿」会让人以为单据还没提交 —— 两种都比"显示原始值 + 提示"
 *    更容易导致误操作。
 */
const meta = computed(() => statusMeta(props.status))
const isUnknown = computed(() => meta.value === null)
const label = computed(() => meta.value?.text ?? props.status)
const strike = computed(() => meta.value?.strikeThrough === true)
const hint = computed(() => (isUnknown.value ? `未知状态：${props.status}` : ''))
</script>

<template>
  <!--
    `title` 而不是 Tooltip：状态列在表格里密度很高，一个轻量原生提示比弹层更合适，
    也省掉每个 Tag 一个 Tooltip 组件的开销（docs/06 §6「所有按钮有 title 或 Tooltip」）。
  -->
  <span
    class="status-tag"
    :style="{ color: statusColor(status) }"
    :title="hint === '' ? label : hint"
    :data-status="status"
    :data-unknown="isUnknown ? 'true' : undefined"
  >
    <span :class="{ 'is-struck': strike }">{{ label }}</span>
  </span>
</template>

<style scoped>
.status-tag {
  font-size: var(--font-size-base);
  white-space: nowrap;
}

/* 已作废要加删除线（docs/06 §1 表） */
.is-struck {
  text-decoration: line-through;
}
</style>
