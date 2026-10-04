<script setup lang="ts">
/**
 * 角色列表页（docs/06 §2.2 三段结构）。
 *
 * ## 内置角色在这一页的特殊之处（docs/07 §2.3）
 *
 * | 能力 | 内置角色 | 自建角色 |
 * | --- | --- | --- |
 * | 查看 / 改名字与说明 | ✅ | ✅ |
 * | **改 `code`** | ❌ 只读 | ❌（同样不可改） |
 * | **改权限点** | ✅（超管必须能收权） | ✅ |
 * | **停用** | ❌ 按钮**不出现** | ✅（进 `…` 菜单，且必填原因） |
 *
 * ⚠️ "停用按钮不出现"而不是"按钮 disabled"：一个永远点不动还占着位置的按钮，
 * 用户会反复点然后以为是系统坏了。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Dropdown, Input, Menu, MenuItem, Space, Spin, Table, Tag } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { PERM } from '@garment/shared'
import type { RoleOut } from '@garment/shared'
import { disableRole, listRoles } from '@/api/system'
import { confirmDanger } from '@/utils/danger'
import EmptyState from '@/components/EmptyState.vue'
import PageLayout from '@/components/PageLayout.vue'

defineOptions({ name: 'SystemRoleList' })

const router = useRouter()

const rows = ref<RoleOut[]>([])
const loading = ref(false)
const failed = ref<string | null>(null)
const keyword = ref('')

const scopeText: Record<string, string> = {
  SELF: '仅本人',
  GROUP: '本组',
  WORKSHOP: '本车间',
  FACTORY: '全厂',
}

const columns: ColumnsType<RoleOut> = [
  { key: 'code', title: 'code', width: 200 },
  { key: 'name', title: '名称', width: 180 },
  { key: 'permission_codes', title: '权限点', width: 140 },
  { key: 'user_count', title: '已授予用户', width: 120 },
  { key: 'data_scope', title: '建号默认范围', width: 140 },
  { key: 'actions', title: '操作', width: 180 },
]

async function load(): Promise<void> {
  loading.value = true
  failed.value = null
  try {
    const q = keyword.value.trim()
    rows.value = await listRoles(q === '' ? undefined : q)
  } catch (caught) {
    failed.value = caught instanceof Error ? caught.message : '加载失败'
    rows.value = []
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  void load()
}

/**
 * 清空筛选。
 *
 * ⚠️ 必须是具名函数：模板表达式里写不了两条语句（`keyword = ''; onSearch()` 会被
 *    当成一个表达式解析并报 "Unexpected token"），而且两页都要这个动作。
 */
function onReset(): void {
  keyword.value = ''
  void load()
}

function editPermissions(role: RoleOut): void {
  void router.push({ name: 'system-role-permissions', params: { id: role.id } })
}

function editMeta(role: RoleOut): void {
  void router.push({ name: 'system-role-edit', params: { id: role.id } })
}

function createRole(): void {
  void router.push({ name: 'system-role-create' })
}

async function onDisable(role: RoleOut): Promise<void> {
  const result = await confirmDanger({
    title: '停用角色',
    content: `停用「${role.name}」后，已授予该角色的 ${role.user_count} 个账号会立即失去对应权限。`,
    requireReason: true,
    okText: '停用',
  })
  if (result === null) return
  await disableRole(role.id, { reason: result.reason })
  await load()
}

/** 内置角色没有"停用"这个动作，所以菜单里干脆不给这一项（docs/07 §2.3）。 */
interface RowAction {
  key: 'meta' | 'permissions' | 'disable'
  label: string
  danger: boolean
}

function rowActions(role: RoleOut): RowAction[] {
  const actions: RowAction[] = [
    { key: 'meta', label: '编辑名称与说明', danger: false },
    { key: 'permissions', label: '配置权限点', danger: false },
  ]
  if (!role.is_system) actions.push({ key: 'disable', label: '停用角色', danger: true })
  return actions
}

async function onRowAction(role: RoleOut, key: RowAction['key']): Promise<void> {
  switch (key) {
    case 'meta':
      return editMeta(role)
    case 'permissions':
      return editPermissions(role)
    case 'disable':
      return onDisable(role)
    default:
      return
  }
}

function asRole(record: Record<string, unknown>): RoleOut {
  return record as unknown as RoleOut
}

function permissionCount(role: RoleOut): number {
  return role.permission_codes?.length ?? 0
}

const isEmpty = computed(() => !loading.value && failed.value === null && rows.value.length === 0)

onMounted(() => {
  void load()
})
</script>

<template>
  <PageLayout
    title="角色权限"
    description="内置角色由系统 seed 建立：code 不可改、不可停用，但权限可以收放"
  >
    <template #extra>
      <Button v-can="PERM.SYSTEM_ROLE_MANAGE" type="primary" @click="createRole">新建角色</Button>
    </template>

    <template #filter>
      <Space wrap>
        <Input
          v-model:value="keyword"
          placeholder="code 或名称"
          style="width: 220px"
          allow-clear
          @press-enter="onSearch"
        />
        <Button type="primary" @click="onSearch">查询</Button>
        <Button @click="onReset"> 重置 </Button>
      </Space>
    </template>

    <Spin :spinning="loading">
      <Table
        :columns="columns"
        :data-source="rows"
        row-key="id"
        size="small"
        :pagination="false"
        :scroll="{ x: 960 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'code'">
            <span class="code-cell" :title="asRole(record).code">{{ asRole(record).code }}</span>
            <Tag v-if="asRole(record).is_system" color="blue">内置</Tag>
          </template>

          <template v-else-if="column.key === 'permission_codes'">
            {{ permissionCount(asRole(record)) }} 项
          </template>

          <template v-else-if="column.key === 'user_count'">
            {{ asRole(record).user_count }}
          </template>

          <template v-else-if="column.key === 'data_scope'">
            {{ scopeText[asRole(record).data_scope] ?? asRole(record).data_scope }}
          </template>

          <template v-else-if="column.key === 'actions'">
            <Space>
              <Button
                v-can="PERM.SYSTEM_ROLE_MANAGE"
                size="small"
                type="link"
                @click="editPermissions(asRole(record))"
              >
                配置权限
              </Button>
              <Dropdown :trigger="['click']">
                <Button size="small" type="link">…</Button>
                <template #overlay>
                  <Menu>
                    <MenuItem
                      v-for="action in rowActions(asRole(record))"
                      :key="action.key"
                      :danger="action.danger"
                      @click="onRowAction(asRole(record), action.key)"
                    >
                      {{ action.label }}
                    </MenuItem>
                  </Menu>
                </template>
              </Dropdown>
            </Space>
          </template>
        </template>

        <template #emptyText>
          <EmptyState
            v-if="isEmpty"
            title="还没有角色"
            hint="内置角色由系统 seed 建立；如需新角色，点右上角「新建角色」"
            action-text="新建角色"
            @action="createRole"
          />
          <EmptyState
            v-else-if="failed !== null"
            title="角色列表加载失败"
            :hint="failed"
            action-text="重试"
            @action="load"
          />
        </template>
      </Table>
    </Spin>
  </PageLayout>
</template>

<style scoped>
.code-cell {
  margin-right: var(--space-1);
}
</style>
