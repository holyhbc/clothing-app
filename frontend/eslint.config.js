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
