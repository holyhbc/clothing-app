<script setup lang="ts">
/**
 * 工位机扫码页（T-PIECE-007 在线主链路 + T-PIECE-008 离线队列）。
 *
 * ## 在线直连、离线入队
 *
 * `useScanMachine` 负责扫码链路；联网时它直连 `POST /piecework/count`（T-PIECE-007
 * 语义不变），离线或可重试失败时把**同一 payload + 同一幂等键**交给 `useOfflineQueue`
 * 落 IndexedDB，本页只负责把队列状态显示出来并驱动回放（设计 §6.2）。
 *
 * ## 计数为什么不在前端累加
 *
 * 大字卡的手数/件数/金额**只用接口返回的 `today_*` 覆盖**：幂等命中（连按两下、
 * 网络重试）时服务端不会重复计数，前端若自己 `+1` 就会与真实值错位。离线缓存同样
 * **不计入**大字卡（未提交就不是权威值）。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Alert, Button, Card, InputNumber, Space, Switch, Tag } from 'ant-design-vue'
import ScanSetupBar from './ScanSetupBar.vue'
import ScanDisplay from './ScanDisplay.vue'
import QueuePanel from './offline/QueuePanel.vue'
import { createScanMachine } from './useScanMachine'
import type { ScanConfig } from './useScanMachine'
import { createOfflineQueue } from './offline/useOfflineQueue'
import { getDeviceId } from './offline/queue'
import type { QueueRecord } from './offline/queue'

defineOptions({ name: 'PieceworkScan' })

const STORAGE_KEY = 'piecework.workstation'
const FLUSH_INTERVAL_MS = 15_000

const queue = createOfflineQueue()
const queueState = queue.state

const machine = createScanMachine({
  enqueue: queue.enqueue,
  isOnline: () => queueState.online,
  deviceId: getDeviceId,
})
const scan = machine.state

const workstationNo = ref('')
const employeeId = ref<string | undefined>(undefined)
const operationNo = ref<string | undefined>(undefined)
const paused = ref(false)

const partial = ref(false)
const partialQty = ref<number | null>(null)

const config = computed<ScanConfig>(() => ({
  employeeId: employeeId.value ?? null,
  workstationNo: workstationNo.value.trim() === '' ? null : workstationNo.value.trim(),
  operationNo: operationNo.value ?? null,
  paused: paused.value,
  qty: partial.value ? (partialQty.value ?? null) : null,
}))

const ready = computed(
  () =>
    config.value.employeeId !== null &&
    config.value.workstationNo !== null &&
    config.value.operationNo !== null,
)

const statusType = computed<'success' | 'warning'>(() =>
  queueState.online && !queueState.unavailable ? 'success' : 'warning',
)

const statusText = computed(() => {
  if (queueState.unavailable) return '离线缓存不可用（浏览器已禁用本地存储），请保持网络畅通'
  if (!queueState.online) return `离线（缓存 ${queueState.pending} 条待提交）`
  return queueState.pending > 0 ? `在线 · 缓存 ${queueState.pending} 条待提交` : '在线'
})

const problems = ref<QueueRecord[]>([])
const problemCount = computed(() => queueState.conflict + queueState.failed)

async function reloadProblems(): Promise<void> {
  try {
    problems.value = await queue.listProblems()
  } catch {
    problems.value = []
  }
}

// 工位号是本页的“页面配置项”（无 workstations 表），刷新后要还在。
function restore(): void {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved !== null) workstationNo.value = saved
  } catch {
    // localStorage 不可用（隐私模式）：不致命，工位号重新输入即可。
  }
}

watch(workstationNo, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, value)
  } catch {
    // 同上，忽略。
  }
})

watch(() => [queueState.conflict, queueState.failed], () => void reloadProblems())

function onKeydown(event: KeyboardEvent): void {
  if (machine.onKey(event, config.value)) event.preventDefault()
}

/**
 * antd `InputNumber` 的 `@update:value` 发的是 `ValueType`（`string | number | null`），
 * 且 `exactOptionalPropertyTypes` 下不能把 `undefined` 绑给 `value`。这里用 `unknown`
 * 收窄：只放数字通过，其余（含清空产生的 `null` / 字符串）一律当「未填」。
 */
function onPartialQty(value: unknown): void {
  partialQty.value = typeof value === 'number' ? value : null
}

function onNetworkChange(): void {
  void queue.refresh()
  if (queueState.online) void queue.flush().then(reloadProblems)
}

async function onRetryRecord(id: string): Promise<void> {
  await queue.retry(id)
  await queue.flush()
  await reloadProblems()
}

async function onRemoveRecord(id: string): Promise<void> {
  await queue.remove(id)
  await reloadProblems()
}

let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  restore()
  // ★ 页面加载即注册页面级监听，用户不用点任何地方就能扫（`06 §2.5` 第 1 条）。
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('online', onNetworkChange)
  window.addEventListener('offline', onNetworkChange)
  void queue.refresh().then(reloadProblems)
  void queue.flush().then(reloadProblems)
  timer = setInterval(() => void queue.flush(), FLUSH_INTERVAL_MS)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('online', onNetworkChange)
  window.removeEventListener('offline', onNetworkChange)
  if (timer !== null) clearInterval(timer)
  machine.dispose()
})
</script>

<template>
  <div class="scan">
    <Alert :type="statusType" show-icon :message="statusText" banner />

    <Card :bordered="false">
      <ScanSetupBar
        v-model:workstation-no="workstationNo"
        v-model:employee-id="employeeId"
        v-model:operation-no="operationNo"
        v-model:paused="paused"
      />
      <Space class="scan__partial" :size="8" align="center">
        <span class="scan__partial-label">部分生产</span>
        <Switch v-model:checked="partial" />
        <InputNumber
          v-if="partial"
          :value="partialQty ?? ''"
          :min="1"
          :precision="0"
          placeholder="实际件数"
          class="scan__qty"
          @update:value="onPartialQty"
        />
        <span v-if="partial" class="scan__partial-hint">不填 = 该手全件</span>
      </Space>
    </Card>

    <Card :bordered="false" class="scan__board">
      <template #title>
        <Space :size="12" align="center">
          <span>工位机扫码</span>
          <Tag v-if="!ready" color="orange">请先完成上方设置</Tag>
          <Tag v-else-if="paused" color="red">已暂停</Tag>
          <Tag v-else color="green">就绪</Tag>
        </Space>
      </template>
      <template #extra>
        <Button size="small" @click="machine.reset()">清除提示</Button>
      </template>
      <ScanDisplay :state="scan" />
    </Card>

    <Card v-if="problemCount > 0" :bordered="false" class="scan__problems">
      <template #title>
        <Space :size="12" align="center">
          <span>待处理记录</span>
          <Tag v-if="queueState.conflict > 0" color="orange">冲突 {{ queueState.conflict }}</Tag>
          <Tag v-if="queueState.failed > 0" color="red">失败 {{ queueState.failed }}</Tag>
        </Space>
      </template>
      <QueuePanel
        :records="problems"
        @retry="onRetryRecord"
        @remove="onRemoveRecord"
      />
    </Card>
  </div>
</template>

<style scoped>
.scan {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.scan__partial {
  margin-top: 16px;
}

.scan__partial-label {
  font-size: 13px;
  color: var(--color-text-third);
}

.scan__partial-hint {
  font-size: 12px;
  color: var(--color-text-third);
}

.scan__qty {
  width: 120px;
}

.scan__board {
  min-height: 320px;
}

.scan__problems {
  border: 1px solid var(--color-border);
}
</style>
