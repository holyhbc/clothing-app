<script setup lang="ts">
/**
 * 工位机扫码页（T-PIECE-007，`04 §5.1` 链路 A + `06 §2.5` 扫码交互）。
 *
 * ## 本页只做「在线主链路」
 *
 * `06 §2.5` 第 6 条 / `07 §7` 要求 IndexedDB 离线队列，那会引入仓库当前没有的
 * 运行时依赖（PWA/IndexedDB），已拆到 **T-PIECE-008**。本页只做：全局扫码捕获、
 * 设置条、提交、四种反馈、暂停开关，以及一个**只提示不落队列**的在线/离线状态条。
 *
 * ## 计数为什么不在前端累加
 *
 * 大字卡的手数/件数/金额**只用接口返回的 `today_*` 覆盖**：幂等命中（连按两下、
 * 网络重试）时服务端不会重复计数，前端若自己 `+1` 就会与真实值错位，而那正是
 * 工人最敏感的数字（工钱）。详见 `useScanMachine`。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Alert, Button, Card, InputNumber, Space, Switch, Tag } from 'ant-design-vue'
import ScanSetupBar from './ScanSetupBar.vue'
import ScanDisplay from './ScanDisplay.vue'
import { createScanMachine } from './useScanMachine'
import type { ScanConfig } from './useScanMachine'

defineOptions({ name: 'PieceworkScan' })

const STORAGE_KEY = 'piecework.workstation'

const machine = createScanMachine()
const { state } = machine

const workstationNo = ref('')
const employeeId = ref<string | undefined>(undefined)
const operationNo = ref<string | undefined>(undefined)
const paused = ref(false)

const partial = ref(false)
const partialQty = ref<number | null>(null)
const online = ref(typeof navigator === 'undefined' ? true : navigator.onLine)

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

function updateOnline(): void {
  online.value = navigator.onLine
}

onMounted(() => {
  restore()
  // ★ 页面加载即注册页面级监听，用户不用点任何地方就能扫（`06 §2.5` 第 1 条）。
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('online', updateOnline)
  window.addEventListener('offline', updateOnline)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('online', updateOnline)
  window.removeEventListener('offline', updateOnline)
  machine.dispose()
})
</script>

<template>
  <div class="scan">
    <Alert
      :type="online ? 'success' : 'warning'"
      show-icon
      :message="online ? '在线' : '离线（本页未启用离线队列，请检查网络后再扫）'"
      banner
    />

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
      <ScanDisplay :state="state" />
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
</style>
