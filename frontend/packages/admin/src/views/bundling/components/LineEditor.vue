<script setup lang="ts">
/**
 * 打菲单明细编辑器（新建页）。
 *
 * 每行 = 一尺码；必带 `cutting_size_line_id`（引用裁剪的尺码明细行）。
 * 列：行号 | 尺码 | 来源裁剪明细行（Combo，显示手数/出数/每手件数/缸号匹号） | 手数 | 计划件数 | 派工组别 | 工位 | 操作
 *
 * ⚠️ `size_code` 允许同尺码多行（同尺码可来自裁剪的多个布批 / 多条尺码明细行，
 * ADR-0017 §4）；唯一键是 `(doc_id, line_no)`。
 */
import { ref } from 'vue'
import { Button, Input, InputNumber, Select, Space, Table, Tooltip } from 'ant-design-vue'
import type { LineIn } from '@garment/shared'

interface Props {
  modelValue: LineIn[]
  sourceOptions: { value: string; label: string }[]
  loadingSources: boolean
}

defineOptions({ name: 'BundlingLineEditor' })

const props = defineProps<Props>()
const emit = defineEmits<{ 'update:modelValue': [value: LineIn[]] }>()

const editingKey = ref<string | null>(null)
const _columns = [
  { key: 'line_no', title: '行号', dataIndex: 'line_no', width: 70 },
  { key: 'size_code', title: '尺码', dataIndex: 'size_code', width: 100 },
  { key: 'cutting_size_line_id', title: '来源裁剪明细行', dataIndex: 'cutting_size_line_id', width: 320 },
  { key: 'hands', title: '手数', dataIndex: 'hands', width: 100, align: 'right' },
  { key: 'planned_qty', title: '计划件数', dataIndex: 'planned_qty', width: 120, align: 'right' },
  { key: 'group_no', title: '派工组别', dataIndex: 'group_no', width: 120 },
  { key: 'workstation_no', title: '工位', dataIndex: 'workstation_no', width: 120 },
  { key: 'actions', title: '操作', width: 120, fixed: 'right' },
]

function commit(next: LineIn[]): void {
  emit('update:modelValue', next.map((line, index) => ({ ...line, line_no: index + 1 })))
}

function addLine(): void {
  const newLine: LineIn = {
    line_no: props.modelValue.length + 1,
    size_code: '',
    color_code: '',
    operation_no: '',
    cutting_size_line_id: '',
    hands: 1,
    planned_qty: null,
    group_no: undefined,
    workstation_no: undefined,
    remark: undefined,
  }
  commit([...props.modelValue, newLine])
}

function startEdit(key: string): void {
  editingKey.value = key
}

function saveEdit(_key: string): void {
  editingKey.value = null
}

function cancelEdit(_key: string): void {
  editingKey.value = null
}

function removeLine(index: number): void {
  commit(props.modelValue.filter((_, i) => i !== index))
}

function onSizeCodeChange(index: number, value: string): void {
  const next = [...props.modelValue]
  next[index] = { ...next[index], size_code: value }
  commit(next)
}

function onSourceChange(index: number, value: string | null): void {
  const next = [...props.modelValue]
  next[index] = { ...next[index], cutting_size_line_id: value ?? '' }
  commit(next)
}

function onHandsChange(index: number, value: number | null): void {
  const next = [...props.modelValue]
  next[index] = { ...next[index], hands: value ?? 1 }
  commit(next)
}

function onPlannedQtyChange(index: number, value: number | null): void {
  const next = [...props.modelValue]
  next[index] = { ...next[index], planned_qty: value ?? null }
  commit(next)
}

function onGroupNoChange(index: number, value: string): void {
  const next = [...props.modelValue]
  const updated = { ...next[index], group_no: value === '' ? null : value } as LineIn
  next[index] = updated
  commit(next)
}

function onWorkstationNoChange(index: number, value: string): void {
  const next = [...props.modelValue]
  const updated = { ...next[index], workstation_no: value === '' ? null : value } as LineIn
  next[index] = updated
  commit(next)
}

function sourceLabel(value: string): string {
  const found = props.sourceOptions.find((o) => o.value === value)
  return found?.label ?? value
}
</script>

<template>
  <div class="line-editor">
    <Table
      :columns="_columns"
      :data-source="modelValue"
      :row-key="(row: LineIn) => row.cutting_size_line_id + '-' + row.line_no"
      size="small"
      :pagination="false"
      :scroll="{ x: 1200 }"
    >
      <template #bodyCell="{ column, record, index }">
        <template v-if="column.key === 'line_no'">
          {{ index + 1 }}
        </template>

        <template v-if="column.key === 'size_code'">
          <Input
            :value="record.size_code"
            placeholder="如 XL / L / M"
            style="width: 100%"
            @update:value="(v: string) => onSizeCodeChange(index, v)"
          />
        </template>

        <template v-else-if="column.key === 'cutting_size_line_id'">
          <template v-if="editingKey === record.cutting_size_line_id">
            <Select
              :value="record.cutting_size_line_id"
              :options="props.sourceOptions"
              allow-clear
              show-search
              placeholder="选来源裁剪明细行…"
              style="width: 100%"
              @update:value="(v: unknown) => onSourceChange(index, typeof v === 'string' ? v : '')"
              @blur="() => saveEdit(record.cutting_size_line_id)"
            />
          </template>
          <template v-else>
            <Tooltip :title="sourceLabel(record.cutting_size_line_id)">
              <span class="code-cell" @click="() => startEdit(record.cutting_size_line_id)">
                {{ sourceLabel(record.cutting_size_line_id) }}
              </span>
            </Tooltip>
          </template>
        </template>

        <template v-else-if="column.key === 'hands'">
          <template v-if="editingKey === `hands-${record.line_no}`">
            <InputNumber
              :value="record.hands"
              :min="1"
              :precision="0"
              style="width: 100%"
              @update:value="(v: number | null) => onHandsChange(index, v)"
              @blur="() => saveEdit(`hands-${record.line_no}`)"
            />
          </template>
          <template v-else>
            <span @click="() => startEdit(`hands-${record.line_no}`)">{{ record.hands }}</span>
          </template>
        </template>

        <template v-else-if="column.key === 'planned_qty'">
          <template v-if="editingKey === `planned-${record.line_no}`">
            <InputNumber
              :value="record.planned_qty ?? 0"
              :min="0"
              :precision="3"
              style="width: 100%"
              @update:value="(v: number | null) => onPlannedQtyChange(index, v)"
              @blur="() => saveEdit(`planned-${record.line_no}`)"
            />
          </template>
          <template v-else>
            <span>{{ record.planned_qty ?? '—' }}</span>
          </template>
        </template>

        <template v-else-if="column.key === 'group_no'">
          <template v-if="editingKey === `group-${record.line_no}`">
            <Input
              :value="record.group_no ?? ''"
              placeholder="组别"
              style="width: 100%"
              @update:value="(v: string) => onGroupNoChange(index, v)"
              @blur="() => saveEdit(`group-${record.line_no}`)"
            />
          </template>
          <template v-else>
            <span>{{ record.group_no ?? '—' }}</span>
          </template>
        </template>

        <template v-else-if="column.key === 'workstation_no'">
          <template v-if="editingKey === `ws-${record.line_no}`">
            <Input
              :value="record.workstation_no ?? ''"
              placeholder="工位"
              style="width: 100%"
              @update:value="(v: string) => onWorkstationNoChange(index, v)"
              @blur="() => saveEdit(`ws-${record.line_no}`)"
            />
          </template>
          <template v-else>
            <span>{{ record.workstation_no ?? '—' }}</span>
          </template>
        </template>

        <template v-else-if="column.key === 'actions'">
          <Space>
            <template v-if="editingKey === null">
              <Button size="small" type="link" @click="startEdit(`hands-${record.line_no}`)">编辑</Button>
            </template>
            <template v-else-if="editingKey === `hands-${record.line_no}`">
              <Button size="small" type="primary" @click="saveEdit(`hands-${record.line_no}`)">保存</Button>
              <Button size="small" @click="cancelEdit(`hands-${record.line_no}`)">取消</Button>
            </template>
            <Button size="small" danger @click="removeLine(index)">删除</Button>
          </Space>
        </template>
      </template>

      <!-- eslint-disable vue/valid-v-slot -->
      <template #emptyText>
        <div class="empty-hint">点上方「新增行」添加第一个尺码明细</div>
      </template>
      <!-- eslint-enable vue/valid-v-slot -->
    </Table>

    <div class="editor-toolbar" style="margin-top: var(--space-3)">
      <Button type="dashed" @click="addLine">+ 新增行</Button>
      <span class="hint">
        ⚠️ 同一尺码可来自多条裁剪明细行（多布批），行号与来源行共同唯一（ADR-0017）。
        手数必须为整数（ADR-0020），计划件数由后端重算 = hands × qty_per_hand。
      </span>
    </div>
  </div>
</template>

<style scoped>
.line-editor {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
}

.empty-hint {
  padding: var(--space-4);
  text-align: center;
  color: var(--color-text-third);
  font-size: var(--font-size-sm);
}

.editor-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.code-cell {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  max-width: 300px;
  font-family: var(--font-mono);
  cursor: pointer;
}
</style>
