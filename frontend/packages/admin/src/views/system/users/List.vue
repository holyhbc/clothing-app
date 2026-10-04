<script setup lang="ts">
/**
 * 用户列表页（docs/06 §2.2 的 `PageHeader` / `FilterCard` / `TableCard` 三段结构）。
 *
 * ## ⚠️ 这里刻意**没有**任何 `password_hash` 的处理代码
 *
 * TC-W23 要求"响应含 `password_hash` 时前端也不渲染"。本卡的做法是**模型层就没有
 * 这一列**（后端 `UserOut` 不含它），所以前端根本拿不到 —— 不需要"记得别渲染"。
 * 如果哪天有人在 `UserOut` 里加了这个字段，本页会**自动开始渲染它**（表格列是
 * 显式声明的，加了字段不会凭空多一列），但导出/复制等场景可能带出去。
 * `tests/modules/test_system_router.py::test_user_list_never_exposes_password_hash_or_phone`
 * 是这条的守门人。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Button,
  Dropdown,
  Input,
  Menu,
  MenuItem,
  Select,
  Space,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue'
// `ColumnsType` 没有从包根导出（只在 `ant-design-vue/es/table` 里），
// 从深路径导入类型是 antd-vue 4 的既有事实，不是绕过检查。
import type { ColumnsType } from 'ant-design-vue/es/table'
import { PERM, ROLE_NAMES } from '@garment/shared'
import type { RoleCode, UserOut } from '@garment/shared'
import { disableUser, enableUser, listUsers } from '@/api/system'
import { confirmDanger } from '@/utils/danger'
import { usePageList } from '@/composables/usePageList'
import EmptyState from '@/components/EmptyState.vue'
import PageLayout from '@/components/PageLayout.vue'
import TableToolbar from '@/components/TableToolbar.vue'

/**
 * 角色 code → 中文名。
 *
 * ⚠️ `ROLE_NAMES` 只覆盖**内置**角色；自建角色的名字要从 `GET /system/roles` 拿。
 * 查不到时回退成 code 本身 —— 显示空白的话用户根本看不出这人被授了什么权。
 */
function roleName(code: string): string {
  return ROLE_NAMES[code as RoleCode] ?? code
}

/**
 * 把表格插槽里的 `record` 收成 `UserOut`。
 *
 * ⚠️ 这是**整个 admin 里唯一一处**对后端数据的类型断言，而且是 antd 逼出来的：
 * 它把 Table 的泛型在 props 上写死成 `any`，插槽的 `record` 就是
 * `Record<string, any>` —— 不收口的话模板里 `record.role_codes` 全无类型检查，
 * 字段名打错也不会报红。
 *
 * 收口之后，模板里任何一处字段名笔误都会在 `vue-tsc` 阶段暴露。
 */
function asUser(record: Record<string, unknown>): UserOut {
  return record as unknown as UserOut
}

/**
 * 已授予的角色 code 列表。
 *
 * ⚠️ 生成类型里 `role_codes` 是**可选**的（后端未授权时省略该字段），
 *    `noUncheckedIndexedAccess` 之外还有可选属性，所以模板里直接
 *    `record.role_codes.length` 会报"可能为 undefined"。统一在这里兜成空数组。
 */
function roleCodesOf(record: Record<string, unknown>): string[] {
  return asUser(record).role_codes ?? []
}

defineOptions({ name: 'SystemUserList' })

const router = useRouter()

interface UserQuery extends Record<string, string | number | boolean | null | undefined> {
  q?: string
  is_active?: boolean
  page: number
  size: number
}

const list = usePageList<UserQuery, UserOut>({
  fetch: listUsers,
  initialQuery: { page: 1, size: 20 },
})

const keyword = ref('')
const activeFilter = ref<'all' | 'active' | 'inactive'>('all')

/**
 * 列定义。标注 `ColumnsType<UserOut>` 让 `key` / `title` 这些受检查，
 * 写错列名会报红。
 *
 * ⚠️ 但它**不能**让插槽里的 `record` 带上类型 —— antd 把 Table 的 columns 泛型
 * 在组件 props 里写死成 `any`（`columns: ColumnsType<any>`），插槽跟着就是
 * `Record<string, any>`。所以模板里统一走 `asUser(record)` 收口（见那个函数）。
 */
const columns: ColumnsType<UserOut> = [
  { key: 'employee_no', title: '工号', width: 120 },
  // ⚠️ 「姓名」必须给 `dataIndex`：只有 `key` 没有 `dataIndex` 时 antd 不知道取哪个
  //    字段，单元格会渲染成**空白**。症状是列表里一列没内容，而页面上没有任何报错 ——
  //    是被测试抓出来的，不是 review 看出来的。
  { key: 'name', title: '姓名', width: 140, dataIndex: 'name' },
  { key: 'role_codes', title: '角色', width: 260 },
  { key: 'data_scope', title: '数据范围', width: 160 },
  { key: 'is_active', title: '状态', width: 140 },
  { key: 'actions', title: '操作', width: 200 },
]

const scopeText: Record<string, string> = {
  SELF: '仅本人',
  GROUP: '本组',
  WORKSHOP: '本车间',
  FACTORY: '全厂',
}

const selectedCount = ref(0)

function onSearch(): void {
  const q = keyword.value.trim()
  list.applyFilter({ q: q === '' ? undefined : q })
}

/**
 * 清空筛选。
 *
 * ⚠️ 必须是具名函数：模板表达式里写不了两条语句（`keyword = ''; onSearch()` 会被
 *    当成一个表达式解析，报 "Unexpected token"），而且两页都要这个动作。
 */
function onReset(): void {
  keyword.value = ''
  activeFilter.value = 'all'
  onSearch()
}

/**
 * 状态筛选。
 *
 * ⚠️ antd 的 `Select` `@change` 传的是 `SelectValue`（`string | number | …`），
 *    模板里不能写类型标注，所以这里收 `unknown` 再收窄 —— 直接按
 *    `'all' | 'active' | 'inactive'` 声明参数会报 TS2322。
 */
function onActiveChange(raw: unknown): void {
  const value = raw === 'active' || raw === 'inactive' ? raw : 'all'
  activeFilter.value = value
  list.applyFilter({ is_active: value === 'all' ? undefined : value === 'active' })
}

function edit(user: UserOut): void {
  void router.push({ name: 'system-user-edit', params: { id: user.id } })
}

function create(): void {
  void router.push({ name: 'system-user-create' })
}

async function onDisable(user: UserOut): Promise<void> {
  const result = await confirmDanger({
    title: '停用用户',
    // ⚠️ 后果必须写清：用户是照着这句话决定要不要点确定的
    content: `停用后 ${user.name}（${user.employee_no}）立即无法登录，进行中的单据会留在原状态。`,
    requireReason: true,
    okText: '停用',
  })
  if (result === null) return
  await disableUser(user.id, { reason: result.reason })
  await list.reload()
}

async function onEnable(user: UserOut): Promise<void> {
  const result = await confirmDanger({
    title: '启用用户',
    content: `启用后 ${user.name}（${user.employee_no}）可以重新登录。`,
    requireReason: true,
    okText: '启用',
  })
  if (result === null) return
  await enableUser(user.id, { reason: result.reason })
  await list.reload()
}

/**
 * 重置口令：先去确认框说清后果，再跳到设置页填新口令。
 *
 * ⚠️ 新口令由**管理员填写** —— 后端不生成随机口令（`auth/sms/send-code` 是 P2
 * 未启用，没有送达通道，随机口令没法告诉用户）。
 */
async function onResetPassword(user: UserOut): Promise<void> {
  const result = await confirmDanger({
    title: '重置口令',
    content: `重置后 ${user.name}（${user.employee_no}）的当前口令立即失效，需用新口令重新登录并再次改密。`,
    requireReason: true,
    okText: '去设置新口令',
  })
  if (result === null) return
  await router.push({ name: 'system-user-password', params: { id: user.id } })
}

interface RowAction {
  key: 'edit' | 'reset' | 'disable' | 'enable'
  label: string
  /** 破坏性操作 —— 菜单项标红，且必须走 `confirmDanger`。 */
  danger: boolean
}

function rowActions(user: UserOut): RowAction[] {
  return [
    { key: 'edit', label: '编辑', danger: false },
    { key: 'reset', label: '重置口令', danger: false },
    // 停用/启用都是敏感动作，但只有停用是破坏性的
    user.is_active
      ? { key: 'disable', label: '停用', danger: true }
      : { key: 'enable', label: '启用', danger: false },
  ]
}

async function onRowAction(user: UserOut, key: RowAction['key']): Promise<void> {
  switch (key) {
    case 'edit':
      return edit(user)
    case 'reset':
      return onResetPassword(user)
    case 'disable':
      return onDisable(user)
    case 'enable':
      return onEnable(user)
    default:
      return
  }
}

/**
 * 表格翻页 / 改每页条数。
 *
 * ⚠️ 必须是**具名函数**：Vue 模板表达式里不能写 TS 类型标注
 * （`(page: {current?: number}) => …` 直接报 TS1109），而且内联箭头每次
 * re-render 都新建一个函数。
 */
function onTableChange(paginationInfo: { current?: number; pageSize?: number }): void {
  const size = paginationInfo.pageSize
  if (size !== undefined && size !== list.query.size) {
    list.changeSize(size)
    return
  }
  if (paginationInfo.current !== undefined) list.changePage(paginationInfo.current)
}

const pagination = computed(() => ({
  total: list.total,
  current: list.query.page,
  pageSize: list.query.size,
  showTotal: (value: number) => `共 ${value} 个账号`,
}))

onMounted(() => {
  void list.reload()
})
</script>

<template>
  <PageLayout title="用户管理" :description="`共 ${list.total} 个账号`">
    <template #extra>
      <Button v-can="PERM.SYSTEM_USER_MANAGE" type="primary" @click="create">新建用户</Button>
    </template>

    <template #filter="{ collapsed }">
      <Space direction="vertical" size="small" style="width: 100%">
        <Space wrap>
          <Input
            v-model:value="keyword"
            placeholder="工号或姓名"
            style="width: 200px"
            allow-clear
            @press-enter="onSearch"
          />
          <Select
            :value="activeFilter"
            style="width: 140px"
            :options="[
              { value: 'all', label: '全部状态' },
              { value: 'active', label: '启用中' },
              { value: 'inactive', label: '已停用' },
            ]"
            @change="onActiveChange"
          />
          <Button type="primary" @click="onSearch">查询</Button>
          <Button @click="onReset"> 重置 </Button>
        </Space>
        <span v-if="collapsed === false" class="filter-hint">更多条件（车间 / 组别）按需再加</span>
      </Space>
    </template>

    <template #toolbar>
      <TableToolbar
        :selected-count="selectedCount"
        :total="list.total"
        :exporting="false"
        export-permission=""
      >
        <template #actions="{ count }">
          <span v-if="count > 0" class="toolbar-hint">批量操作将在后续卡片提供</span>
        </template>
      </TableToolbar>
    </template>

    <Spin :spinning="list.loading">
      <Table
        :columns="columns"
        :data-source="list.items"
        row-key="id"
        size="small"
        :pagination="pagination"
        :scroll="{ x: 1000 }"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'employee_no'">
            <!-- 工号列不换行 + 等宽（docs/06 §2.2），过长时 title 给全 -->
            <span class="code-cell" :title="asUser(record).employee_no">
              {{ asUser(record).employee_no }}
            </span>
          </template>

          <template v-else-if="column.key === 'role_codes'">
            <Space wrap>
              <Tag v-for="code in roleCodesOf(record)" :key="code">{{ roleName(code) }}</Tag>
              <span v-if="roleCodesOf(record).length === 0">未分配</span>
            </Space>
          </template>

          <template v-else-if="column.key === 'data_scope'">
            {{ scopeText[asUser(record).data_scope] ?? asUser(record).data_scope }}
          </template>

          <template v-else-if="column.key === 'is_active'">
            <!--
              停用不是单据状态，所以**不能**用 StatusTag（它的映射固定为
              docs/06 §1 的五档单据状态）。这里用一个 Tag，色走 token。
            -->
            <Tag :color="asUser(record).is_active ? 'success' : 'default'">
              {{ asUser(record).is_active ? '启用中' : '已停用' }}
            </Tag>
            <Tag v-if="asUser(record).must_change_password" color="warning">待改密</Tag>
          </template>

          <template v-else-if="column.key === 'actions'">
            <Space>
              <Button
                v-can="PERM.SYSTEM_USER_MANAGE"
                size="small"
                type="link"
                @click="edit(asUser(record))"
              >
                编辑
              </Button>
              <Dropdown :trigger="['click']">
                <Button size="small" type="link">…</Button>
                <template #overlay>
                  <Menu>
                    <MenuItem
                      v-for="action in rowActions(asUser(record))"
                      :key="action.key"
                      :danger="action.danger"
                      @click="onRowAction(asUser(record), action.key)"
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
          <!-- 四态之空：给出下一步动作，而不是只说"暂无数据" -->
          <EmptyState
            v-if="list.isEmpty"
            title="还没有用户"
            hint="点右上角「新建用户」录入第一个账号，或调整筛选条件"
            action-text="新建用户"
            secondary-action-text="清空筛选"
            @action="create"
            @secondary-action="onReset"
          />
          <EmptyState
            v-else-if="list.hasError"
            title="用户列表加载失败"
            :hint="list.error?.message ?? '请稍后重试'"
            action-text="重试"
            @action="list.reload()"
          />
        </template>
      </Table>
    </Spin>
  </PageLayout>
</template>

<style scoped>
.filter-hint,
.toolbar-hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}
</style>
