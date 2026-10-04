/**
 * 测试专用的路径解析。
 *
 * ⚠️ 为什么不能用 `import.meta.url`：vitest 的 jsdom 环境里它是 http 形式的 URL，
 *    `fileURLToPath` 直接抛 `TypeError: The URL must be of scheme file`。
 *    所以从 `cwd` 逐级上溯找仓库根 —— 这样无论从仓库根还是包目录跑测试都能定位。
 *
 * 只被 `*.test.ts` 引用，不进生产构建。
 */
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

const MARKER = join('docs', '06-前端与UI规范.md')

/** 仓库根目录（含 `docs/`）。 */
export function repoRoot(): string {
  let dir = resolve(process.cwd())
  for (;;) {
    if (existsSync(join(dir, MARKER))) return dir
    const parent = dirname(dir)
    if (parent === dir) {
      throw new Error(`从 ${process.cwd()} 往上找不到仓库根（缺 ${MARKER}）`)
    }
    dir = parent
  }
}

/** `packages/admin/src` 目录。 */
export function adminSrc(): string {
  return join(repoRoot(), 'frontend', 'packages', 'admin', 'src')
}
