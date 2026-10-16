<script setup lang="ts">
/**
 * 冲突 / 失败记录展开面板（T-PIECE-008，`07 §7`「不静默丢弃」）。
 *
 * 只负责展示与派发动作：记录在 `useOfflineQueue` 里，删除 / 重试也用它的方法。
 * 冲突项的文案复用 `useScanMachine.duplicateText` —— 在线冲突与离线回放冲突
 * 必须是**同一句提示**，否则工人会在两个地方看到两种说法。
 */
import { Empty, Tag } from 'ant-design-vue'
import { formatDateTime } from '@garment/shared'
import { duplicateText } from '../useScanMachine'
import type { QueueRecord } from './queue'

defineProps<{ records: readonly QueueRecord[] }>()
const emit = defineEmits<{ retry: [id: string]; remove: [id: string] }>()

function statusLabel(record: QueueRecord): string {
  return record.status === 'conflict' ? '冲突' : '失败'
}

function describe(record: QueueRecord): string {
  if (record.status === 'conflict' && record.conflict !== null) {
    return duplicateText(record.conflict)
  }
  return record.lastError?.message ?? '待人工处理'
}

function hint(record: QueueRecord): string {
  return record.status === 'conflict' ? '删除' : '重试'
}

function onPrimary(record: QueueRecord): void {
  if (record.status === 'conflict') emit('remove', record.id)
  else emit('retry', record.id)
}
</script>

<template>
  <div class="queue-panel">
    <Empty v-if="records.length === 0" description="无待处理记录" />
    <ul v-else class="queue-panel__list">
      <li v-for="record in records" :key="record.id" class="queue-panel__item">
        <div class="queue-panel__row">
          <Tag :color="record.status === 'conflict' ? 'orange' : 'red'">
            {{ statusLabel(record) }}
          </Tag>
          <span class="queue-panel__code">{{ record.payload.bundle_no }}</span>
          <span class="queue-panel__time">{{ formatDateTime(record.payload.scanned_at) }}</span>
        </div>
        <div class="queue-panel__msg">{{ describe(record) }}</div>
        <div class="queue-panel__actions">
          <a class="queue-panel__action" @click="onPrimary(record)">{{ hint(record) }}</a>
          <a
            v-if="record.status !== 'conflict'"
            class="queue-panel__action queue-panel__action--muted"
            @click="emit('remove', record.id)"
          >
            删除
          </a>
          <span v-else class="queue-panel__keep">不操作即保留</span>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.queue-panel {
  padding-top: 8px;
}

.queue-panel__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.queue-panel__item {
  padding: 8px 0;
  border-bottom: 1px solid var(--color-border);
}

.queue-panel__row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.queue-panel__code {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.queue-panel__time,
.queue-panel__keep {
  color: var(--color-text-third);
  font-size: 12px;
}

.queue-panel__msg {
  margin: 4px 0;
  color: var(--color-text-second);
}

.queue-panel__actions {
  display: flex;
  gap: 16px;
  align-items: center;
}

.queue-panel__action {
  cursor: pointer;
}

.queue-panel__action--muted {
  color: var(--color-text-third);
}
</style>
