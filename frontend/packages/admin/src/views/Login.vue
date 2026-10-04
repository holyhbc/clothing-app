<script setup lang="ts">
/**
 * 登录页。
 *
 * `defineOptions` 不是为了好看：任务卡规定的文件名（`Login.vue` / `Error403.vue` /
 * `Combo.vue` …）是单词，而 eslint 的 `vue/multi-word-component-names` 要求组件名
 * 至少两个词。文件名由规范定死、组件名由 lint 定死，两者只能用 `defineOptions` 调和 ——
 * 改文件名会同时偏离任务卡和 docs/03 §2.2 的目录约定。
 */
defineOptions({ name: 'LoginView' })
/**
 * 登录页（docs/06 §5「业务错误：说清楚为什么 + 下一步」）。
 *
 * ⚠️ **不区分「账号不存在」与「口令错误」**：后端返回码能分出来，但文案里分出来
 * 就成了账号枚举工具 —— 攻击者能用它批量试出哪些工号存在。这是安全口径，不是文案偏好。
 *
 * ⚠️ **网络错误必须给「重试」**：只显示一句"网络异常"的话，用户唯一的出路是刷新
 * 整页（丢掉已输入的工号），现场断网时这个体验很差。
 */
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Alert, Button, Card, Form, Input, Space, Typography } from 'ant-design-vue'
import { ApiError } from '@garment/shared'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const employeeNo = ref('')
const password = ref('')
const submitting = ref(false)
/** 认证失败的文案：docs/06 §5 要求"说清下一步"。 */
const authErrorText = ref<string | null>(null)
/** 网络类失败：文案不同，而且**必须给重试** —— 用户不该只能刷新整页。 */
const networkErrorText = ref<string | null>(null)

async function onSubmit(): Promise<void> {
  if (submitting.value) return
  submitting.value = true
  authErrorText.value = null
  networkErrorText.value = null
  try {
    await auth.login(employeeNo.value.trim(), password.value)
    // redirect 只接受站内相对路径：直接 `router.push` 一个外部地址是开放重定向
    const redirect = route.query['redirect']
    if (typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')) {
      await router.replace(redirect)
    } else {
      await router.replace({ name: 'home' })
    }
  } catch (error) {
    // ⚠️ **不区分"账号不存在"与"口令错误"**（TC-W17）：后端返回码里能分出来，
    //    但文案里分出来就成了账号枚举工具 —— 攻击者能用它批量试出哪些工号存在。
    //    统一一句话，让用户自己去核对工号。
    // ⚠️ 判 **HTTP 401** 而不是错误码：后端 `PASSWORD_INCORRECT(11003)` 映射到
    //   401，而 `ApiError.isAuthFailure` 只覆盖 11001/11002/11004（登录态失效那组）。
    //   早先按 `isAuthFailure` 判的结果是"口令错"永远走不到这个分支，
    //   用户看到的是「暂时无法登录，请稍后重试」—— 于是反复重试正确口令。
    //
    // ⚠️ 403（`ACCOUNT_DISABLED` 账号停用）**故意也用同一句话**：分开说就成了
    //   账号枚举工具（攻击者能据此试出哪些工号存在）。代价是被停用的用户会来问人，
    //   那是可接受的成本，安全边界不能为了体验让步。
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      authErrorText.value = '账号或口令不正确，请核对后重试'
    } else if (error instanceof ApiError && error.status === 0) {
      // 网络/非预期内容（`status === 0` 是 client 对非 JSON 响应的标记）
      networkErrorText.value = '网络异常，请检查后重试'
    } else {
      networkErrorText.value = '暂时无法登录，请稍后重试'
    }
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

        <Alert v-if="authErrorText" type="error" :message="authErrorText" show-icon />
        <Alert v-if="networkErrorText" type="error" :message="networkErrorText" show-icon>
          <template #action>
            <a size="small" @click="onSubmit">重试</a>
          </template>
        </Alert>

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
