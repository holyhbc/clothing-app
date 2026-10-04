<script setup lang="ts">
/**
 * 工序与单价模板复制（ADR-0009 §3、docs/modules/01 §5.1）。
 *
 * ## 为什么 `copy_mode` 与 `conflict_policy` **都不给默认值**（TC-W35）
 *
 * 后端 `TemplateCopyIn` 的这两个字段**必填且没有默认值**，注释写得很直白：
 * 「默默覆盖别人配好的工价比报错危险得多」（ADR-0026 风险 ③ 同类问题）。
 * 界面上对应地就是：**没选就不让提交**，而不是给一个"沿用源价 + 覆盖"的默认值
 * 让用户没意识到就点下去。
 *
 * ## 复制完必须说的话（modules/01 §5.1）
 *
 * 后端返回的 `messages[]` 里**一定**含「尺码比例未复制」—— 比例是**款号自己的销售
 * 构成**，复制别的款的毫无意义。不提示的话用户以为"工序和比例都配好了"，问题推迟到
 * 出布头对不上账时才爆。所以这里**逐条渲染** `messages`，而不是只弹一句"复制成功"。
 */
import { computed, ref, watch } from 'vue'
import {
  Alert,
  Button,
  InputNumber,
  Modal,
  RadioGroup,
  Select,
  Space,
  Typography,
  message,
} from 'ant-design-vue'
import { PERM } from '@garment/shared'
import type { ConflictPolicy, TemplateCopyMode, TemplateCopyOut } from '@garment/shared'
import { copyStyleTemplate, searchStyleOptions } from '@/api/styles'
import Combo from '@/components/Combo.vue'
import CopyResult from '@/views/base/styles/CopyResult.vue'

const props = defineProps<{ open: boolean; targetStyleNo: string }>()

defineOptions({ name: 'StyleCopyDialog' })

const emit = defineEmits<{ close: [] }>()

/**
 * 复制模式（**取值逐字来自后端枚举** `TemplateCopyMode`）。
 *
 * ⚠️ 这里第一版是照直觉写的 `COPY_PRICE` / `COPY_STRUCTURE`，**三个值全错** ——
 *    正确的��� `COPY_PRICE_AS_IS` / `COPY_STRUCTURE_ONLY`。是 `vue-tsc` 抓出来的：
 *    `copy_mode` 的类型是那个枚举的联合，传别的值直接 TS2322。
 *    枚举值猜错的后果比类型错误严重得多 —— 编译通过的话，复制功能 100% 失败，
 *    而页面上只显示一句「参数不合法」，没人知道是自己写错了枚举。
 */
const COPY_MODES = [
  { value: 'COPY_PRICE_AS_IS', label: '沿用源款单价' },
  { value: 'COPY_PRICE_WITH_RATIO', label: '按比例上浮（1.08 = 上浮 8%）' },
  { value: 'COPY_STRUCTURE_ONLY', label: '只复制工序结构，不复制单价' },
]

/** 冲突策略（`ConflictPolicy`）。⚠️ `ABORT` 是「整个复制中止」而不是「跳过」，别写成 KEEP。 */
const CONFLICT_POLICIES = [
  { value: 'OVERWRITE', label: '覆盖目标款已有单价' },
  { value: 'SKIP', label: '保留目标款已有单价（跳过该工序）' },
  { value: 'MERGE', label: '保留已有，只补没有的' },
  { value: 'ABORT', label: '中止整个复制（一个冲突就都不写）' },
]

const sourceStyleNo = ref<string | null>(null)
const copyMode = ref<string | null>(null)
const conflictPolicy = ref<string | null>(null)
const priceRatio = ref<number | null>(null)
const submitting = ref(false)
const errorText = ref<string | null>(null)
const result = ref<TemplateCopyOut | null>(null)

/** 模式②必填 `price_ratio`（后端 Schema 层就拒），所以这里跟着必填。 */
const ratioRequired = computed(() => copyMode.value === 'COPY_PRICE_WITH_RATIO')

const canSubmit = computed(
  () =>
    sourceStyleNo.value !== null &&
    copyMode.value !== null &&
    conflictPolicy.value !== null &&
    (!ratioRequired.value || priceRatio.value !== null),
)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    // 每次打开重置：上一次的源款与选择留着，用户会以为"复制的是刚才那个"
    sourceStyleNo.value = null
    copyMode.value = null
    conflictPolicy.value = null
    priceRatio.value = null
    errorText.value = null
    result.value = null
  },
)

async function onSubmit(): Promise<void> {
  if (!canSubmit.value || submitting.value) return
  submitting.value = true
  errorText.value = null
  try {
    result.value = await copyStyleTemplate(props.targetStyleNo, sourceStyleNo.value as string, {
      // ⚠️ 断言成枚举本身而不是 `string`：这样写错枚举值会**编译报错**而不是运行时 10001
      copy_mode: copyMode.value as TemplateCopyMode,
      conflict_policy: conflictPolicy.value as ConflictPolicy,
      // ⚠️ 只在模式②传：模式① / ③ 传了会被后端当成"上浮 0 倍"还是"忽略"取决于实现，
      //    而 `TemplateCopyIn.price_ratio` 是可空的 —— 这里不给就是不给，不填 1。
      price_ratio: ratioRequired.value ? priceRatio.value : null,
    })
    void message.success('复制完成')
  } catch (caught) {
    errorText.value = caught instanceof Error ? caught.message : '复制失败'
  } finally {
    submitting.value = false
  }
}

function onClose(): void {
  emit('close')
}
</script>

<template>
  <Modal
    :open="open"
    title="工序与单价模板复制"
    :width="680"
    :mask-closable="false"
    @cancel="onClose"
  >
    <Space direction="vertical" size="middle" style="width: 100%">
      <Alert type="warning" show-icon>
        <template #message>
          复制会把<strong>源款号的工序结构与单价整套写入 {{ targetStyleNo }}</strong>
        </template>
        <template #description>
          ⚠️ 分类价属全厂口径，**不复制**（ADR-0026 §4）。⚠️ 尺码比例也**不复制** ——
          比例是款号自己的销售构成，别人的比例对自己没有意义。
        </template>
      </Alert>

      <Alert v-if="errorText" type="error" :message="errorText" show-icon />

      <!-- 复制成功之后不再让用户改参数，直接给结果（`CopyResult` 自己带 messages 展示） -->
      <CopyResult v-if="result" :result="result" />

      <!-- 参数区 -->
      <template v-else>
        <Space direction="vertical" size="small" style="width: 100%">
          <label class="field-label">源款号</label>
          <Combo
            v-model="sourceStyleNo"
            :fetch-options="searchStyleOptions"
            placeholder="输入要复制的源款号"
          />

          <label class="field-label">复制模式（必选）</label>
          <RadioGroup
            :value="copyMode ?? undefined"
            :options="COPY_MODES"
            option-type="button"
            button-style="solid"
            @update:value="(value: unknown) => (copyMode = value === null ? null : String(value))"
          />

          <template v-if="ratioRequired">
            <label class="field-label">上浮比例（模式②必填）</label>
            <InputNumber
              :value="priceRatio ?? ''"
              :min="0.0001"
              :precision="4"
              :step="0.01"
              style="width: 200px"
              placeholder="1.08 = 上浮 8%"
              @update:value="
                (value: string | number | null) =>
                  (priceRatio = value === null ? null : Number(value))
              "
            />
          </template>

          <label class="field-label">目标款已有同工序时怎么办（必选）</label>
          <Select
            :value="conflictPolicy ?? undefined"
            :options="CONFLICT_POLICIES"
            placeholder="必选 —— 默认会覆盖的风险太大"
            style="width: 100%"
            allow-clear
            @update:value="
              (value: unknown) =>
                (conflictPolicy = value === undefined || value === null ? null : String(value))
            "
          />

          <!--
            ⚠️ 没选参数时按钮禁用（TC-W35）。文案要说清**为什么**禁用，
            否则用户会以为是按钮坏了。
          -->
          <Button
            v-can="PERM.BASE_RATE_TEMPLATE_MANAGE"
            type="primary"
            :disabled="!canSubmit"
            :loading="submitting"
            @click="onSubmit"
          >
            复制到 {{ targetStyleNo }}
          </Button>
          <Typography.Text v-if="!canSubmit" type="secondary" class="field-hint">
            需要先选：源款号、复制模式、冲突策略{{ ratioRequired ? '、上浮比例' : '' }}
          </Typography.Text>
        </Space>
      </template>
    </Space>
  </Modal>
</template>

<style scoped>
.field-label {
  font-size: var(--font-size-sm);
  color: var(--color-text-second);
}

.field-hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.skipped-list {
  margin: 0;
  padding-left: var(--space-4);
}

.money {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
