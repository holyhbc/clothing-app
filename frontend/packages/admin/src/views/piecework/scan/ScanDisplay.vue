<script setup lang="ts">
/**
 * 工位机大字显示（`06 §2.5`：数字大字 + `tabular-nums`，远处可读）。
 *
 * 只负责渲染，**不含任何状态**：计数与缓冲都在 `useScanMachine` 里，
 * 这样「计数从哪来」与「计数怎么显示」不会互相纠缠（Big 卡数字错位时，
 * 一眼能分清是数据问题还是样式问题）。
 */
import type { ScanState } from './useScanMachine'

defineProps<{ state: ScanState }>()
</script>

<template>
  <div class="display">
    <div class="display__counters">
      <div class="display__cell">
        <span class="display__value">{{ state.todayHands }}</span>
        <span class="display__unit">手</span>
      </div>
      <div class="display__cell">
        <span class="display__value">{{ state.todayQty }}</span>
        <span class="display__unit">件</span>
      </div>
      <div class="display__cell">
        <span class="display__value display__value--money">¥{{ state.todayAmount }}</span>
      </div>
    </div>

    <div class="display__scan" :class="`display__scan--${state.flash ?? 'idle'}`">
      <div class="display__hint">⌨ 等待扫码…</div>
      <div class="display__buffer">{{ state.buffer }}</div>
    </div>

    <div
      v-if="state.message !== ''"
      class="display__result"
      :class="`display__result--${state.phase}`"
      role="status"
    >
      {{ state.message }}
    </div>
    <div v-else class="display__result display__result--idle">
      今日：{{ state.todayHands }} 手 · {{ state.todayQty }} 件
    </div>
  </div>
</template>

<style scoped>
.display {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.display__counters {
  display: flex;
  gap: 48px;
  align-items: baseline;
}

.display__cell {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.display__value {
  /* `06 §2.5` 第 5 条：2xl 以上、等宽数字 */
  font-size: 48px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  letter-spacing: 1px;
}

.display__value--money {
  font-size: 36px;
  color: var(--color-danger);
}

.display__unit {
  font-size: 18px;
  color: var(--color-text-third);
}

.display__scan {
  border: 3px solid var(--color-border);
  border-radius: 8px;
  padding: 24px 20px;
  min-height: 96px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  transition: border-color 0.1s, background-color 0.1s;
}

.display__scan--ok {
  border-color: var(--color-success);
  background-color: color-mix(in srgb, var(--color-success) 10%, var(--color-bg-card));
}

.display__scan--warn {
  border-color: var(--color-warning);
  background-color: color-mix(in srgb, var(--color-warning) 10%, var(--color-bg-card));
}

.display__scan--error {
  border-color: var(--color-danger);
  background-color: color-mix(in srgb, var(--color-danger) 8%, var(--color-bg-card));
}

.display__hint {
  font-size: 14px;
  color: var(--color-text-third);
}

.display__buffer {
  font-size: 22px;
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}

.display__result {
  min-height: 24px;
  font-size: 18px;
  font-weight: 600;
}

.display__result--success {
  color: var(--color-success);
}

.display__result--cached {
  color: var(--color-warning);
}

.display__result--duplicate {
  color: var(--color-warning);
}

.display__result--error {
  color: var(--color-danger);
}

.display__result--idle {
  color: var(--color-text-third);
  font-weight: 400;
}
</style>
