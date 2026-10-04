<script setup lang="ts">
/**
 * 基础资料列表页（docs/06 §2.2 的三段式；九个资源共用一个组件）。
 *
 * ## 为什么要做成「一个组件 + 九个 3 行文件」而不是九份 List.vue
 *
 * 后端九个资源的 CRUD 形状完全同构（`resources.py::RESOURCES` 就是为此而生）。
 * 前端若照抄九份，「停用要必填原因」「删除被引用时不给入口」「导出与列表同一筛选」
 * 这些规则就有九份拷贝 —— 漏一份就是一个可以随便停用主数据的漏洞。
 * 差异（中文名、列、筛选项）全在 `api/base.ts` 的注册表里声明。
 *
 * 每个资源目录下仍保留 `List.vue` / `Form.vue` 两个文件（docs/03 §2.5 的目录约定），
 * 内容只是把 key 传进来：
 *
 * ```vue
 * <template><ResourceList resource-key="colors" /></template>
 * ```
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
  Tooltip,
  message,
} from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { BASE_DICT_CONTRACT, PERM } from '@garment/shared'
import type { BaseDictRow } from '@garment/shared'
import { baseApi, compactQuery, resourceDecl } from '@/api/base'
import type {
  BaseFilter,
  BaseListQuery,
  BaseResourceApi,
  BaseResourceDecl,
  RegistryKey,
} from '@/api/base'
import { listMissingBuiltin, restoreBuiltin } from '@/api/system'
import { confirmDanger } from '@/utils/danger'
import { usePageList } from '@/composables/usePageList'
import { useExport } from '@/composables/useExport'
import EmptyState from '@/components/EmptyState.vue'
import PageLayout from '@/components/PageLayout.vue'
import TableToolbar from '@/components/TableToolbar.vue'

const props = defineProps<{ resourceKey: RegistryKey }>()

defineOptions({ name: 'BaseResourceList' })

/**
 * 把 antd 表格插槽里的 `record` 收成 `BaseDictRow`。
 *
 * ⚠️ 与系统管理两页同一个理由：antd 把 Table 的 columns 泛型在 props 上写死成
 * `any`，插槽里的 `record` 就是 `Record<string, any>` —— 不收口的话模板里
 * 任何一处字段名笔误都不会报红，而症状是「那一列是空的」（页面上零报错）。
 */
function asRow(record: Record<string, unknown>): BaseDictRow {
  return record as unknown as BaseDictRow
}

/**
 * 每个页面文件传进来的是**字面量**（`<ResourceList resource-key="colors" />`），
 * 不是路由参数，所以取一次就够，不需要 `computed`。
 */
const resourceKey = props.resourceKey
// ⚠️ 显式标注成 `BaseResourceDecl`：注册表是 `as const`，不标注的话 `decl.filters`
//    的元素是**字面量联合**，某个资源没写 `placeholder` 时模板里读它直接报 TS2339。
const decl: BaseResourceDecl = resourceDecl(resourceKey)
const contract = BASE_DICT_CONTRACT[resourceKey]
// ⚠️ 显式标注 `BaseResourceApi`：不标注的话 `baseApi[resourceKey]` 是九个对象类型的
//    **联合**，联合上的同名方法不是同一个函数签名，`api.list(query)` 直接报
//    "No overload matches this call"。
const api: BaseResourceApi = baseApi[resourceKey]

const router = useRouter()

const list = usePageList<BaseListQuery, BaseDictRow>({
  fetch: (query) => api.list(query),
  initialQuery: { page: 1, size: 20 },
})

const keyword = ref('')
const activeFilter = ref<'all' | 'active' | 'inactive'>('all')
const extraFilters = ref<Record<string, string | boolean | undefined>>({})

/** 「更多条件」是否展开（默认收起，docs/06 §2.2）。 */
const expanded = ref(false)

const exportState = useExport({
  resourceLabel: decl.title,
  api,
  // ⚠️ 点击时求值：翻页 / 改筛选之后导出的必须是**当前**范围
  currentQuery: () => list.query,
  labelOf: (name, value) => filterLabel(name, String(value)),
})

function filterLabel(name: string, value: string): string {
  const filter = decl.filters.find((item) => item.name === name)
  if (filter?.options !== undefined) return filter.options[value] ?? value
  return value
}

// ⚠️ 每列都给 `dataIndex` —— 只有 `key` 没有 `dataIndex` 时 antd 渲染成**空白单元格**
//    且页面上零报错（T-WEB-004 踩过）。名字的类型是 `keyof BaseDictRow`，
//    打错在 `vue-tsc` 阶段就红。
const columns = computed<ColumnsType<BaseDictRow>>(() => {
  // 先把注册表列收成 `ColumnsType` 再拼后面三列：直接返回数组字面量的话，推导出来的
  // 类型与 `ColumnsType` 有细微差别（`align: 'right' | undefined` 在
  // exactOptionalPropertyTypes 下不兼容），`computed<T>` 会报 "No overload matches"。
  const declared: ColumnsType<BaseDictRow> = decl.columns.map((column) => ({
    key: column.name,
    title: column.label,
    dataIndex: column.name,
    width: column.width,
    ...(column.numeric === true ? { align: 'right' as const } : {}),
  }))
  return [
    ...declared,
    { key: 'is_active', title: '状态', dataIndex: 'is_active', width: 130 },
    // 「引用」列无条件渲染：不参与引用检查的资源 `ref_count` 是 `null`，显示 `—`。
    // 少一列比多一列好 —— 字典三表与工序都要它（T-WEB-005 验收标准）。
    { key: 'ref_count', title: '引用', dataIndex: 'ref_count', width: 90 },
    { key: 'actions', title: '操作', width: 160, fixed: 'right' },
  ]
})

function onSearch(): void {
  const q = keyword.value.trim()
  list.applyFilter({
    q: q === '' ? undefined : q,
    is_active: activeFilter.value === 'all' ? undefined : activeFilter.value === 'active',
    ...extraFilters.value,
  })
}

function onReset(): void {
  keyword.value = ''
  activeFilter.value = 'all'
  extraFilters.value = {}
  onSearch()
}

/**
 * antd `Select` 的 `@change` 传的是 `SelectValue`（`string | number | …`）。
 * 模板里不能写类型标注（`(raw: unknown) => …` 报 TS1109），所以收 `unknown`
 * 再收窄的逻辑必须放在具名函数里。
 */
function onActiveChange(raw: unknown): void {
  activeFilter.value = raw === 'active' || raw === 'inactive' ? raw : 'all'
  onSearch()
}

function onFilterSelect(filter: BaseFilter, raw: unknown): void {
  const text = raw === null || raw === undefined ? '' : String(raw)
  // ⚠️ 布尔筛选用 `'1' / '0'` 过 Select（antd 的 SelectValue 不接受 boolean），
  //    落到查询参数时再换回 true / false —— 后端 `is_piecework` 要的是布尔。
  const value: string | boolean | undefined =
    filter.control === 'bool'
      ? text === '1'
        ? true
        : text === '0'
          ? false
          : undefined
      : text === ''
        ? undefined
        : text
  extraFilters.value = { ...extraFilters.value, [filter.name]: value }
  onSearch()
}

/** Select 的当前值。⚠️ 布尔筛选用字符串回显 —— `false` 传给 Select 会被当成"没选"。 */
function filterSelectValue(filter: BaseFilter): string | undefined {
  const value = extraFilters.value[filter.name]
  if (value === undefined) return undefined
  if (value === true) return '1'
  if (value === false) return '0'
  return String(value)
}

function onFilterInput(filter: BaseFilter, event: Event): void {
  const value = (event.target as HTMLInputElement).value
  extraFilters.value = { ...extraFilters.value, [filter.name]: value === '' ? undefined : value }
  onSearch()
}

function toggleExpanded(): void {
  expanded.value = !expanded.value
}

/** 某行是否可删：`ref_count` 为 `null` = 不参与引用检查；`0` = 无人引用。 */
function deletable(row: BaseDictRow): boolean {
  return row.ref_count === null || row.ref_count === 0
}

/** 删不掉时的原因（写进菜单项文案与 title，docs/06 §6：按钮都要有说明）。 */
function blockedReason(row: BaseDictRow): string {
  const sources = contract.refCheckers.join('、')
  return `不能删除：已被 ${row.ref_count ?? 0} 处引用${sources === '' ? '' : `（${sources}）`}，请改为停用`
}

function create(): void {
  void router.push({ name: `base-${resourceKey}-new` })
}

function edit(row: BaseDictRow): void {
  void router.push({ name: `base-${resourceKey}-edit`, params: { code: rowCode(row) } })
}

/** 行的路径参数值 = 编码列（码表没有编码列，`name` 兼作路径参数）。 */
function rowCode(row: BaseDictRow): string {
  return String((row as unknown as Record<string, unknown>)[contract.codeColumn] ?? '')
}

function rowKey(row: BaseDictRow): string {
  return row.id
}

/** 表格里某个单元格的原始值（空值统一显示 `—`，空白分不清是没填还是没取到）。 */
function cellText(record: Record<string, unknown>, columnKey: unknown): string {
  const value = record[String(columnKey)]
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

async function onDisable(row: BaseDictRow): Promise<void> {
  const result = await confirmDanger({
    title: `停用${decl.itemLabel}`,
    // ⚠️ 后果必须写清：用户是照着这句话决定要不要点确定的
    content: `停用后 ${rowCode(row)} 不再出现在新建${decl.itemLabel}的候选里，已开出的单据不受影响。停用只是标记，删除不可撤销。`,
    requireReason: true,
    okText: '停用',
  })
  if (result === null) return
  await api.disable(rowCode(row), result.reason)
  void message.success('已停用')
  await list.reload()
}

/**
 * 删除（字典表真删 / 其余软删）。
 *
 * ⚠️ 引用 > 0 的行**结构上不给入口**（菜单项 disabled，见 `rowActions`），
 *    这里再挡一次是防"用户点的是刷新前的菜单"：列表刷新后引用数可能已经变了。
 */
async function onDelete(row: BaseDictRow): Promise<void> {
  if (!deletable(row)) {
    void message.warning(blockedReason(row))
    return
  }
  const code = rowCode(row)
  const result = await confirmDanger({
    title: `删除${decl.itemLabel} ${code}`,
    content: contract.allowPhysicalDelete
      ? '删除后**不可撤销**（字典项是真删，不是隐藏）。恢复只能靠「恢复内置库」重建内置的那一部分，自建的内容找不回来。'
      : `删除后该${decl.itemLabel}不再出现在候选里，历史单据仍显示原来的名称。`,
    okText: '删除',
  })
  if (result === null) return
  try {
    await api.remove(code)
    void message.success('已删除')
  } catch (caught) {
    // 并发兜底：查引用之后、删除之前有人插入了引用（ADR-0025 的条件 DELETE 里
    // NOT EXISTS 命中为假）→ 后端回 20003
    if (caught instanceof Error && 'code' in caught && (caught as { code: number }).code === 20003) {
      void message.error(blockedReason(row))
      return
    }
    throw caught
  }
  await list.reload()
}

interface RowAction {
  key: 'disable' | 'delete'
  label: string
  /** 破坏性操作 → 菜单项标红，且必须走 `confirmDanger`。 */
  danger: boolean
  disabled: boolean
  /** 禁用原因。写进菜单项文案与 title —— 只灰一个按钮而不说为什么，用户会反复点。 */
  reason: string
}

function rowActions(row: BaseDictRow): RowAction[] {
  const canDelete = deletable(row)
  return [
    {
      key: 'disable',
      label: '停用',
      danger: true,
      disabled: row.is_active !== true,
      reason: row.is_active !== true ? '该条已停用' : '',
    },
    {
      key: 'delete',
      label: '删除',
      danger: true,
      disabled: !canDelete,
      reason: canDelete ? '' : blockedReason(row),
    },
  ]
}

function actionLabel(action: RowAction): string {
  return action.disabled && action.reason !== '' ? `${action.label}（${action.reason}）` : action.label
}

async function onRowAction(row: BaseDictRow, action: RowAction): Promise<void> {
  if (action.disabled) {
    if (action.reason !== '') void message.warning(action.reason)
    return
  }
  if (action.key === 'disable') {
    await onDisable(row)
    return
  }
  await onDelete(row)
}

const restoring = ref(false)

/**
 * 「恢复内置库」（只出现在字典三表 —— 由 `hasBuiltinFlag` 决定，不在前端声明）。
 *
 * ## 交互顺序：先告诉用户少了什么，再让他确认
 *
 * 直接弹「确定恢复吗？」的话，用户不知道会凭空多出 16 个颜色 —— 而"我的颜色列表
 * 怎么突然变了"比"恢复失败"更让人不信任。所以先查缺失清单，把数量摆出来。
 */
async function onRestoreBuiltin(): Promise<void> {
  restoring.value = true
  try {
    const missing = await listMissingBuiltin()
    const detail = Object.entries(missing.dicts)
      .filter(([, codes]) => codes.length > 0)
      .map(([table, codes]) => `${table} ${codes.length} 个`)
      .join('、')
    if (detail === '') {
      void message.info('内置库完整，没有缺失项')
      return
    }
    const confirmed = await confirmDanger({
      title: '恢复内置库',
      content: `将重建缺失的内置数据：${detail}。⚠️ 只恢复**内置**项 —— 用户自建的${decl.itemLabel}删掉后找不回来。`,
      okText: '恢复',
    })
    if (confirmed === null) return
    // ⚠️ 三类**都显式传**：这个按钮的语义就是「只恢复字典」，而生成类型里三个键
    //    都是必填（pydantic 给了默认值但 openapi 仍标成 required）
    const result = await restoreBuiltin({ dicts: true, permissions: false, roles: false })
    void message.success(result.message)
    await list.reload()
  } finally {
    restoring.value = false
  }
}

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
  // ⚠️ `?? 1` / `?? 20`：PageQuery 的 page / size 是**可选**的，而 antd 的分页配置
  // 在 exactOptionalPropertyTypes 下不接受 undefined
  current: list.query.page ?? 1,
  pageSize: list.query.size ?? 20,
  showTotal: (value: number) => `共 ${value} 个${decl.itemLabel}`,
}))

/** 筛选摘要：工具栏与导出文件名都用它。 */
const filterSummary = computed(() =>
  Object.entries(compactQuery({ ...list.query }))
    .filter(([name]) => name !== 'page' && name !== 'size')
    .map(([name, value]) => filterLabel(name, String(value)))
    .join('、'),
)

onMounted(() => {
  void list.reload()
})
</script>

<template>
  <PageLayout :title="decl.title" :description="`共 ${list.total} 个${decl.itemLabel}`">
    <template #extra>
      <!--
        导出与新建相邻（docs/05 §9.1）。`v-can` 用后端声明的导出权限点 —— 分类与工序
        的写权限不是 `base:*`，写成 base:export 会让有权限的人被藏掉按钮。
      -->
      <Button
        v-can="contract.permissions.export"
        :loading="exportState.exporting"
        @click="exportState.run()"
      >
        导出
      </Button>
      <!--
        「恢复内置库」只给字典三表。⚠️ 文案写「恢复内置库」而不是「找回删除的数据」：
        它只重建 seed 清单里的编码，自建项找不回来（ADR-0025 §决策 3）。
      -->
      <Button
        v-if="contract.hasBuiltinFlag"
        v-can="PERM.SYSTEM_CONFIG_MANAGE"
        :loading="restoring"
        @click="onRestoreBuiltin"
      >
        恢复内置库
      </Button>
      <Button v-can="contract.permissions.create" type="primary" @click="create">
        新建{{ decl.itemLabel }}
      </Button>
    </template>

    <template #filter="{ collapsed }">
      <Space direction="vertical" size="small" style="width: 100%">
        <Space wrap>
          <Input
            v-model:value="keyword"
            placeholder="编码或名称"
            style="width: 200px"
            allow-clear
            @press-enter="onSearch"
          />
          <Select
            :value="activeFilter"
            style="width: 130px"
            :options="[
              { value: 'all', label: '全部状态' },
              { value: 'active', label: '启用中' },
              { value: 'inactive', label: '已停用' },
            ]"
            @change="onActiveChange"
          />
          <Button type="primary" @click="onSearch">查询</Button>
          <Button @click="onReset">重置</Button>
          <Button v-if="decl.filters.length > 0" type="link" @click="toggleExpanded">
            {{ expanded ? '收起条件' : '更多条件' }}
          </Button>
        </Space>

        <Space v-if="expanded" wrap>
          <span class="filter-hint">更多条件</span>
          <template v-for="filter in decl.filters" :key="filter.name">
            <Select
              v-if="filter.control === 'enum' && filter.options"
              :value="filterSelectValue(filter)"
              :placeholder="filter.label"
              style="width: 140px"
              allow-clear
              :options="Object.entries(filter.options).map(([value, label]) => ({ value, label }))"
              @change="onFilterSelect(filter, $event)"
            />
            <Select
              v-else-if="filter.control === 'bool'"
              :value="filterSelectValue(filter)"
              :placeholder="filter.label"
              style="width: 130px"
              allow-clear
              :options="[
                { value: '1', label: '是' },
                { value: '0', label: '否' },
              ]"
              @change="onFilterSelect(filter, $event)"
            />
            <Input
              v-else
              :value="String(extraFilters[filter.name] ?? '')"
              :placeholder="filter.placeholder ?? filter.label"
              style="width: 160px"
              allow-clear
              @change="onFilterInput(filter, $event)"
            />
          </template>
        </Space>
        <span v-else-if="collapsed === false && decl.filters.length === 0" class="filter-hint">
          本资源只有关键字与状态两个筛选条件
        </span>
      </Space>
    </template>

    <template #toolbar>
      <TableToolbar
        :selected-count="0"
        :total="list.total"
        :exporting="exportState.exporting"
        :export-permission="contract.permissions.export"
        @export="exportState.run()"
      >
        <template #actions>
          <span class="toolbar-hint">{{
            filterSummary === '' ? '未筛选，导出全部' : `当前筛选：${filterSummary}`
          }}</span>
        </template>
      </TableToolbar>
    </template>

    <Spin :spinning="list.loading">
      <Table
        :columns="columns"
        :data-source="list.items"
        :row-key="rowKey"
        size="small"
        :pagination="pagination"
        :scroll="{ x: 960 }"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'is_active'">
            <!--
              停用不是单据状态，所以**不能**用 StatusTag（它的映射固定为 docs/06 §1 的
              五档单据状态）。用一个 Tag，色走 token。
            -->
            <Tag :color="asRow(record).is_active ? 'success' : 'default'">
              {{ asRow(record).is_active ? '启用中' : '已停用' }}
            </Tag>
            <!-- 内置角标：只给字典三表（`hasBuiltinFlag`），且**不影响**能否删除（R27） -->
            <Tag v-if="contract.hasBuiltinFlag && asRow(record).is_builtin === true">内置</Tag>
          </template>

          <template v-else-if="column.key === 'ref_count'">
            <span v-if="asRow(record).ref_count === null" class="cell-muted">—</span>
            <span v-else-if="asRow(record).ref_count === 0">0</span>
            <Tooltip v-else :title="blockedReason(asRow(record))">
              <span class="cell-danger">{{ asRow(record).ref_count }}</span>
            </Tooltip>
          </template>

          <template v-else-if="column.key === 'actions'">
            <Space>
              <Button
                v-can="contract.permissions.update"
                size="small"
                type="link"
                @click="edit(asRow(record))"
              >
                编辑
              </Button>
              <!--
                破坏性操作进 `…` 下拉（docs/06 §2.2「破坏性操作不用红色文字按钮，
                避免误点」）。⚠️ antd 会把 Dropdown **teleport 到 document.body**，
                单测里必须从 document 查，否则「菜单里没有删除项」这类断言假绿。
              -->
              <Dropdown :trigger="['click']">
                <Button size="small" type="link">…</Button>
                <template #overlay>
                  <Menu>
                    <MenuItem
                      v-for="action in rowActions(asRow(record))"
                      :key="action.key"
                      :danger="action.danger"
                      :disabled="action.disabled"
                      :title="action.reason"
                      @click="onRowAction(asRow(record), action)"
                    >
                      {{ actionLabel(action) }}
                    </MenuItem>
                  </Menu>
                </template>
              </Dropdown>
            </Space>
          </template>

          <template v-else>
            <!-- 编码 / 单号列不换行 + 等宽，超长省略（docs/06 §2.2），title 给全 -->
            <span class="code-cell" :title="cellText(record, column.key)">
              {{ cellText(record, column.key) }}
            </span>
          </template>
        </template>

        <template #emptyText>
          <!-- 四态之空：给出下一步动作，而不是只说"暂无数据" -->
          <EmptyState
            v-if="list.isEmpty"
            :title="`还没有${decl.itemLabel}`"
            :hint="`点右上角「新建${decl.itemLabel}」录入第一条，或调整筛选条件`"
            :action-text="`新建${decl.itemLabel}`"
            secondary-action-text="清空筛选"
            @action="create"
            @secondary-action="onReset"
          />
          <EmptyState
            v-else-if="list.hasError"
            :title="`${decl.title}列表加载失败`"
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

.cell-muted {
  color: var(--color-text-third);
}

/* 引用数 > 0：标成危险色 —— 它意味着"这一行删不掉"（docs/06 §1 功能色） */
.cell-danger {
  color: var(--color-danger);
  font-weight: 600;
  cursor: help;
}
</style>