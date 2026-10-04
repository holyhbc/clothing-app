<script setup lang="ts">
/**
 * 变更历史抽屉（docs/06 §2.3「变更历史（Drawer，展示 document_logs）」）。
 *
 * ## 为什么单独做成组件
 *
 * 每一个详情页都要它（款号、裁剪单、打菲单、计件、工资…），而它的三处最容易写错是
 * **共性**的：① 时间倒序；② 单号 / 单据号可复制；③ 空态要说清"这个单据还没被改过"
 * 而不是"暂无数据"。写九遍就有九种写法，而其中一种忘了倒序，界面就变成"最早的操作在
 * 最上面"，用户会以为这个单据刚建（实际上它已经被改过十次了）。
 *
 * ⚠️ **`doc_type` 与 `doc_no` 都必填**：审计表没有归属列，"查全部日志"在数据范围
 *    （Q-P0-05 跟单只看自己的款号）下无法表达。所以抽屉必须由页面告诉它"查哪张单据"。
 */
import { ref, watch } from 'vue'
import { Alert, Drawer, Empty, Skeleton, Space, Tag, Typography } from 'ant-design-vue'
import { formatDateTime } from '@garment/shared'
import type { DocumentLogOut } from '@garment/shared'
import { listDocumentLogs } from '@/api/styles'

const props = defineProps<{
  open: boolean
  docType: string
  docNo: string
  /** 抽屉标题；不传用单号。 */
  title?: string
}>()

defineOptions({ name: 'HistoryDrawer' })

/** 关闭。⚠️ 抽屉自己不发 `update:open` —— antd 的 Drawer 只在 `close` 上有事件，
 *  由页面决定是 `v-model:open` 还是单向 `:open` + `@close`。 */
defineEmits<{ close: [] }>()

/** 动作 → 中文。⚠️ 取值来自后端 `document_logs.action`（08 §1.1 的 DocumentAction）。 */
const ACTION_LABELS: Readonly<Record<string, string>> = {
  CREATE: '新建',
  UPDATE: '修改',
  DELETE: '删除',
  RESTORE: '恢复',
  SUBMIT: '提交',
  APPROVE: '审核',
  REJECT: '驳回',
  WITHDRAW: '撤回',
  REVERSE: '反审核',
  CANCEL: '作废',
  IMPORT: '导入',
}

const rows = ref<DocumentLogOut[]>([])
const total = ref(0)
const loading = ref(false)
const errorText = ref<string | null>(null)

function actionLabel(action: string): string {
  // ⚠️ 未知动作**原样显示**而不是空白：后端加了一个新动作而前端没跟上时，
  // 空白标签会让用户以为这里没有动作（StatusTag 当初也是同一个坑）
  return ACTION_LABELS[action] ?? action
}

async function load(): Promise<void> {
  if (props.docType === '' || props.docNo === '') return
  loading.value = true
  errorText.value = null
  try {
    const page = await listDocumentLogs({ doc_type: props.docType, doc_no: props.docNo })
    rows.value = page.items
    total.value = page.total
  } catch (caught) {
    rows.value = []
    total.value = 0
    errorText.value = caught instanceof Error ? caught.message : '变更历史加载失败'
  } finally {
    loading.value = false
  }
}

// ⚠️ **只有 `watch({immediate: true})`，不要再加 `onMounted(load)`** —— 两者同时存在
//    会**加载两次**：第一次用 A 的返回、第二次用 B 的返回，界面上留下的是 B 的内容
//    配 A 的标题（实测踩到：抽屉标题与日志对不上，且日志 id 与界面显示的不一致）。
//
// ⚠️ `watch` 而不是只在挂载时加载：抽屉会从款号 A 打开、看一眼关掉、再打开款号 B。
//    只在挂载时加载的话，第二次看到的还是 A 的历史 —— 而用户完全看不出来。
watch(
  () => [props.open, props.docType, props.docNo] as const,
  ([open]) => {
    if (open) void load()
  },
  { immediate: true },
)
</script>

<template>
  <Drawer
    :open="open"
    :title="title ?? `变更历史 · ${docNo}`"
    :width="560"
    placement="right"
    @close="$emit('close')"
  >
    <Skeleton v-if="loading" active :paragraph="{ rows: 4 }" />
    <template v-else>
      <Alert v-if="errorText" type="error" :message="errorText" show-icon />
      <Empty
        v-else-if="rows.length === 0"
        :description="`${docNo} 还没有变更记录`"
        :image="Empty.PRESENTED_IMAGE_SIMPLE"
      />
      <ul v-else class="history-list">
        <li v-for="row in rows" :key="row.id" class="history-item">
          <Space direction="vertical" :size="2" style="width: 100%">
            <Space>
              <Tag :color="row.action === 'DELETE' ? 'error' : 'blue'">
                {{ actionLabel(row.action) }}
              </Tag>
              <span class="history-operator">{{ row.operator_name }}</span>
              <span class="history-time">{{ formatDateTime(row.created_at) }}</span>
            </Space>
            <!--
              `from_status → to_status` 只在**状态迁移**类动作下有值；新建 / 修改 /
              删除没有状态可言，所以条件渲染而不是显示「→」。
            -->
            <Typography.Text v-if="row.from_status || row.to_status" class="history-status">
              {{ row.from_status ?? '—' }} → {{ row.to_status ?? '—' }}
            </Typography.Text>
            <Typography.Text v-if="row.reason" class="history-reason">
              原因：{{ row.reason }}
            </Typography.Text>
          </Space>
        </li>
      </ul>
      <p v-if="total > rows.length" class="history-more">
        共 {{ total }} 条，当前显示最近 {{ rows.length }} 条
      </p>
    </template>
  </Drawer>
</template>

<style scoped>
.history-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.history-item {
  padding: var(--space-3) 0;
  border-bottom: 1px solid var(--color-border);
}

.history-operator {
  font-size: var(--font-size-base);
  color: var(--color-text);
}

.history-time,
.history-more,
.history-status {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.history-reason {
  font-size: var(--font-size-base);
  color: var(--color-text-second);
}

.history-more {
  margin-top: var(--space-3);
}
</style>
