<script setup lang="ts">
/**
 * 列表工具栏（docs/06 §2.2「工具栏：已选 N 项时的批量操作（禁用状态显式）」）。
 *
 * ## 为什么"已选 N 项"要做成组件而不是每页各写一遍
 *
 * 三个地方很容易做错，而它们的后果都是**用户误操作**：
 *  1. 没显示已选数量 → 用户不知道自己选了几条就点了批量停用；
 *  2. 未选中任何项时按钮仍可点 → 点了以后要么没反应，要么弹一个"请选择 0 项"；
 *  3. 危险批量操作没有二次确认 → 一次误点停用半个车间的人。
 *
 * ## 为什么导出按钮在这里而不是页面里
 *
 * docs/05 §9.1 要求**列表类接口必须提供导出**。导出按钮挂在工具栏上，
 * 位置统一、权限声明统一（`v-can`），不会出现"这页忘了加导出"。
 */
import { computed } from 'vue'
/**
 * ⚠️ 同 EmptyState：显式 `import { X }` + `<X>`，不用 `<a-xxx>` 全局标签
 *    （`main.ts` 没有 `app.use(Antd)`，`<a-button>` 会渲染成无行为的未知元素）。
 */
import { Button, Dropdown, Menu, MenuItem } from 'ant-design-vue'

defineOptions({ name: 'TableToolbar' })

const props = withDefaults(
  defineProps<{
    /** 已选中的行数。 */
    selectedCount: number
    /** 总行数；用于"全选当前页"提示与百分比。 */
    total?: number
    /** 导出权限点；不给就不渲染导出按钮（避免出现一个点了就 403 的按钮）。 */
    exportPermission?: string
    /** 导出中（防重复点击）。 */
    exporting?: boolean
    /** 危险批量操作：只允许**单个**且必须走二次确认。 */
    dangerActionText?: string
  }>(),
  { total: 0, exportPermission: '', exporting: false, dangerActionText: '' },
)

const emit = defineEmits<{
  clearSelection: []
  export: []
  danger: [count: number]
}>()

const hasSelection = computed(() => props.selectedCount > 0)
const shownCount = computed(() =>
  props.total > props.selectedCount
    ? `${props.selectedCount} / ${props.total}`
    : `${props.selectedCount}`,
)
</script>

<template>
  <div class="table-toolbar">
    <div class="table-toolbar-left">
      <slot name="actions" :count="selectedCount" :enabled="hasSelection" />
      <span v-if="hasSelection" class="table-toolbar-count">
        已选 <strong>{{ shownCount }}</strong> 项
      </span>
      <Button v-if="hasSelection" size="small" @click="emit('clearSelection')">清空选择</Button>
    </div>

    <div class="table-toolbar-right">
      <!--
        危险批量操作：放 `...` 下拉而不是直显（docs/06 §2.2「破坏性操作不用红色文字
        按钮，避免误点」）。是否真危险由 `utils/danger.ts` 决定 —— 它要求**必填原因**。
      -->
      <Dropdown v-if="dangerActionText !== ''" :trigger="['click']">
        <Button size="small">…</Button>
        <template #overlay>
          <Menu>
            <MenuItem :disabled="!hasSelection" @click="emit('danger', selectedCount)">
              {{ dangerActionText }}
            </MenuItem>
          </Menu>
        </template>
      </Dropdown>

      <Button
        v-if="exportPermission !== ''"
        v-can="exportPermission"
        size="small"
        :loading="props.exporting"
        @click="emit('export')"
      >
        导出
      </Button>
    </div>
  </div>
</template>

<style scoped>
.table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.table-toolbar-left,
.table-toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

/* 选中数量是**粗体**：批量操作是不可逆的，数量的显眼程度要和它的风险相称 */
.table-toolbar-count {
  font-size: var(--font-size-base);
  color: var(--color-text-second);
}
</style>
