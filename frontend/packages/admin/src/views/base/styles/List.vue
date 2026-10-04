<script setup lang="ts">
/**
 * 款号列表页（docs/06 §2.2 的三段式）。
 *
 * ## 为什么这一页**不**走 `views/base/ResourceList.vue`
 *
 * 九个基础资料资源是同构的，所以共用一个列表组件；款号不是同构的：它有「归属客户 /
 * 分类 / 跟单」三个外键维度（有专门的候选接口与中文名回显），有「最近使用」排序
 * （`last_used_at DESC`，常用优先），还有一条"点进去是详情而不是编辑"的路径。
 * 硬塞进注册表意味着给 `ResourceList` 加 6 个 `if key === 'styles'` —— 那就是把
 * 九页共用的组件变成九页的补丁板。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Input, Select, Space, Spin, Table, Tag, Tooltip } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { PERM, formatDateTime } from '@garment/shared'
import type { StyleListOut } from '@garment/shared'
import { baseApi } from '@/api/base'
import { exportStyles, listStyles, searchCustomerOptionsById } from '@/api/styles'
import type { StyleListQuery } from '@/api/styles'
import { useExport } from '@/composables/useExport'
import { usePageList } from '@/composables/usePageList'
import EmptyState from '@/components/EmptyState.vue'
import PageLayout from '@/components/PageLayout.vue'
import TableToolbar from '@/components/TableToolbar.vue'

defineOptions({ name: 'StyleList' })

const router = useRouter()

const list = usePageList<StyleListQuery, StyleListOut>({
  fetch: listStyles,
  initialQuery: { page: 1, size: 20 },
})

const keyword = ref('')
const activeFilter = ref<'all' | 'active' | 'inactive'>('all')
const categoryFilter = ref<string | undefined>(undefined)
const customerFilter = ref<string | undefined>(undefined)

/**
 * 导出（docs/05 §9.1）。⚠️ 走 `useExport` 而不是自己拼 blob：文件名摘要、object URL
 *    的 revoke、「已导出 N 行」这三件事都在那里面，前手写一遍必然漏其中一件。
 *
 * ⚠️ `currentQuery` **每次点击时求值**：翻页或改筛选后导出的必须是**当前**范围，
 *    拿创建时的快照会导出上一次的筛选结果，而用户完全看不出来。
 */
const exporter = useExport({
  resourceLabel: '款号',
  api: { exportXlsx: exportStyles },
  // ⚠️ `page` / `size` **要删掉**而不是设成 undefined：导出不分页
  //    （docs/07 §3.2 铁律 3），留着 page=3 会让人以为导出的是第 3 页。
  //    用 `exactOptionalPropertyTypes` 时也不能写 `page: undefined`（那是显式的
  //    "有这个键但没值"），必须从对象里剔除。
  currentQuery: () => {
    // ⚠️ 用 `delete` 式的逐字段剔除而不是解构丢弃（`{page: _page, ...}`）：
    //    那样会引入两个没人用的变量，eslint 的 no-unused-vars 直接报错
    //    （默认没开 `argsIgnorePattern`，`_` 前缀不算豁免）
    const filters: StyleListQuery = { ...list.query }
    delete filters.page
    delete filters.size
    return filters
  },
})

const columns: ColumnsType<StyleListOut> = [
  { key: 'style_no', title: '款号', dataIndex: 'style_no', width: 150 },
  { key: 'name', title: '款名', dataIndex: 'name', width: 200 },
  { key: 'customer_name', title: '归属客户', dataIndex: 'customer_name', width: 160 },
  { key: 'category_name', title: '分类', dataIndex: 'category_name', width: 120 },
  { key: 'last_used_at', title: '最近使用', dataIndex: 'last_used_at', width: 150 },
  { key: 'is_active', title: '状态', dataIndex: 'is_active', width: 100 },
  { key: 'actions', title: '操作', width: 180, fixed: 'right' },
]

/** 分类候选（款号分类必填，B-CAT-02；value 是 id）。 */
const categoryOptions = ref<{ value: string; label: string }[]>([])

const customerOptions = ref<{ value: string; label: string }[]>([])

/**
 * 客户候选。
 *
 * ⚠️ **只能拉前 20 个做本地筛选** —— `/customers/options` 的 `size` 硬上限是 20
 * （05 §9.5.1「候选下拉强制 size ≤ 20」），而客户**没有页面**（docs/12 L-063），
 * 也就没有"搜客户"的地方可跳。所以在客户页落地前，这个筛选**对前 20 个客户有效**。
 * 与其在这里编一个"支持任意客户"的假象，不如把边界写在界面上。
 */
async function loadCustomerOptions(): Promise<void> {
  try {
    const options = await searchCustomerOptionsById()
    customerOptions.value = options.map((option) => ({ value: option.value, label: option.label }))
  } catch {
    customerOptions.value = []
  }
}

/** antd Select 的本地过滤（候选已是一次性拉回的一页）。 */
function filterCustomerOption(input: string, option: { label: string } | undefined): boolean {
  const keyword = input.trim().toLowerCase()
  if (keyword === '') return true
  return (option?.label ?? '').toLowerCase().includes(keyword)
}

async function loadCategoryOptions(): Promise<void> {
  try {
    const options = await baseApi['product-categories'].optionsById()
    categoryOptions.value = options.map((option) => ({ value: option.value, label: option.label }))
  } catch {
    // 拉不到候选不该阻断列表 —— 用户仍能看数据、搜索、点进详情
    categoryOptions.value = []
  }
}

function onSearch(): void {
  const q = keyword.value.trim()
  list.applyFilter({
    q: q === '' ? undefined : q,
    is_active: activeFilter.value === 'all' ? undefined : activeFilter.value === 'active',
    category_id: categoryFilter.value,
    customer_id: customerFilter.value,
  })
}

function onReset(): void {
  keyword.value = ''
  activeFilter.value = 'all'
  categoryFilter.value = undefined
  customerFilter.value = undefined
  onSearch()
}

function onActiveChange(raw: unknown): void {
  activeFilter.value = raw === 'active' || raw === 'inactive' ? raw : 'all'
  onSearch()
}

function onCategoryChange(raw: unknown): void {
  categoryFilter.value = typeof raw === 'string' && raw !== '' ? raw : undefined
  onSearch()
}

function onCustomerChange(raw: unknown): void {
  customerFilter.value = typeof raw === 'string' && raw !== '' ? raw : undefined
  onSearch()
}

function create(): void {
  void router.push({ name: 'base-styles-new' })
}

/** 点款号进**详情**而不是编辑 —— 款号的主操作是配工序 / 比例 / 单价，不是改名字。 */
function openDetail(styleNo: string): void {
  void router.push({ name: 'base-styles-detail', params: { styleNo } })
}

function edit(styleNo: string): void {
  void router.push({ name: 'base-styles-edit', params: { styleNo } })
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
  current: list.query.page ?? 1,
  pageSize: list.query.size ?? 20,
  showTotal: (value: number) => `共 ${value} 个款号`,
}))

onMounted(() => {
  void list.reload()
  void loadCategoryOptions()
  void loadCustomerOptions()
})
</script>

<template>
  <PageLayout title="款号" :description="`共 ${list.total} 个款号`">
    <template #extra>
      <Button
        v-can="[PERM.BASE_EXPORT, PERM.SYSTEM_EXPORT_MANAGE]"
        :loading="exporter.exporting"
        @click="exporter.run()"
      >
        导出
      </Button>
      <Button v-can="PERM.BASE_CREATE" type="primary" @click="create">新建款号</Button>
    </template>

    <template #filter="{ collapsed }">
      <Space direction="vertical" size="small" style="width: 100%">
        <Space wrap>
          <Input
            v-model:value="keyword"
            placeholder="款号 / 款名 / 客户名"
            style="width: 240px"
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
          <!--
            分类 / 归属客户筛选。⚠️ `value` 是 **UUID**（提交 `category_id` / `customer_id`
            要的就是 id），所以走 `optionsById` 而不是 `/options`（那个给业务编码）。
          -->
          <Select
            :value="categoryFilter"
            placeholder="按分类"
            style="width: 150px"
            allow-clear
            :options="categoryOptions"
            @change="onCategoryChange"
          />
          <Select
            :value="customerFilter"
            placeholder="按归属客户"
            style="width: 170px"
            allow-clear
            show-search
            :options="customerOptions"
            :filter-option="filterCustomerOption"
            @change="onCustomerChange"
          />
          <Button type="primary" @click="onSearch">查询</Button>
          <Button @click="onReset">重置</Button>
        </Space>
        <span v-if="collapsed === false" class="filter-hint">
          款号列表默认按最近使用排序（常用优先）；候选下拉也是这个顺序
        </span>
      </Space>
    </template>

    <template #toolbar>
      <TableToolbar :selected-count="0" :total="list.total">
        <template #actions="{ enabled }">
          <span v-if="!enabled" class="toolbar-hint">
            款号的主操作在详情页（配色组 / 尺码 / 工序 / 单价）
          </span>
        </template>
      </TableToolbar>
    </template>

    <Spin :spinning="list.loading">
      <Table
        :columns="columns"
        :data-source="list.items"
        :row-key="(row: StyleListOut) => row.id"
        size="small"
        :pagination="pagination"
        :scroll="{ x: 960 }"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'style_no'">
            <!-- 款号是主键式的业务编码：不换行 + 等宽 + title 给全（docs/06 §2.2） -->
            <span class="code-cell" :title="record.style_no">{{ record.style_no }}</span>
          </template>

          <template v-else-if="column.key === 'customer_name'">
            {{ record.customer_name ?? '—' }}
          </template>

          <template v-else-if="column.key === 'category_name'">
            <!-- 分类必填（B-CAT-02），但列表仍兜一层空值 —— 旧数据可能没有 -->
            <Tag v-if="record.category_name" color="blue">{{ record.category_name }}</Tag>
            <span v-else class="cell-muted">—</span>
          </template>

          <template v-else-if="column.key === 'last_used_at'">
            <!--
              「最近使用」是款号候选默认排序的依据（05 §9.5.2）—— 用户最常开的是常用款，
              所以这一列要能一眼看出"这个款最近没人用"。
            -->
            <Tooltip v-if="record.last_used_at" :title="formatDateTime(record.last_used_at)">
              <span>{{ formatDateTime(record.last_used_at, { withDate: false }) }}</span>
            </Tooltip>
            <span v-else class="cell-muted">从未使用</span>
          </template>

          <template v-else-if="column.key === 'is_active'">
            <Tag :color="record.is_active ? 'success' : 'default'">
              {{ record.is_active ? '启用中' : '已停用' }}
            </Tag>
          </template>

          <template v-else-if="column.key === 'actions'">
            <Space>
              <Button
                v-can="PERM.BASE_READ"
                size="small"
                type="link"
                @click="openDetail(record.style_no)"
              >
                详情
              </Button>
              <Button
                v-can="PERM.BASE_UPDATE"
                size="small"
                type="link"
                @click="edit(record.style_no)"
              >
                编辑
              </Button>
            </Space>
          </template>
        </template>

        <template #emptyText>
          <EmptyState
            v-if="list.isEmpty"
            title="还没有款号"
            hint="点右上角「新建款号」建第一个档案；货号由你自己填，系统只给建议"
            action-text="新建款号"
            secondary-action-text="清空筛选"
            @action="create"
            @secondary-action="onReset"
          />
          <EmptyState
            v-else-if="list.hasError"
            title="款号列表加载失败"
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
.toolbar-hint,
.cell-muted {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}
</style>
