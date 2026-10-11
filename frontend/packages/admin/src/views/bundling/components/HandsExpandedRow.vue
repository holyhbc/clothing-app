<script setup lang="ts">
/**
 * 列表「展开行」：显示某张打菲单的手数明细。
 *
 * 抽出来是因为「手数明细长什么样」与「列表怎么分页、怎么筛」毫无关系，
 * 却挤在同一个文件里 —— 两个都不大，合起来就把 `List.vue` 顶过了 400 行
 * （ADR-0030 的硬线）。
 *
 * ⚠️ 它**只渲染**，不拉数据：明细由 `List.vue` 在 `@expand` 时统一拉取并缓存，
 * 通过 `lines` 传入。所以本组件是纯展示，没有 loading 态也没有请求。
 */
import { Table } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import type { LineOut } from '@garment/shared'

defineProps<{ lines: LineOut[]; loaded: boolean }>()

const columns: ColumnsType<LineOut> = [
  { key: 'line_no', title: '行号', dataIndex: 'line_no', width: 60 },
  { key: 'size_code', title: '尺码', dataIndex: 'size_code', width: 80 },
  { key: 'hands', title: '手数', dataIndex: 'hands', width: 80, align: 'right' },
  { key: 'planned_qty', title: '计划件数', dataIndex: 'planned_qty', width: 100, align: 'right' },
  { key: 'available_qty_before', title: '可打菲量', dataIndex: 'available_qty_before', width: 100, align: 'right' },
]
</script>

<template>
  <span v-if="!loaded" class="cell-muted">加载中…</span>
  <Table
    v-else
    class="hands-table"
    :columns="columns"
    :data-source="lines"
    :row-key="(row: LineOut) => row.id"
    size="small"
    :pagination="false"
  />
</template>
