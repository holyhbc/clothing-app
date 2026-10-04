/**
 * 防抖（docs/06 §10「输入 300ms 防抖」）。
 *
 * ## 为什么自己写而不是直接用 lodash-es
 *
 * 为了一个 20 行的函数引入 `lodash-es`（还要为它处理 `esModuleInterop` 与按需引入）不划算。
 * 但**计时逻辑必须抽出来单独测**：防抖写错的后果是"每敲一个字发一次请求"（候选接口
 * 走 `pg_trgm` 索引，高频打会打满 IO），或者反过来"改了字永远不生效"。
 * 抽成 composable 后可以用假定时器精确断言。
 */

/**
 * 返回一个防抖后的函数。
 *
 * @param fn 真正要执行的函数
 * @param wait 静默等待毫秒数
 *
 * @example
 * const debouncedSearch = useDebounce((keyword: string) => search(keyword), 300)
 */
export function useDebounce<A extends unknown[]>(fn: (...args: A) => void, wait: number) {
  let timer: number | undefined

  function debounced(...args: A): void {
    if (timer !== undefined) window.clearTimeout(timer)
    timer = window.setTimeout(() => {
      timer = undefined
      fn(...args)
    }, wait)
  }

  /**
   * 取消待执行的调用。
   *
   * ⚠️ 组件卸载时**必须**调用：否则用户刚输入就切了页面，300ms 后定时器照常触发，
   * 对一个已卸载的组件发请求 —— 在路由切换频繁的 PC 端会攒出可观的无效请求。
   */
  function cancel(): void {
    if (timer !== undefined) {
      window.clearTimeout(timer)
      timer = undefined
    }
  }

  return { debounced, cancel }
}
