/**
 * 列表页的「查询 + 分页 + 筛选 + 四态」通用逻辑。
 *
 * ## 为什么抽出来
 *
 * 每个模块的 `List.vue` 都要同一套东西：翻页、改变筛选后回到第 1 页、加载态、
 * 空态、错误态带重试。写两遍就有两套写法，而两套写法里迟早有一处忘了
 * "改筛选要回第 1 页" —— 症状是用户改了筛选条件却停在第 5 页，看到一个空列表，
 * 于是以为数据没了。
 *
 * ## 四态分别归谁
 *
 * | 态 | 这里给什么 | 页面渲染什么 |
 * | --- | --- | --- |
 * | 加载 | `loading` | 表格内 Spin / Skeleton（docs/06 §2.2「禁止整页闪烁」） |
 * | 空 | `isEmpty` | `EmptyState`（带下一步动作） |
 * | 错误 | `hasError` + `error` | `EmptyState` + 「重试」 |
 * | 无权限 | —— | 路由守卫已把人送去 `/403`，页面不用管 |
 */
import { computed, reactive, ref } from 'vue'
import { ApiError } from '@garment/shared'
import type { PageQuery, QueryValue } from '@garment/shared'

export interface PageListOptions<TQuery extends PageQuery, TItem> {
  /** 拉数据。返回 `{items, total}`。 */
  fetch: (query: TQuery) => Promise<{ items: TItem[]; total: number }>
  /** 初始查询条件（通常是 `{ page: 1, size: 20 }`）。 */
  initialQuery: TQuery
}

export function usePageList<TQuery extends PageQuery, TItem>(
  options: PageListOptions<TQuery, TItem>,
) {
  const query = ref<TQuery>({ ...options.initialQuery })
  const items = ref<TItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<ApiError | null>(null)

  /**
   * 改筛选条件 —— **自动回第 1 页**。
   *
   * ⚠️ 这一条是本 composable 存在的主要理由：不回第 1 页的话，用户在第 5 页改筛选，
   *   后端返回空集，界面显示"没有数据"，而其实第 1 页有 3 条 —— 用户会以为
   *   筛选条件写错了或者数据丢了。
   */
  function applyFilter(patch: Partial<Record<keyof TQuery, QueryValue>>): void {
    // `as TQuery`：patch 的值类型是 QueryValue（含 undefined，用于"清空筛选"），
    // 与 TQuery 的具体字段类型不完全重合 —— 这是"部分更新"的固有代价，
    // 换来的是调用点不必为"清空某个筛选"单独开一个方法。
    query.value = { ...query.value, ...patch, page: 1 } as TQuery
    void reload()
  }

  /** 只翻页，不动筛选条件（页码保留）。 */
  function changePage(page: number): void {
    query.value = { ...query.value, page }
    void reload()
  }

  /** 改每页条数 —— 同样回第 1 页（条数变了，原页码可能越界）。 */
  function changeSize(size: number): void {
    query.value = { ...query.value, size, page: 1 }
    void reload()
  }

  async function reload(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const result = await options.fetch(query.value)
      items.value = result.items
      total.value = result.total
    } catch (caught) {
      // ⚠️ 只保留 ApiError：别的异常（网络栈崩了、代码 bug）不该被显示成
      //    "加载失败"，否则一个编程错误会被伪装成网络问题、排查方向整个跑偏。
      //    直接往上抛，交给全局兜底。
      if (!(caught instanceof ApiError)) throw caught
      error.value = caught
      // 失败时**保留旧数据**：清空的话用户会以为数据被删了
    } finally {
      loading.value = false
    }
  }

  const isEmpty = computed(() => !loading.value && error.value === null && items.value.length === 0)
  const hasError = computed(() => error.value !== null)

  /**
   * 返回值用 `reactive` 包一层，这样**嵌套的 ref 会被自动解包**。
   *
   * ⚠️ 模板只对 setup 的**顶层**绑定做 ref 解包；返回一个含 ref 的普通对象时，
   *    模板里必须写 `list.items.value` —— 而 `list.items.value = ...` 在模板里
   *    是**只读**的（解包后拿到的是数组本身），于是"表格勾选要回写数据"这类需求
   *    会莫名其妙不生效。`reactive` 消掉这层坑，模板与脚本都能写 `list.items`。
   */
  return reactive({
    applyFilter,
    changePage,
    changeSize,
    error,
    hasError,
    isEmpty,
    items,
    loading,
    query,
    reload,
    total,
  })
}
