/**
 * 打菲单的「变更历史」抽屉（读 `document_logs`）。
 *
 * 抽出来是因为它是完整内聚的一个单元：自己的 open/loading/logs 状态、
 * 自己的数据加载、自己的列定义、自己的模板。留在 `Detail.vue` 里会让那个文件
 * 同时管「单据本身」和「操作留痕」两件事，而两者毫无关系。
 *
 * ⚠️ 只读：拉日志不写任何东西，所以不涉及权限点与状态机迁移。
 */
<script setup lang="ts">
import { ref, watch } from 'vue'
import { Drawer, Spin, Table } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { listBundlingOrderLogs } from '@/api/bundling'

const props = defineProps<{ orderId: string }>()

const open = ref(false)
const loading = ref(false)
const logs = ref<unknown[]>([])

const columns: ColumnsType<unknown> = [
  { key: 'created_at', title: '时间', dataIndex: 'created_at', width: 160 },
  { key: 'operator_name', title: '操作人', dataIndex: 'operator_name', width: 100 },
  { key: 'action', title: '动作', dataIndex: 'action', width: 100 },
  { key: 'from_status', title: '从', dataIndex: 'from_status', width: 80 },
  { key: 'to_status', title: '到', dataIndex: 'to_status', width: 80 },
  { key: 'reason', title: '原因', dataIndex: 'reason', width: 200 },
]

async function load(): Promise<void> {
  if (!props.orderId) return
  loading.value = true
  try {
    const result = await listBundlingOrderLogs(props.orderId, { page: 1, page_size: 100 })
    logs.value = result.items
  } catch {
    // 拉不到留痕不该让抽屉崩掉 —— 留痕是辅助信息，单据本身才是主体
    logs.value = []
  } finally {
    loading.value = false
  }
}

/** 对外：父组件点「变更历史」时调这个，而不是直接 `v-model:open`。 */
function show(): void {
  open.value = true
  void load()
}

// 切换到另一张单据时清掉上一张的留痕，避免张冠李戴
watch(
  () => props.orderId,
  () => {
    logs.value = []
    open.value = false
  },
)

defineExpose({ show })
</script>

<template>
  <Drawer v-model:open="open" title="变更历史" :width="720" placement="right">
    <Spin :spinning="loading">
      <Table
        :columns="columns"
        :data-source="logs"
        :row-key="(row: unknown) => (row as Record<string, unknown>).id as string"
        size="small"
        :pagination="false"
      />
    </Spin>
  </Drawer>
</template>
