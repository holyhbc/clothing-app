<script setup lang="ts">
/**
 * 根组件：**只有 `<RouterView>`**（docs/03 §2.1 第 2 条，`<script setup>` 单文件组件）。
 *
 * 布局（Header 48px / Sider 200px / 内容 24px 内边距、限宽 1680px 居中，docs/06 §2.1）
 * 在 T-WEB-003 落地 —— 骨架阶段先不搭，避免这里写一套 T-WEB-003 又要推翻的壳。
 */
import { ConfigProvider } from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
</script>

<template>
  <ConfigProvider :locale="zhCN">
    <RouterView />
  </ConfigProvider>
</template>
