/**
 * E2E 全局准备：跑 API seed（docs/10 §4「数据通过 API seed 准备，不通过 UI 造数据」）。
 *
 * ## 为什么用 `spawnSync` 调 Python 而不是 HTTP 调后端的 seed 接口
 *
 * - 后端**没有**「造测试数据」的接口（也不该有：那是一条任何人能往生产库灌数据的路）。
 * - 种子脚本走 service 层，权限校验、状态机、乐观锁全都在，跑得通说明数据合法。
 * - 脚本已经幂等：重复执行只把 E2E 那批数据复位。
 *
 * ⚠️ **失败必须让整轮测试失败**：静默跳过的话，E2E 会「全绿」而库里一行数据都没有 ——
 *    那是最坏的一种假绿（用例真的跑了，只是全在空数据上断言）。
 */
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const BACKEND = resolve(HERE, '../../backend')

/** 没给连接串就直接失败 —— 猜一个库名然后连到别人的数据上，比失败危险得多。 */
function required(name: string): string {
  const value = process.env[name]
  if (value === undefined || value === '') {
    throw new Error(
      `[e2e] 缺少环境变量 ${name}。E2E 必须指向**独立库**（docs/10 §4）：\n` +
        `  export ${name}=postgresql+asyncpg://erp_app:<pwd>@127.0.0.1:5432/garment_erp_e2e`,
    )
  }
  return value
}

export default function globalSetup(): void {
  const python = resolve(BACKEND, '.venv/bin/python')
  if (!existsSync(python)) {
    throw new Error(`[e2e] 找不到 ${python}：先在 backend/ 装好虚拟环境（uv sync）`)
  }

  const url = required('E2E_DATABASE_URL')
  const result = spawnSync(python, ['-m', 'tests.e2e_seed'], {
    cwd: BACKEND,
    encoding: 'utf-8',
    // ⚠️ 种子脚本要 `DATABASE_URL_MIGRATION`（基线初始化走迁移账号），
    //    不传的话它会跳过基线并报「已有 N 个账号」之类的含糊错误
    env: { ...process.env, DATABASE_URL_MIGRATION: process.env.DATABASE_URL_MIGRATION ?? url },
  })

  if (result.status !== 0) {
    throw new Error(
      `[e2e] 数据种子失败（exit ${result.status}）：\n${result.stdout ?? ''}\n${result.stderr ?? ''}`,
    )
  }
  // ⚠️ `console.log` 在这里是**对的**：这是 CLI 的人机界面，不是业务代码
  //   （docs/03 §1.1 第 10 条禁的是业务代码里的 print）。E2E 的日志要让人
  //   一眼看出种子跑没跑成功 —— 失败时 CI 日志里那行「种子完成」是唯一的线索。
  process.stdout.write(`[e2e] ${result.stdout?.trim().split('\n').at(-1) ?? '种子完成'}\n`)
}
