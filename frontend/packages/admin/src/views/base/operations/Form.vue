<script setup lang="ts">
/**
 * 工序新建 / 编辑页。同 `List.vue`：实现共用 `ResourceForm.vue`。
 *
 * 一个页面同时承担新建与编辑，判据是路由有没有 `:code`（路径参数是**业务编码**，
 * 如 `/colors/{color_code}`，不是 UUID —— docs/modules/01 §4.4）。
 */
import ResourceForm from '../ResourceForm.vue'

defineOptions({ name: 'OperationsForm' })
</script>

<template>
  <ResourceForm resource-key="operations" />
</template>
