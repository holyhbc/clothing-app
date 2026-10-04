import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 类型出口的一致性守卫（T-WEB-006 写 `api/styles.ts` 时真的踩到了）。
 *
 * ## 踩到的坑
 *
 * `packages/shared/src/types/index.ts` 里加了 `StyleOut`，忘了在 `src/index.ts` 的
 * 再导出清单里加一行 —— 于是 `import type { StyleOut } from '@garment/shared'` 报
 * **`has no exported member 'StyleOut'`**。而这个报错的**方向是错的**：它让人以为
 * "`StyleOut` 没在 schema 里生成"，于是去查生成器、查 openapi.json、查
 * `generate:api` 幂等性 —— 全都正常，因为问题只是**出口漏了一行**。
 *
 * 漏一个类型的代价还不止于此：docs/06 §7 说「页面**只能**从 `@garment/shared` 导入，
 * 不要深入子路径」，所以页面为了绕过它就得深入 `@garment/shared/types` —— 那个
 * "规则"是随手写的还是有意为之，从此没人知道了。
 *
 * 所以这里断言：`types/index.ts` 的**每一个** `export type` 都出现在 `index.ts` 的
 * 再导出清单里。少一行就红，且报错直接指向缺的那个名字。
 */

const SHARED_SRC = resolve(dirname(fileURLToPath(import.meta.url)), '..')

function readTypesSource(): string {
  return readFileSync(resolve(SHARED_SRC, 'types/index.ts'), 'utf8')
}

function readIndexSource(): string {
  return readFileSync(resolve(SHARED_SRC, 'index.ts'), 'utf8')
}

/** `types/index.ts` 里导出的类型名（跳过 `export type {` 那种再导出形式）。 */
function declaredTypeNames(): string[] {
  const source = readTypesSource()
  return [...source.matchAll(/^export type ([A-Za-z]\w*)\s*=/gm)].map((match) => match[1] ?? '')
}

/** `index.ts` 的 `export type { ... } from './types/index.ts'` 清单里的名字。 */
function reexportedTypeNames(): string[] {
  const source = readIndexSource()
  const match = /export type \{([^}]*)\} from '\.\/types\/index\.ts'/.exec(source)
  if (match === null)
    throw new Error("index.ts 里找不到 `export type { … } from './types/index.ts'`")
  return (match[1] ?? '')
    .split(',')
    .map((item) => item.trim())
    .filter((item) => /^[A-Za-z]\w*$/.test(item))
}

describe('shared 的类型出口', () => {
  it('types/index.ts 声明的每个类型都在 index.ts 再导出了', () => {
    const declared = declaredTypeNames()
    expect(declared.length).toBeGreaterThan(30)

    const missing = declared.filter((name) => !reexportedTypeNames().includes(name))
    expect(
      missing,
      `这些类型在 types/index.ts 里导出了但没在 index.ts 再导出 —— ` +
        `页面 import '@garment/shared' 时会报「has no exported member」，` +
        `而那个报错方向是错的（会让人怀疑是生成物没生成）：${missing.join('、')}`,
    ).toEqual([])
  })

  it('⚠️ 生成器产出的枚举/常量也要在 index.ts 出口（否则同样报「没有导出」）', () => {
    // 这三个是生成物：`permissions.ts` / `status.ts` / `baseDictFields.ts`。
    // 生成器新增导出时最容易忘记在 index.ts 加一行 —— 上一个坑就是这么来的。
    const source = readIndexSource()
    for (const name of ['PERM', 'PERMISSION_NAMES', 'DOCUMENT_STATUSES', 'BASE_DICT_CONTRACT']) {
      expect(source, `${name} 没在 index.ts 出口`).toContain(name)
    }
  })
})
