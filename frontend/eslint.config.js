// ESLint flat config —— 工作区根配置，各 package 的 eslint.config.js 用 `export default [...]` 追加。
// 规则取向（docs/03-代码规范.md §2.1）：
//   1. 禁 any：未知类型用 unknown + 收窄
//   2. 无 console 残留
//   3. 组件内禁止硬编码颜色/间距（用 design token，见 docs/06 §1）
import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import vue from 'eslint-plugin-vue'
import vueParser from 'vue-eslint-parser'

export default tseslint.config(
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      '**/*.d.ts',
      '**/coverage/**',
      '**/playwright-report/**',
      '**/test-results/**',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: ['.vue'],
      },
    },
    rules: {
      // 单文件组件统一 <script setup lang="ts">，禁止 Options API（docs/03 §2.1）
      'vue/options-api': 'error',
      'vue/block-order': ['error', { order: ['script', 'template', 'style'] }],
    },
  },
  {
    // ⚠️ 构建脚本跑在 **Node**（跑在浏览器的东西有 `window`，这里反过来）。
    //    不声明 globals 的话 `process` 会报 no-undef —— 而它确实是 Node 提供的，
    //    不是"忘了导入"。
    files: ['**/*.mjs', '**/*.cjs', '**/scripts/**'],
    languageOptions: {
      globals: {
        process: 'readonly',
        console: 'readonly',
        URL: 'readonly',
        fetch: 'readonly',
        globalThis: 'readonly',
      },
    },
  },
  {
    rules: {
      // 提交前 lint 拦截 console 残留（docs/03 §2.1 第 10 条）
      'no-console': 'error',
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      eqeqeq: ['error', 'always'],
      'prefer-const': 'error',
      'object-shorthand': 'error',
    },
  },
)
