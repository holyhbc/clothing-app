/**
 * 单测环境补丁。
 *
 * jsdom 声明了 `window.scrollTo` 但**没有实现**（调用它会在 stderr 打一行
 * `Not implemented: Window's scrollTo() method`）。vue-router 的 `scrollBehavior`
 * 会调它，于是每个跑守卫的用例都带一行这样的噪声 —— 噪声多了真报错就被淹掉，
 * 所以这里补一个空实现。
 *
 * 只在测试环境生效（由 `vitest.config.ts` 的 `setupFiles` 引入）。
 */

Object.defineProperty(window, 'scrollTo', {
  configurable: true,
  value: () => undefined,
  writable: true,
})
