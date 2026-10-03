<script setup lang="ts">
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
