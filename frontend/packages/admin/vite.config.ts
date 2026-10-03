import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

/**
 * PC 管理端构建配置。
 *
 * `base` 必须是 `/`：runtime 阶段 `COPY --from=builder /build/packages/admin/dist
 * /usr/share/nginx/html`，产物挂在站点根（nginx `location /`）。
 * 对照 `packages/mobile` 的 `/mobile/` —— 写错会让资源路径与另一端重名。
 */
export default defineConfig({
  base: '/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // ⚠️ 显式指到 **源码** 而不是走 node_modules：`@garment/shared` 的 exports
      // 指向 `./src/index.ts`（它只发类型与纯 TS，不产出 dist）。交给 Vite 的
      // 依赖预构建去处理 node_modules 里的裸 .ts 会在换 Vite 版本时炸，
      // 而且报错信息与真实原因毫无关系。
      '@garment/shared': fileURLToPath(new URL('../shared/src/index.ts', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 开发期不跨域：`/api` 反代到后端，浏览器看到的仍是同源地址，
      // 于是 refresh token 的 HttpOnly Cookie（docs/07 §1.1，`samesite=lax`）
      // 会被 fetch 的默认 `credentials: 'same-origin'` 带上 —— 不需要
      // `credentials: 'include'`，也就不需要动 shared 的请求层。
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 单据型 ERP 首屏要拉 antd 整包，chunk 偏大；给个显式告警线而不是默默变大
    chunkSizeWarningLimit: 1200,
  },
})
