<script setup lang="ts">
/**
 * PC 端布局（docs/06 §2.1）。
 *
 * ```
 * ┌────────────────────────────────────────────────────────┐
 * │ Header 48px：Logo | 折叠 | 面包屑 | 消息 | 用户          │
 * ├──────────┬─────────────────────────────────────────────┤
 * │ Sider    │ Content：24px 内边距，最大 1680px 居中        │
 * │ 200px    │                                             │
 * │ 两级菜单 │                                             │
 * └──────────┴─────────────────────────────────────────────┘
 * ```
 *
 * ## 菜单按权限过滤，但不隐藏"存在性"
 *
 * 无权限的菜单项直接不渲染（docs/07 §4.1「菜单项按权限过滤」）。注意这**不是**
 * 安全措施 —— 用户手动输 URL 仍然进得去（会被守卫送到 403），所以守卫那道判定是
 * 独立于这里的，两边都用了同一份权限点但**刻意不共用同一份数据**（见 `menu.ts`）。
 *
 * ## 断点行为（docs/06 §6）
 *
 * `<1366px` 自动收起成图标；用户手动折叠的状态在断点变化时不被覆盖 —— 现场工位机
 * 常年固定一个分辨率，自动覆盖用户的选择只会让人重新点一次。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { Avatar, Breadcrumb, Dropdown, Layout, Menu, Tooltip } from 'ant-design-vue'
import type { MenuProps } from 'ant-design-vue'
import { useAuthStore } from '@/stores/auth'
import { MENU_GROUPS } from '@/layouts/menu'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

// 侧栏宽度从 token 读，不用字面量（docs/06 §1）。antd 的 Sider 两个宽度 props 都接受
// 字符串并直接落到 CSS width，所以这里能保持"组件里没有字面量尺寸"这条纪律。
const siderWidth = 'var(--sider-width)'
const collapsedSiderWidth = 'var(--sider-collapsed-width)'

/** 用户手动折叠的状态；`null` = 跟随断点自动决定。 */
const manualCollapsed = ref<boolean | null>(null)
const width = ref(typeof window === 'undefined' ? 1920 : window.innerWidth)

/** 平板 / 小屏自动收成图标（docs/06 §6）。 */
const autoCollapsed = computed(() => width.value < 1366)
const collapsed = computed(() => manualCollapsed.value ?? autoCollapsed.value)

function toggleCollapsed(): void {
  manualCollapsed.value = !collapsed.value
}

function onResize(): void {
  width.value = window.innerWidth
}

onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => window.removeEventListener('resize', onResize))

/** 按 `auth.has()` 过滤后的菜单；两组都空时整个侧栏不渲染（省 200px 给表格）。 */
const visibleGroups = computed(() =>
  MENU_GROUPS.map((group) => ({
    ...group,
    children: group.children.filter((item) => {
      const required = item.permission
      // ⚠️ 用 `typeof === 'string'` 而不是 `Array.isArray`：类型是
      //   `PermissionCode | readonly PermissionCode[]`，**readonly 数组**不会被
      //   Array.isArray 收窄（编译后仍是联合类型），tsc 会把 readonly string[]
      //   塞给 has(code: string) 而报错。
      return typeof required === 'string' ? auth.has(required) : auth.hasAny(required)
    }),
  })).filter((group) => group.children.length > 0),
)

// ⚠️ 显式标成 NonNullable：MenuProps['items'] 的类型带 undefined，
//    而 exactOptionalPropertyTypes 下 `:items="` 不接受 undefined。
//    这里实际永远不会是 undefined（map 一定返回数组），用 NonNullable 说清这一点。
const menuItems = computed<NonNullable<MenuProps['items']>>(() =>
  visibleGroups.value.map((group) => ({
    key: group.key,
    label: collapsed.value ? group.title.slice(0, 2) : group.title,
    children: group.children.map((item) => ({ key: item.key, label: item.title })),
  })),
)

const selectedKeys = computed(() => (typeof route.name === 'string' ? [route.name] : []))

/** 面包屑：组标题 + 页面标题（`meta.title` 是页面与文档标题的唯一来源）。 */
const breadcrumbs = computed(() => {
  const title = route.meta['title']
  if (typeof title !== 'string' || title === '') return []
  const group = visibleGroups.value.find((g) => g.children.some((c) => c.key === route.name))
  return group === undefined ? [title] : [group.title, title]
})

const userMenu = computed<NonNullable<MenuProps['items']>>(() => [
  { key: 'scope', label: `数据范围：${auth.user?.employee_no ?? '未知'}`, disabled: true },
  { type: 'divider' },
  { key: 'password', label: '修改密码' },
  { key: 'logout', label: '退出登录' },
])

/**
 * 菜单跳转。
 *
 * ⚠️ 这里必须用具名函数而不是模板内联箭头：Vue 模板表达式里**不能写 TS 类型标注**
 *    （`({ key }: { key: string }) => ...` 直接报 TS1005），而且内联箭头每次
 *    重渲染都新建一个函数、传给子组件的 prop 引用每次都变。
 */
function onMenuClick(info: { key: string | number }): void {
  void router.push({ name: String(info.key) })
}

async function onUserMenuClick(info: { key: string | number }): Promise<void> {
  if (info.key === 'password') {
    await router.push({ name: 'change-password' })
    return
  }
  if (info.key === 'logout') {
    await auth.logout()
    await router.replace({ name: 'login' })
  }
}
</script>

<template>
  <Layout class="admin-layout">
    <Layout.Header class="admin-header">
      <div class="admin-header-left">
        <span class="admin-logo">服装厂 ERP</span>
        <Tooltip :title="collapsed ? '展开菜单' : '收起菜单'">
          <button
            type="button"
            class="admin-collapse-btn"
            :aria-expanded="!collapsed"
            :aria-label="collapsed ? '展开菜单' : '收起菜单'"
            @click="toggleCollapsed"
          >
            {{ collapsed ? '»' : '«' }}
          </button>
        </Tooltip>
      </div>

      <Breadcrumb class="admin-breadcrumb">
        <Breadcrumb.Item v-for="crumb in breadcrumbs" :key="crumb">{{ crumb }}</Breadcrumb.Item>
      </Breadcrumb>

      <div class="admin-header-right">
        <Dropdown :trigger="['click']" placement="bottomRight">
          <span class="admin-user">
            <Avatar :size="24">{{ auth.user?.name?.slice(0, 1) ?? '?' }}</Avatar>
            <span class="admin-user-name">{{ auth.user?.name ?? '未登录' }}</span>
          </span>
          <template #overlay>
            <Menu :items="userMenu" @click="onUserMenuClick" />
          </template>
        </Dropdown>
      </div>
    </Layout.Header>

    <Layout has-sider class="admin-body">
      <Layout.Sider
        v-if="visibleGroups.length > 0"
        :collapsed="collapsed"
        :width="siderWidth"
        :collapsed-width="collapsedSiderWidth"
        :trigger="null"
        theme="light"
        class="admin-sider"
      >
        <Menu
          mode="inline"
          :items="menuItems"
          :selected-keys="selectedKeys"
          :inline-collapsed="collapsed"
          class="admin-menu"
          @click="onMenuClick"
        />
      </Layout.Sider>

      <Layout.Content class="layout-content">
        <div class="admin-content-inner">
          <!--
            ⚠️ 必须是 `<RouterView />`，**不能是 `<slot />`**。
            vue-router 4 里父路由组件负责渲染 `children`：App.vue 的
            `<RouterView />` 只渲染第一层（`AdminLayout` 自己），
            第二层要靠本组件内部的 `<RouterView />` 才会被渲染。

            写成 `<slot />` 的话，侧栏、面包屑、顶栏全都正常，**只有内容区永远空白** ——
            而且组件测试完全测不出来（那些测试直接 mount 页面组件，不经过 layout），
            直到 E2E 真的用浏览器打开一个 URL 才暴露。这是「单测全绿而产品打不开」的
            典型样本。
          -->
          <RouterView />
        </div>
      </Layout.Content>
    </Layout>
  </Layout>
</template>

<style scoped>
.admin-layout {
  height: 100%;
}

.admin-header {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  height: var(--header-height);
  padding: 0 var(--space-4);
  background: var(--color-bg-card);
  border-bottom: 1px solid var(--color-border);
  /* Header 固定：不跟着内容区滚（docs/06 §2.1） */
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
}

.admin-header-left {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.admin-logo {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--color-primary);
  white-space: nowrap;
}

.admin-collapse-btn {
  border: none;
  background: transparent;
  color: var(--color-text-second);
  cursor: pointer;
  font-size: var(--font-size-lg);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
}

.admin-collapse-btn:hover {
  background: var(--color-bg);
  color: var(--color-text);
}

.admin-breadcrumb {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.admin-header-right {
  margin-left: auto;
}

.admin-user {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
}

.admin-user-name {
  color: var(--color-text-second);
  white-space: nowrap;
}

.admin-body {
  min-height: 0;
}

.admin-sider {
  border-right: 1px solid var(--color-border);
  background: var(--color-bg-card);
}

.admin-menu {
  border-inline-end: none !important;
}

.admin-content-inner {
  /* docs/06 §2.1：内容区 24px 内边距，最大 1680px 居中（超宽屏不要拉满） */
  padding: var(--space-6);
  max-width: var(--page-max-width);
  margin: 0 auto;
  width: 100%;
}
</style>
