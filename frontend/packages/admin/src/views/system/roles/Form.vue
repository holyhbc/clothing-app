<script setup lang="ts">
/**
 * 角色表单：基础信息 + **权限点按模块分组勾选**（docs/06 §6.3）。
 *
 * ## 为什么把"改权限"与"改名称"放在同一个页面
 *
 * 后端是两个端点（`PATCH /roles/{id}` 与 `PUT /roles/{id}/permissions`），
 * 但从用户的角度这是**一件事**："配一个角色"。拆成两页会让人以为改完名称
 * 就生效了，而权限其实没动 —— 那是最容易漏的一步。
 *
 * 保存时先 PATCH 基础信息再 PUT 权限：权限那个端点会 `version + 1`，
 * 顺序反了第二个请求的乐观锁会不匹配。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  CheckboxGroup,
  Collapse,
  CollapsePanel,
  Form,
  FormItem,
  Input,
  message,
  Select,
  Space,
  Typography,
} from 'ant-design-vue'
import { PERM } from '@garment/shared'
import type { PermissionGroupOut, RoleOut } from '@garment/shared'
import {
  createRole,
  listPermissionGroups,
  listRoles,
  patchRole,
  replaceRolePermissions,
} from '@/api/system'

defineOptions({ name: 'SystemRoleForm' })

const route = useRoute()
const router = useRouter()

const roleId = computed(() => (typeof route.params['id'] === 'string' ? route.params['id'] : null))
const isEdit = computed(() => roleId.value !== null)

const current = ref<RoleOut | null>(null)
const groups = ref<PermissionGroupOut[]>([])
const loading = ref(false)
const saving = ref(false)
const loadError = ref<string | null>(null)
const submitError = ref<string | null>(null)

const form = ref({
  code: '',
  name: '',
  data_scope: 'SELF' as RoleOut['data_scope'],
  description: '',
  permission_codes: [] as string[],
})

const scopeOptions = [
  { value: 'SELF', label: '仅本人' },
  { value: 'GROUP', label: '本组' },
  { value: 'WORKSHOP', label: '本车间' },
  { value: 'FACTORY', label: '全厂' },
]

const selectedCount = computed(() => form.value.permission_codes.length)

/** 每个模块的勾选状态（`a-checkbox-group` 的 v-model 语义）。 */
const groupedValues = computed({
  get: () => form.value.permission_codes,
  set: (next: string[]) => {
    form.value.permission_codes = next
  },
})

function moduleCodes(group: PermissionGroupOut): string[] {
  return group.permissions.map((item) => item.code)
}

function moduleSelected(group: PermissionGroupOut): number {
  const codes = moduleCodes(group)
  return codes.filter((code) => form.value.permission_codes.includes(code)).length
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    groups.value = await listPermissionGroups()
  } catch (caught) {
    loadError.value = caught instanceof Error ? caught.message : '权限点加载失败'
  }
  if (roleId.value !== null) {
    try {
      const roles = await listRoles()
      const found = roles.find((role) => role.id === roleId.value)
      if (found === undefined) {
        loadError.value = '角色不存在或已被停用'
      } else {
        current.value = found
        form.value = {
          code: found.code,
          name: found.name,
          data_scope: found.data_scope,
          description: found.description ?? '',
          permission_codes: found.permission_codes ?? [],
        }
      }
    } catch (caught) {
      loadError.value = caught instanceof Error ? caught.message : '角色加载失败'
    }
  }
  loading.value = false
}

async function onSubmit(): Promise<void> {
  if (saving.value) return
  saving.value = true
  submitError.value = null
  try {
    if (roleId.value === null) {
      await createRole({
        code: form.value.code.trim(),
        name: form.value.name.trim(),
        data_scope: form.value.data_scope,
        // ⚠️ 清空说明发 `null` 而不是不传：字段是可选的，省略会被后端当成"保持原值"，
        //    于是用户清空说明框保存后说明还在 —— 看着像没保存成功。
        description: form.value.description === '' ? null : form.value.description,
        permission_codes: form.value.permission_codes,
      })
      void message.success('已创建角色')
      await router.replace({ name: 'system-roles' })
      return
    }

    const base = current.value
    if (base === null) return
    // ⚠️ 顺序：先改基础信息（version +1），再改权限。反过来第二个请求的
    //    乐观锁会对不上，用户会看到"角色已被他人修改"而什么都没改成。
    await patchRole(roleId.value, {
      name: form.value.name.trim(),
      data_scope: form.value.data_scope,
      description: form.value.description === '' ? null : form.value.description,
      version: base.version,
    })
    await replaceRolePermissions(roleId.value, {
      permission_codes: form.value.permission_codes,
      version: base.version + 1,
    })
    void message.success('已保存，权限对所有持有该角色的账号立即生效')
    await router.replace({ name: 'system-roles' })
  } catch (caught) {
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
        {{ isEdit ? `编辑角色 ${current?.name ?? ''}` : '新建角色' }}
      </Typography.Title>

      <Alert v-if="loadError" type="error" :message="loadError" show-icon />
      <Alert v-if="submitError" type="error" :message="submitError" show-icon />
      <Alert
        v-if="current?.is_system === true"
        type="info"
        show-icon
        message="这是系统内置角色"
        description="code 与停用被禁用，但权限点可以收放 —— 超管必须能收权（docs/07 §2.3）。"
      />

      <Form layout="vertical" style="max-width: 720px" @submit.prevent="onSubmit">
        <FormItem label="code" required>
          <!--
            ⚠️ 内置角色**必填只读**：它被 `user_roles` 与审计日志引用。
            自建角色也要只读 —— 后端 `RolePatch` 根本没有 `code` 字段
            （`extra="forbid"` 会 422），前端只读是提前把话说清楚。
          -->
          <Input
            v-model:value="form.code"
            placeholder="小写字母开头，如 production_leader"
            :disabled="isEdit"
            allow-clear
          />
        </FormItem>

        <FormItem label="名称" required>
          <Input v-model:value="form.name" allow-clear />
        </FormItem>

        <FormItem label="建号默认数据范围">
          <Select v-model:value="form.data_scope" :options="scopeOptions" />
        </FormItem>

        <FormItem label="职责说明">
          <Input.TextArea v-model:value="form.description" :rows="2" />
        </FormItem>

        <FormItem label="权限点（按模块分组）">
          <Space direction="vertical" size="small" style="width: 100%">
            <Typography.Text type="secondary">
              已选 {{ selectedCount }} 项。保存后对所有持有该角色的账号**立即生效**，无需重新登录。
            </Typography.Text>
            <CheckboxGroup v-model:value="groupedValues">
              <Collapse :borderless="true">
                <CollapsePanel
                  v-for="group in groups"
                  :key="group.module"
                  :header="`${group.module_name}（${moduleSelected(group)} / ${moduleCodes(group).length}）`"
                >
                  <Space direction="vertical" size="small">
                    <Checkbox v-for="item in group.permissions" :key="item.code" :value="item.code">
                      {{ item.name }}
                      <span class="permission-code">{{ item.code }}</span>
                    </Checkbox>
                  </Space>
                </CollapsePanel>
              </Collapse>
            </CheckboxGroup>
          </Space>
        </FormItem>

        <Space>
          <Button
            v-can="PERM.SYSTEM_ROLE_MANAGE"
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

.permission-code {
  margin-left: var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}
</style>
