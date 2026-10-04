<script setup lang="ts">
/**
 * 空态（docs/06 §2.2「空状态用 `Empty`，文案给下一步动作」、
 * docs/06 §5「空数据 → `Empty` + 下一步动作」）。
 *
 * ## 四态里的第二态，另三态分别在哪
 *
 * | 态 | 谁负责 |
 * | --- | --- |
 * | 加载 | 页面自己（Skeleton / Spin） |
 * | **空** | 本组件 |
 * | 错误（带重试） | 页面自己：`ApiError` catch 里渲染 `EmptyState` + 「重试」 |
 * | 无权限 | 路由守卫送 `/403` |
 *
 * ⚠️ **"没有数据" 和 "没有筛选结果" 是两件事，文案不同**：前者要引导去新建，
 *    后者要引导去清筛选。把两者混成"暂无数据"的话，用户建了一堆数据却找不到，
 *    会以为系统坏了。
 */
/**
 * ⚠️ 组件用 `import { X } from 'ant-design-vue'` + `<X>` 的写法，**不用 `<a-button>`**
 *    这种 kebab 全局标签 —— `main.ts` 里并没有 `app.use(Antd)`（全局注册整包会把
 *    整个 antd 打进首屏），所以 `<a-xxx>` 会被 Vue 当成未知自定义元素渲染：
 *    **标签出现在页面上，但没有任何行为**（点击不触发、样式全无）。
 *    这种缺陷编译不报错、lint 不报错，只有真点一下才发现。
 */
import { Button, Empty } from 'ant-design-vue'

defineOptions({ name: 'EmptyState' })

withDefaults(
  defineProps<{
    /** 主体文案，如"还没有款号"。 */
    title: string
    /** 下一步动作的说明，如"点右上角「新建」录入第一个款号"。 */
    hint?: string
    /** 主操作文案；给了就渲染按钮。 */
    actionText?: string
    /** 辅助操作文案（如"清空筛选条件"），最多一个。 */
    secondaryActionText?: string
  }>(),
  { hint: '', actionText: '', secondaryActionText: '' },
)

const emit = defineEmits<{ action: []; secondaryAction: [] }>()
</script>

<template>
  <div class="empty-state">
    <!--
      ⚠️ **不要**写 `:description="null"`。antd 的 Empty 内部是
      `const { description = slots.description?.() || undefined } = props`
      —— prop 与插槽同时存在时，`null` 会让整段描述渲染成一个空注释节点，
      也就是 title 与 hint **全部消失**。只给插槽就够了。

      （注：这段注释里刻意不写那个空注释的字面量 —— 它自带的 `-->` 会**提前结束**
    本注释，vue/no-parsing-error 会报 nested-comment。）-->
    <Empty>
      <template #description>
        <p class="empty-title">{{ title }}</p>
        <p v-if="hint !== ''" class="empty-hint">{{ hint }}</p>
      </template>
      <div class="empty-actions">
        <Button v-if="actionText !== ''" type="primary" @click="emit('action')">
          {{ actionText }}
        </Button>
        <Button v-if="secondaryActionText !== ''" @click="emit('secondaryAction')">
          {{ secondaryActionText }}
        </Button>
      </div>
    </Empty>
  </div>
</template>

<style scoped>
.empty-state {
  padding: var(--space-8) var(--space-4);
}

.empty-title {
  margin: 0;
  font-size: var(--font-size-lg);
  color: var(--color-text-second);
}

.empty-hint {
  margin: var(--space-1) 0 0;
  font-size: var(--font-size-base);
  /* 三级文字色：比标题弱一档，不与标题抢注意力 */
  color: var(--color-text-third);
}

.empty-actions {
  display: flex;
  gap: var(--space-2);
  justify-content: center;
  margin-top: var(--space-4);
}
</style>
