/**
 * 单测环境补丁。
 *
 * jsdom 缺的东西按需补，**每一条都要写清为什么**，否则过两年没人敢删也不敢再加。
 * 只在测试环境生效（由 `vitest.config.ts` 的 `setupFiles` 引入）。
 */

/**
 * ① `window.scrollTo`：jsdom 声明了但没实现（调用它会在 stderr 打一行
 * `Not implemented: Window's scrollTo() method`）。vue-router 的 `scrollBehavior`
 * 会调它，于是每个跑守卫的用例都带一行这样的噪声 —— 噪声多了真报错就被淹掉。
 */
Object.defineProperty(window, 'scrollTo', {
  configurable: true,
  value: () => undefined,
  writable: true,
})

/**
 * ② `window.matchMedia`：Ant Design Vue 的 `Grid.useBreakpoint` 在组件 setup 阶段
 * 就会调它，jsdom 完全没这个 API，于是挂载任何用 `<Row>/<Col>` 或间接用到
 * 响应式栅格的组件都会 `TypeError: window.matchMedia is not a function`。
 *
 * 固定返回「宽屏」：`matches: false` 让组件走 desktop 分支。本仓库的验收分辨率
 * 是 1366×768 与 1920×1080，响应式行为由 Playwright（E2E）覆盖，不靠这个 shim。
 */
Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
})

/** ③ `ResizeObserver`：antd 的 Sider / Table 自适应宽度会构造它，jsdom 没有。 */
class NoopResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver
