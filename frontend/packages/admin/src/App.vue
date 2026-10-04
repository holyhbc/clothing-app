<script setup lang="ts">
/**
 * 根组件：只有 `<RouterView>`（docs/03 §2.1 第 2 条，`<script setup>` 单文件组件）。
 * 布局在 `layouts/AdminLayout.vue`，由路由表挂上去。
 */
import { ConfigProvider } from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
</script>

<template>
  <!--
    `componentSize="small"`：PC 端**密度优先**（docs/06 §0 / §2.2「工厂用户看表格」）。
    同一屏能看到的行数直接决定查一张单据要点几页翻页。移动端/表单密集处可局部改回。
  -->
  <ConfigProvider :locale="zhCN" component-size="small">
    <RouterView />
  </ConfigProvider>
</template>
