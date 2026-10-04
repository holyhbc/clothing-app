<script setup lang="ts">
/**
 * 页面容器（docs/06 §2.2 的 `PageHeader` + `FilterCard` + `TableCard` 结构）。
 *
 * ```
 * PageHeader: 标题 | [次要操作] [主操作 primary]
 * FilterCard: 查询条件（默认 3 个，"更多条件"可展开）
 *   <slot name="filter">
 * TableCard:
 *   <slot name="toolbar">   ← TableToolbar
 *   <slot />                ← 表格
 * ```
 *
 * ## 「主操作 1 个」这条规则为什么要靠组件约束
 *
 * docs/06 §2.1 写的是「主操作 1 个（`primary`），其余 `default`；危险操作放 `...`
 * 下拉并二次确认」。写成文档靠自觉，但一个页面放三个 `primary` 按钮时，**没有任何人
 * 会注意到** —— 直到用户在满屏按钮里找不到"现在该点哪个"。
 * 所以这里把 primary 收进一个具名插槽：结构上就只能有一个。
 */
import { computed, useSlots } from 'vue'

defineOptions({ name: 'PageLayout' })

withDefaults(
  defineProps<{
    /** 页面标题，同时作为面包屑末级（`meta.title` 是标题的唯一来源）。 */
    title: string
    /** 次要说明，放在标题下方（如"共 128 条，本月已审核 42 条"）。 */
    description?: string
    /** 筛选区默认展开。列多的页面折起来，列少的别折。 */
    filterCollapsed?: boolean
  }>(),
  { description: '', filterCollapsed: true },
)

const slots = useSlots()

const hasFilter = computed(() => slots['filter'] !== undefined)
const hasToolbar = computed(() => slots['toolbar'] !== undefined)
</script>

<template>
  <section class="page-layout">
    <header class="page-header">
      <div class="page-header-text">
        <h1 class="page-title">{{ title }}</h1>
        <p v-if="description !== ''" class="page-description">{{ description }}</p>
      </div>
      <!--
        具名插槽：结构上只允许一个 primary（docs/06 §2.1「主操作 1 个」）。
        想放第二个就必须先改这个组件 —— 那正是我们希望发生的对话。
      -->
      <div class="page-header-extra">
        <slot name="extra" />
      </div>
    </header>

    <section v-if="hasFilter" class="page-filter">
      <slot name="filter" :collapsed="filterCollapsed" />
    </section>

    <section class="page-card">
      <div v-if="hasToolbar" class="page-toolbar">
        <slot name="toolbar" />
      </div>
      <slot />
    </section>
  </section>
</template>

<style scoped>
.page-layout {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.page-header-text {
  min-width: 0;
}

/* 20px/600（docs/06 §2.1「页面标题 20px/600，与操作按钮同一行右侧」） */
.page-title {
  margin: 0;
  font-size: var(--font-size-xl);
  font-weight: 600;
  line-height: 1.4;
  color: var(--color-text);
}

.page-description {
  margin: var(--space-1) 0 0;
  font-size: var(--font-size-base);
  color: var(--color-text-second);
}

.page-header-extra {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.page-filter,
.page-card {
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-card);
}

.page-filter {
  padding: var(--space-4);
}

.page-card {
  padding: var(--space-4);
}

.page-toolbar {
  padding-bottom: var(--space-3);
  margin-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
</style>
