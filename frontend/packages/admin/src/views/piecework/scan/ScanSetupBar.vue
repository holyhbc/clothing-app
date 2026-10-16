<script setup lang="ts">
/**
 * 工位机设置条（`04 §5.1` 步骤 1）：选定工位号 + 操作员 + 默认工序 + 暂停开关。
 *
 * ⚠️ 两个下拉都是**远程搜索**（`06 §2.2`：100+ 项用远程搜索 + 防抖 300ms）：
 * 操作员来自 `GET /piecework/operators`（数据范围内，label =「工号 姓名」，
 * T-PIECE-006），工序来自基础资料 `/operations/options`。两者都可能上百条，
 * 一次全拉会拖慢工位机首屏，而且组长在几百条里也挑不出人。
 */
import { onMounted, ref, watch } from 'vue'
import { Input, Select, Switch } from 'ant-design-vue'
import type { OptionOut, PieceworkOperatorOptionOut } from '@garment/shared'
import { listPieceworkOperators } from '@/api/piecework'
import { baseApi } from '@/api/base'

const workstationNo = defineModel<string>('workstationNo', { required: true })
const employeeId = defineModel<string | undefined>('employeeId', { required: true })
const operationNo = defineModel<string | undefined>('operationNo', { required: true })
const paused = defineModel<boolean>('paused', { required: true })

const operatorOptions = ref<PieceworkOperatorOptionOut[]>([])
const operatorLoading = ref(false)
const operationOptions = ref<OptionOut[]>([])
const operationLoading = ref(false)

let operatorTimer: ReturnType<typeof setTimeout> | null = null

async function searchOperators(keyword: string): Promise<void> {
  operatorLoading.value = true
  try {
    operatorOptions.value = await listPieceworkOperators({ q: keyword, size: 20 })
  } finally {
    operatorLoading.value = false
  }
}

function onOperatorSearch(keyword: string): void {
  if (operatorTimer !== null) clearTimeout(operatorTimer)
  operatorTimer = setTimeout(() => void searchOperators(keyword), 300)
}

async function searchOperations(keyword: string): Promise<void> {
  operationLoading.value = true
  try {
    operationOptions.value = await baseApi.operations.options(keyword)
  } finally {
    operationLoading.value = false
  }
}

onMounted(() => {
  void searchOperators('')
  void searchOperations('')
})

// 暂停开关关闭时应立即恢复监听，这里无需额外逻辑；仅保留 watch 以便将来扩展。
watch(paused, () => {})
</script>

<template>
  <div class="setup">
    <label class="setup__field">
      <span class="setup__label">工位号</span>
      <Input
        v-model:value="workstationNo"
        class="setup__workstation"
        placeholder="如 WS-01"
        :maxlength="32"
        allow-clear
      />
    </label>

    <label class="setup__field">
      <span class="setup__label">操作员</span>
      <Select
        v-model:value="employeeId"
        class="setup__select"
        placeholder="工号 / 姓名搜索"
        show-search
        allow-clear
        :filter-option="false"
        :options="operatorOptions"
        :loading="operatorLoading"
        @search="onOperatorSearch"
      />
    </label>

    <label class="setup__field">
      <span class="setup__label">默认工序</span>
      <Select
        v-model:value="operationNo"
        class="setup__select"
        placeholder="工序号搜索"
        show-search
        allow-clear
        :filter-option="false"
        :options="operationOptions"
        :loading="operationLoading"
        @search="searchOperations"
      />
    </label>

    <label class="setup__field setup__field--switch">
      <span class="setup__label">暂停扫码</span>
      <Switch v-model:checked="paused" />
    </label>
  </div>
</template>

<style scoped>
.setup {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 24px;
  align-items: flex-end;
}

.setup__field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
}

.setup__field--switch {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.setup__label {
  font-size: 13px;
  color: var(--color-text-third);
}

.setup__workstation {
  width: 140px;
}

.setup__select {
  width: 220px;
}
</style>
