<script setup lang="ts">
/**
 * 改密页 —— `must_change_password` 的唯一出口。
 *
 * 守卫把这类用户钉在这里，所以这个页面**必须真的能用**：口令强度与后端策略一致
 * （docs/09 §），否则用户会在这里撞墙而不知道规则是什么。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Alert, Button, Card, Form, Input, Space, Typography } from 'ant-design-vue'
import { changePassword } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const submitting = ref(false)
const errorText = ref<string | null>(null)

const mismatched = computed(
  () => confirmPassword.value.length > 0 && newPassword.value !== confirmPassword.value,
)

async function onSubmit(): Promise<void> {
  if (submitting.value || mismatched.value) return
  submitting.value = true
  errorText.value = null
  try {
    await changePassword({ old_password: oldPassword.value, new_password: newPassword.value })
    // 改密后必须重新拉一次 me：must_change_password 只有服务端知道是否已解除，
    // 不刷新的话守卫会立刻把人又钉回这个页面。
    await auth.restore()
    await router.replace({ name: 'home' })
  } catch {
    errorText.value = '原口令不正确，或新口令不满足强度要求，请核对后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="password-page">
    <Card :bordered="false" class="password-card">
      <Space direction="vertical" size="large" style="width: 100%">
        <div>
          <Typography.Title :level="4" style="margin: 0"> 修改口令 </Typography.Title>
          <Typography.Text type="secondary"> 首次登录需修改口令后才能使用系统 </Typography.Text>
        </div>

        <Alert v-if="errorText" type="error" :message="errorText" show-icon />
        <Alert
          v-else-if="auth.mustChangePassword"
          type="warning"
          message="当前账号被要求强制修改口令"
          show-icon
        />

        <Form layout="vertical" @submit.prevent="onSubmit">
          <Form.Item label="原口令" required>
            <Input.Password v-model:value="oldPassword" autocomplete="current-password" />
          </Form.Item>
          <Form.Item label="新口令" required>
            <Input.Password v-model:value="newPassword" autocomplete="new-password" />
          </Form.Item>
          <Form.Item
            label="确认新口令"
            required
            :validate-status="mismatched ? 'error' : ''"
            :help="mismatched ? '两次输入的新口令不一致' : ''"
          >
            <Input.Password v-model:value="confirmPassword" autocomplete="new-password" />
          </Form.Item>
          <Button
            type="primary"
            block
            :loading="submitting"
            :disabled="mismatched"
            @click="onSubmit"
          >
            提交
          </Button>
        </Form>
      </Space>
    </Card>
  </div>
</template>

<style scoped>
.password-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
  padding: var(--space-6);
}

.password-card {
  width: 100%;
  max-width: 380px;
  box-shadow: var(--shadow-card);
}
</style>
