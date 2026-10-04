<script setup lang="ts">
/**
 * 新建 / 编辑用户（`/system/users` 的三件套之 Form）。
 *
 * 一个页面同时承担新建与编辑，判据是路由有没有 `:id`：
 * - 无 `:id` → 新建（要填工号与初始口令）
 * - 有 `:id` → 编辑（**工号与口令都不在这里改**：工号是登录凭据且被审计日志引用，
 *   改口令要走"重置口令"流程 —— 那一步要填原因并吊销全部 refresh token）
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert,
  Button,
  Card,
  Form,
  FormItem,
  Input,
  message,
  Select,
  Space,
  Typography,
} from 'ant-design-vue'
import { PERM } from '@garment/shared'
import type { RoleOut, UserOut } from '@garment/shared'
import { assignUserRoles, createUser, getUser, listRoles, patchUser } from '@/api/system'

defineOptions({ name: 'SystemUserForm' })

const route = useRoute()
const router = useRouter()

const userId = computed(() => (typeof route.params['id'] === 'string' ? route.params['id'] : null))
const isEdit = computed(() => userId.value !== null)

const loading = ref(false)
const saving = ref(false)
const loadError = ref<string | null>(null)
const submitError = ref<string | null>(null)

const current = ref<UserOut | null>(null)
const roles = ref<RoleOut[]>([])

const form = ref({
  employee_no: '',
  name: '',
  password: '',
  data_scope: 'SELF' as UserOut['data_scope'],
  role_codes: [] as string[],
  must_change_password: true,
})

const scopeOptions = [
  { value: 'SELF', label: '仅本人' },
  { value: 'GROUP', label: '本组' },
  { value: 'WORKSHOP', label: '本车间及授权车间' },
  { value: 'FACTORY', label: '全厂' },
]

const roleOptions = computed(() =>
  roles.value.map((role) => ({ value: role.code, label: role.name })),
)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    // ⚠️ 角色列表要 `system:role:manage` 才能读全量，但**新建用户**只要
    //    `system:user:manage`。所以角色拉不到时不阻断建号 —— 只提示"只能选到部分角色"。
    roles.value = await listRoles()
  } catch {
    roles.value = []
  }
  if (userId.value !== null) {
    try {
      const user = await getUser(userId.value)
      current.value = user
      form.value = {
        employee_no: user.employee_no,
        name: user.name,
        password: '',
        data_scope: user.data_scope,
        role_codes: user.role_codes ?? [],
        must_change_password: user.must_change_password,
      }
    } catch {
      loadError.value = '加载用户失败，请返回列表重试'
    }
  }
  loading.value = false
}

async function onSubmit(): Promise<void> {
  if (saving.value) return
  saving.value = true
  submitError.value = null
  try {
    if (userId.value === null) {
      await createUser({
        employee_no: form.value.employee_no.trim(),
        name: form.value.name.trim(),
        password: form.value.password,
        data_scope: form.value.data_scope,
        role_codes: form.value.role_codes,
        must_change_password: true,
      })
      void message.success('已创建账号，该用户首次登录需修改口令')
    } else {
      // 角色走**整体替换**端点：`assignUserRoles` 与 `patchUser` 是两次请求，
      // 各自独立事务。⚠️ 所以角色可能已经换成功而基本信息失败 —— 界面提示要说清。
      await patchUser(userId.value, {
        name: form.value.name.trim(),
        data_scope: form.value.data_scope,
        must_change_password: form.value.must_change_password,
        version: current.value?.version ?? 1,
      })
      await assignUserRoles(userId.value, {
        role_codes: form.value.role_codes,
        version: (current.value?.version ?? 1) + 1,
      })
      void message.success('已保存')
    }
    await router.replace({ name: 'system-users' })
  } catch (caught) {
    // 统一文案 + 保留输入（docs/06 §2.4「失败保留输入」）：让用户只改错的那一项
    submitError.value = caught instanceof Error ? caught.message : '保存失败，请重试'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <Card :bordered="false" class="form-card">
    <Space direction="vertical" size="middle" style="width: 100%">
      <Typography.Title :level="4" style="margin: 0">
        {{ isEdit ? `编辑用户 ${current?.name ?? ''}` : '新建用户' }}
      </Typography.Title>

      <Alert v-if="loadError" type="error" :message="loadError" show-icon />
      <Alert v-if="submitError" type="error" :message="submitError" show-icon />
      <Alert
        v-if="roles.length === 0"
        type="warning"
        show-icon
        message="没能读到角色列表"
        description="你可能没有角色管理权限。可以先保存账号，之后由管理员分配角色。"
      />

      <Form layout="vertical" style="max-width: 520px" @submit.prevent="onSubmit">
        <FormItem label="工号" required>
          <!--
            编辑态**只读**：工号是登录凭据，且被 `document_logs.doc_no` 引用。
            后端 `UserPatch` 也没有这个字段（`extra=forbid` 会 422），这里只读是前端
            提前把话说清楚，避免用户填完提交才被拒。
          -->
          <Input
            v-model:value="form.employee_no"
            placeholder="跨车间唯一，如 A001"
            :disabled="isEdit"
            allow-clear
          />
        </FormItem>

        <FormItem label="姓名" required>
          <Input v-model:value="form.name" allow-clear />
        </FormItem>

        <FormItem v-if="!isEdit" label="初始口令" required>
          <Input.Password
            v-model:value="form.password"
            placeholder="至少 8 位，含字母与数字"
            autocomplete="new-password"
          />
        </FormItem>

        <FormItem v-if="!isEdit" label="数据范围" required>
          <!--
            数据范围是**用户属性**，不从角色合并（后端 `users.data_scope`）。
            角色只贡献权限点。
          -->
          <Select v-model:value="form.data_scope" :options="scopeOptions" />
        </FormItem>

        <FormItem label="角色">
          <Select
            v-model:value="form.role_codes"
            mode="multiple"
            :options="roleOptions"
            placeholder="不选则该账号没有任何权限"
          />
        </FormItem>

        <Space>
          <Button
            v-can="PERM.SYSTEM_USER_MANAGE"
            type="primary"
            :loading="saving"
            @click="onSubmit"
          >
            保存
          </Button>
          <Button @click="router.back()">取消</Button>
        </Space>
      </Form>
    </Space>
  </Card>
</template>

<style scoped>
.form-card {
  box-shadow: var(--shadow-card);
}
</style>
