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
      // 单文件组件统一 <script setup lang="ts">，禁止 Options API（docs/03 §2.1）。
      // ⚠️ 规则名是 `component-api-style`，**没有** `vue/options-api` 这个规则 ——
      //    eslint-plugin-vue 9.33 的规则表里查不到它。而 ESLint 只在**有文件命中
      //    `files: ['**/*.vue']` 时才校验这一段配置**，所以在 T-WEB-001 之前
      //    （仓库里一个 .vue 文件都没有）这个错引用一直没暴露：pnpm lint 照样绿。
      //    第一个 .vue 文件出现的那一刻，`pnpm lint` 直接崩在配置校验上。
      'vue/component-api-style': ['error', ['script-setup']],
      'vue/block-order': ['error', { order: ['script', 'template', 'style'] }],
      // ⚠️ 这两条与 Prettier **直接冲突**（eslint-plugin-vue 官方也建议用 Prettier
      //    时关掉它们）：`--fix` 把属性拆成多行，Prettier 又给它并回一行，
      //    于是每次提交都产生一整片互相撤销的改动，真正的告警被淹在噪声里。
      //    排版归 Prettier，eslint 只管语义。
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
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
