import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { adminSrc, repoRoot } from '@/test-support/paths'

/**
 * design token 守卫（TC-W18）。
 *
 * ## 为什么 token 要比对**文档**而不是比对自身
 *
 * 「tokens.css 里的每个 token 都有值」这种检查是 vacuous 的 —— 它永远通过。
 * 真正会出事的是**漂移**：某次改版顺手把 `--color-primary` 改成 `#1668de`，
 * 只改了 css 没改 docs/06 §1。于是规范与实现不一致，而下一个 AI 照文档写出来的
 * 组件颜色和现有组件对不上 —— 而且**没有任何东西会报错**。
 *
 * 所以这里拿 `docs/06-前端与UI规范.md` §1 的 CSS 块当权威，逐项双向比对。
 * 和 `backend/tests/modules/test_permission_registry.py` 里「权限点三处一致性」
 * 是同一个思路：把"靠人记住的约定"变成闸门会失败的事实。
 */

// 路径解析见 `@/test-support/paths`（不能用 `import.meta.url`：vitest 的 jsdom
// 环境里它是 http 形式的 URL，`fileURLToPath` 会抛 "must be of scheme file"）
const DOC_06 = join(repoRoot(), 'docs', '06-前端与UI规范.md')
const SRC_ROOT = adminSrc()
const TOKENS_CSS = join(SRC_ROOT, 'styles', 'tokens.css')

/** 抽 `--name: value` 里的 name。 */
function tokenNames(text: string): Set<string> {
  return new Set([...text.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((match) => match[1] ?? ''))
}

function docTokenBlock(): string {
  const doc = readFileSync(DOC_06, 'utf8')
  // §1 的 CSS 块紧跟在 tokens.css 的路径说明之后
  const match = /tokens\.css`：\s*```css\n([\s\S]*?)```/.exec(doc)
  if (match?.[1] === undefined) {
    throw new Error(`在 ${DOC_06} 里找不到 §1 的 tokens.css 代码块 —— 规范结构变了？`)
  }
  return match[1]
}

describe('design token', () => {
  it('TC-W18 tokens.css 的 key 集合与 docs/06 §1 完全一致', () => {
    const expected = tokenNames(docTokenBlock())
    const actual = tokenNames(readFileSync(TOKENS_CSS, 'utf8'))

    expect(actual.size).toBeGreaterThan(0)
    expect([...actual].filter((name) => !expected.has(name))).toEqual([])
    expect([...expected].filter((name) => !actual.has(name))).toEqual([])
  })

  it('docs/06 §1 的代码块本身存在且不是空的（否则上一个用例会假绿）', () => {
    expect(tokenNames(docTokenBlock()).size).toBeGreaterThan(30)
  })

  it('组件与全局样式里没有硬编码颜色（docs/06 §1「禁止字面量」）', () => {
    interface Violation {
      readonly file: string
      readonly text: string
    }
    const violations: Violation[] = []

    // 只扫源码；tokens.css 本身就是 token 的**定义处**，那里当然全是字面量
    const offenders: string[] = []
    const walk = (dir: string): void => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = `${dir}/${entry.name}`
        if (entry.isDirectory()) {
          walk(full)
          continue
        }
        if (/\.(ts|vue|css)$/.test(entry.name) && !/\.test\.ts$/.test(entry.name)) {
          offenders.push(full)
        }
      }
    }
    walk(join(SRC_ROOT, 'components'))
    walk(join(SRC_ROOT, 'views'))
    walk(join(SRC_ROOT, 'layouts'))
    walk(join(SRC_ROOT, 'styles'))

    // 十六进制颜色 / rgb() / 裸 rgba()
    const colorPattern = /#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(/
    for (const file of offenders) {
      const base = file.slice(SRC_ROOT.length).replace(/^\//, '')
      // tokens.css 是 token 的**定义处**，那里当然全是字面量
      if (base === 'styles/tokens.css') continue
      const text = readFileSync(file, 'utf8')
      text.split('\n').forEach((line, index) => {
        if (colorPattern.test(line)) {
          violations.push({ file: `${base}:${index + 1}`, text: line.trim() })
        }
      })
    }

    expect(violations.map((v) => `${v.file} → ${v.text}`)).toEqual([])
  })
})
