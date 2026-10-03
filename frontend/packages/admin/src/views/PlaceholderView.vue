<script setup lang="ts">
/**
 * 模块页占位（T-WEB-005 基础资料页替换）。
 *
 * 之所以现在就建它：路由守卫的「有权限 → 进入」与「无权限 → 403」两条分支需要
 * 一个真实的受限路由才能手工验证，光有 `/` 一条路的话，403 分支永远只存在于单测里。
 */
import { useRoute } from 'vue-router'
import { Alert, Card, Typography } from 'ant-design-vue'

const route = useRoute()
</script>

<template>
  <div class="placeholder-page">
    <Card :bordered="false" class="placeholder-card">
      <Typography.Title :level="4" style="margin: 0">
        {{ route.meta.title }}
      </Typography.Title>
      <Alert
        type="info"
        show-icon
        message="本页面在 T-WEB-005（基础资料页）落地"
        description="当前只占位，用来验证路由守卫的「有权进入 / 无权 403」两条分支。"
        style="margin-top: var(--space-4)"
      />
    </Card>
  </div>
</template>

<style scoped>
.placeholder-page {
  padding: var(--space-6);
  max-width: var(--page-max-width);
  margin: 0 auto;
  width: 100%;
}

.placeholder-card {
  box-shadow: var(--shadow-card);
}
</style>
