<script setup lang="ts">
/**
 * 404 页。
 *
 * `defineOptions` 不是为了好看：任务卡规定的文件名（`Login.vue` / `Error403.vue` /
 * `Combo.vue` …）是单词，而 eslint 的 `vue/multi-word-component-names` 要求组件名
 * 至少两个词。文件名由规范定死、组件名由 lint 定死，两者只能用 `defineOptions` 调和 ——
 * 改文件名会同时偏离任务卡和 docs/03 §2.2 的目录约定。
 */
defineOptions({ name: 'Error404View' })
import { Button, Result } from 'ant-design-vue'
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <Result status="404" title="404" sub-title="页面不存在或已调整，请从工作台重新进入">
    <template #extra>
      <Button type="primary" @click="router.replace({ name: 'home' })"> 返回工作台 </Button>
    </template>
  </Result>
</template>
