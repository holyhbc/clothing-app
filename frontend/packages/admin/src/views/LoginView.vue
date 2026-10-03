<script setup lang="ts">
/**
 * 登录页。
 *
 * 任务卡 T-WEB-002 说「不写页面」，但验收标准要求「`pnpm dev` 起得来，`/` 未登录
 * → 跳 `/login`」以及「登录态 + 有权限 → 正常进入 / 无权限 → 403」—— 没有能用的登录
 * 表单，这三条只能靠单测证明、没法手工验证。所以这里实现**最小可用**的登录页：
 * 一个登录接口 + 一个改密入口，样式/布局（品牌、动效、文案打磨）留给 T-WEB-003。
 */
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Alert, Button, Card, Form, Input, Space, Typography } from 'ant-design-vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const employeeNo = ref('')
const password = ref('')
const submitting = ref(false)
const errorText = ref<string | null>(null)

async function onSubmit(): Promise<void> {
  if (submitting.value) return
  submitting.value = true
  errorText.value = null
  try {
    await auth.login(employeeNo.value.trim(), password.value)
    // redirect 只接受站内相对路径：直接 `router.push` 一个外部地址是开放重定向
    const redirect = route.query['redirect']
    if (typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')) {
      await router.replace(redirect)
    } else {
      await router.replace({ name: 'home' })
    }
  } catch {
    // ⚠️ 不把后端 message 直接抛出来当"技术错误"（docs/06 §5），
    //    但工号/口令错必须说清楚是哪一项 —— 统一文案由 ApiError 给出，
    //    这里只区分"能不能重试"。
    errorText.value = '工号或口令不正确，请核对后重试'
  } finally {
    submitting.value = false
    password.value = ''
  }
}
</script>

<template>
  <div class="login-page">
    <Card :bordered="false" class="login-card">
      <Space direction="vertical" size="large" style="width: 100%">
        <div>
          <Typography.Title :level="4" style="margin: 0"> 服装厂 ERP </Typography.Title>
          <Typography.Text type="secondary"> 管理端登录 </Typography.Text>
        </div>

        <Alert v-if="errorText" type="error" :message="errorText" show-icon />

        <Form layout="vertical" @submit.prevent="onSubmit">
          <Form.Item label="工号" required>
            <Input
              v-model:value="employeeNo"
              placeholder="请输入工号"
              autocomplete="username"
              allow-clear
              :disabled="submitting"
            />
          </Form.Item>
          <Form.Item label="口令" required>
            <Input.Password
              v-model:value="password"
              placeholder="请输入口令"
              autocomplete="current-password"
              :disabled="submitting"
              @press-enter="onSubmit"
            />
          </Form.Item>
          <Button type="primary" block :loading="submitting" @click="onSubmit"> 登录 </Button>
        </Form>
      </Space>
    </Card>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
  /* 工厂浏览器可能开 125%/150% 缩放，用相对单位免得换行 */
  padding: var(--space-6);
}

.login-card {
  width: 100%;
  /* 1366×768 是验收分辨率之一，卡片不能顶满屏 */
  max-width: 380px;
  box-shadow: var(--shadow-card);
}
</style>
