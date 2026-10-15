<script setup lang="ts">
/**
 * 计件流水详情抽屉（T-PIECE-005；`GET /api/v1/piecework-logs/{log_id}`）。
 *
 * 展示：流水本体 + 码信息（第几手 / 手件数 / 当前计件人）+ 红冲关联链
 * （原流水 / 被哪些流水红冲，§6.1「响应要点」）。
 *
 * ⚠️ 只读：拉详情不写任何东西，不涉及权限点与状态机迁移。
 * ⚠️ `logId` 为 `null` 时关闭 —— 父组件用 `detailId` 单一真值源控制，不用 `v-model:open`。
 */
import { computed, ref, watch } from 'vue'
import { Descriptions, DescriptionsItem, Drawer, Spin, Tag } from 'ant-design-vue'
import { formatMoney, formatUnitPrice, formatDateTime } from '@garment/shared'
import type { PieceworkLogDetailOut } from '@garment/shared'
import { getPieceworkLog } from '@/api/piecework'

const props = defineProps<{ logId: string | null }>()
const emit = defineEmits<{ close: [] }>()

const loading = ref(false)
const detail = ref<PieceworkLogDetailOut | null>(null)
const failed = ref(false)

const open = computed({
  get: () => props.logId !== null,
  set: (value: boolean) => {
    if (!value) emit('close')
  },
})

const LOG_TYPE_LABEL: Record<string, { text: string; color: string }> = {
  SCAN: { text: '扫码', color: 'green' },
  MANUAL: { text: '手工补录', color: 'blue' },
  SUPPLEMENT: { text: '补录', color: 'cyan' },
  REVERSAL: { text: '红冲', color: 'red' },
}

async function load(id: string): Promise<void> {
  loading.value = true
  failed.value = false
  detail.value = null
  try {
    detail.value = await getPieceworkLog(id)
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}

watch(
  () => props.logId,
  (id) => {
    if (id !== null) void load(id)
  },
  { immediate: true },
)

const typeMeta = computed(() =>
  detail.value === null ? null : (LOG_TYPE_LABEL[detail.value.log_type] ?? null),
)
</script>

<template>
  <Drawer
    v-model:open="open"
    title="计件流水详情"
    :width="640"
    placement="right"
    @close="emit('close')"
  >
    <Spin :spinning="loading">
      <p v-if="failed" class="detail-error">详情加载失败，请关闭后重试</p>
      <template v-else-if="detail !== null">
        <Descriptions :column="2" size="small" bordered>
          <DescriptionsItem label="打菲号" :span="2">
            <span class="code">{{ detail.bundle_no }}</span>
          </DescriptionsItem>
          <DescriptionsItem label="款号">{{ detail.style_no }}</DescriptionsItem>
          <DescriptionsItem label="工序">{{ detail.operation_no }}</DescriptionsItem>
          <DescriptionsItem label="员工">{{ detail.employee_name ?? '—' }}</DescriptionsItem>
          <DescriptionsItem label="组别">{{ detail.group_no ?? '—' }}</DescriptionsItem>
          <DescriptionsItem label="工作日期">{{ detail.work_date }}</DescriptionsItem>
          <DescriptionsItem label="件数">{{ detail.qty }}</DescriptionsItem>
          <DescriptionsItem label="单价">¥{{ formatUnitPrice(detail.unit_price) }}</DescriptionsItem>
          <DescriptionsItem label="金额">¥{{ formatMoney(detail.amount, 2) }}</DescriptionsItem>
          <DescriptionsItem label="类型">
            <Tag :color="typeMeta?.color ?? 'default'">
              {{ typeMeta?.text ?? detail.log_type }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="结算">
            <span v-if="detail.payroll_id !== null">已结算</span>
            <span v-else>未结算</span>
          </DescriptionsItem>
          <DescriptionsItem label="扫描时间">{{ formatDateTime(detail.scanned_at) }}</DescriptionsItem>
          <DescriptionsItem label="创建时间">{{ formatDateTime(detail.created_at) }}</DescriptionsItem>
          <DescriptionsItem label="备注" :span="2">{{ detail.remark ?? '—' }}</DescriptionsItem>
        </Descriptions>

        <h3 v-if="detail.bundle" class="section-title">码信息</h3>
        <Descriptions v-if="detail.bundle" :column="2" size="small" bordered>
          <DescriptionsItem label="第几手">第 {{ detail.bundle.hands }} 手</DescriptionsItem>
          <DescriptionsItem label="手件数">{{ detail.bundle.bundle_qty }}</DescriptionsItem>
          <DescriptionsItem label="当前已计">{{ detail.bundle.counted_qty }}</DescriptionsItem>
          <DescriptionsItem label="当前计件人">
            {{ detail.bundle.counted_by_name ?? '（未计件）' }}
          </DescriptionsItem>
        </Descriptions>

        <template v-if="detail.reversal_of || (detail.reversed_by?.length ?? 0) > 0">
          <h3 class="section-title">红冲关联</h3>
          <ul class="related-list">
            <li v-if="detail.reversal_of">
              本流水是对
              <span class="code">{{ detail.reversal_of.work_date }}</span>
              原流水的红冲修正
            </li>
            <li v-for="rel in detail.reversed_by ?? []" :key="rel.log_id">
              已被
              <Tag :color="LOG_TYPE_LABEL[rel.log_type]?.color ?? 'default'">
                {{ LOG_TYPE_LABEL[rel.log_type]?.text ?? rel.log_type }}
              </Tag>
              {{ rel.work_date }} 修正
            </li>
          </ul>
        </template>
      </template>
    </Spin>
  </Drawer>
</template>

<style scoped>
.detail-error {
  color: var(--color-danger);
}

.section-title {
  margin: var(--space-4) 0 var(--space-2);
  font-size: var(--font-size-base);
  font-weight: 600;
}

.related-list {
  margin: 0;
  padding-left: var(--space-5);
  line-height: 2;
}

.code {
  font-family: var(--font-mono);
  white-space: nowrap;
}
</style>
