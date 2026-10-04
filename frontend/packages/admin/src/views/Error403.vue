<script setup lang="ts">
/**
 * 403 页。
 *
 * `defineOptions` 不是为了好看：任务卡规定的文件名（`Login.vue` / `Error403.vue` /
 * `Combo.vue` …）是单词，而 eslint 的 `vue/multi-word-component-names` 要求组件名
 * 至少两个词。文件名由规范定死、组件名由 lint 定死，两者只能用 `defineOptions` 调和 ——
 * 改文件名会同时偏离任务卡和 docs/03 §2.2 的目录约定。
 */
defineOptions({ name: 'Error403View' })
/**
 * 403 页（docs/06 §5「无权限 → 页面级空态 403」）。
 *
 * ⚠️ 文案要说清**下一步**而不是只说"没权限"：用户多半是被临时收窄了权限，
 * 直接告诉他找谁申请比自己猜要快 —— 这就是任务卡要求的「申请入口」。
 *
 * ⚠️ 这不是安全边界。真正的拦截在服务端（docs/03 §2.1 第 8 条「权限控制双层」）；
 * 即便绕过前端直接打接口，后端照样 403。
 */
import { useRouter } from 'vue-router'
import { Button, Empty, Space, Typography } from 'ant-design-vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
</script>

<template>
  <div class="forbidden-page">
    <Empty description="您没有访问此页面的权限">
      <Space direction="vertical" size="small">
        <Typography.Text type="secondary">
          当前账号（{{ auth.user?.name ?? '未知' }}）未被授予该功能。
        </Typography.Text>
        <Typography.Text type="secondary">
          请联系车间管理员或系统管理员开通后重试。
        </Typography.Text>
        <Button type="primary" @click="router.replace({ name: 'home' })"> 返回工作台 </Button>
      </Space>
    </Empty>
  </div>
</template>

<style scoped>
.forbidden-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
  padding: var(--space-6);
}
</style>
