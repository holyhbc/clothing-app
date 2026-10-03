import { defineConfig } from 'vitest/config'

// ⚠️ **不引入 setup 文件**：本包只有纯 TS（无 Vue、无浏览器组件），
// 单测跑在 node 环境，靠 fake timers / mock fetch 就够了。
// 少一层间接，出错时栈顶就是出问题的那行。
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      // ⚠️ 排除**没有运行时逻辑**的文件，否则覆盖率被它们拉到不真实的低：
      //   - `src/index.ts`      纯 barrel（再导出），业务逻辑一行没有
      //   - `src/types/**`     纯 `export type`，编译后是空文件
      //   - `src/enums/permissions.ts` 生成物，只有常量数据；
      //     它的正确性由后端 `test_frontend_constants_match_registry` 断言
      //     （INV-P0-4），那边能看到"少了哪个权限点"，这边看不到
      include: ['src/**/*.ts'],
      exclude: [
        'src/index.ts',
        'src/types/**',
        'src/enums/permissions.ts',
        '**/*.test.ts',
        '**/*.d.ts',
        'vitest.config.ts',
      ],
      thresholds: {
        // 任务卡验收：shared 覆盖 ≥ 90%
        statements: 90,
        branches: 90,
        functions: 90,
        lines: 90,
      },
    },
    // ⚠️ 测试里读 `sessionStorage`：node 环境没有它，用内存替身注入全局，
    // 见 src/api/__tests__ 里的 storageStub。**不做全局 setup** 是因为
    // 「一个测试改动全局、另一个测试被影响」这种串味极难排查。
  },
})
