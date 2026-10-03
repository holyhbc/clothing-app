import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

/**
 * 单测环境用 **jsdom**：守卫与 `v-can` 都要真实的 DOM（元素插入、样式、事件），
 * node 环境跑不了。
 *
 * ⚠️ alias 必须与 `vite.config.ts` **完全一致** —— 否则单测里解析到的是另一份
 * 模块实例（症状是 `setActivePinia` 之后 store 还是 undefined）。
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@garment/shared': fileURLToPath(new URL('../shared/src/index.ts', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    setupFiles: ['src/test-setup.ts'],
    // 不开 globals：测试里显式 `import { describe, it, expect } from 'vitest'`，
    // 免得全局污染让「忘记 import」这类错误要到运行时才暴露
    globals: false,
  },
})
