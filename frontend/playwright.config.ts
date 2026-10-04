import { defineConfig, devices } from '@playwright/test'

/**
 * E2E 配置（docs/10 §4）。
 *
 * ## 三个决定，都是踩过坑之后的
 *
 * 1. **`webServer` 起 vite 而不是假设外面已经起着**：
 *    手工起服务时最常见的失败是「端口已被上次那个残留进程占着」，
 *    表现为 E2E 莫名其妙地连到一个**旧版本**的前端上跑 —— 用例过了，功能却是坏的。
 *    交给 Playwright 起服务 + 换端口，进程生命周期跟着测试走。
 * 2. **`reuseExistingServer: !process.env.CI`**：本地开发时复用已有服务（改一行
 *    不用重启 3 秒），CI 上强制新起（容器里没有「已有服务」这回事）。
 * 3. **`trace: 'on-first-retry'` 而不是常开**：常开会让每轮 CI 多传几十 MB 制品；
 *    `retain-on-failure` 让**失败**那次的 trace 留下来，够定位了。
 *
 * ⚠️ **数据经 API seed 准备，不通过 UI 造数据**（docs/10 §4）：`globalSetup`
 *    跑 `backend/tests/e2e_seed.py`。用 UI 造数据的话，失败时分不清是业务逻辑错
 *    还是上一条用例没点完。
 *
 * ⚠️ **`fullyParallel: false`**：E2E 库是**共用**的（`garment_erp_e2e`），
 *    并行跑会互相看不见对方的数据 —— docs/10 §4 写的是「每个 E2E 用独立库可并行」，
 *    那要等到用例数量上来、值得为每条用例建库时才做；现在一条主流程，串行更省事。
 */
const E2E_PORT = 5199
const API_PORT = 8099

/**
 * E2E 数据库必须是**独立库**。
 *
 * ⚠️ 不设默认值：要连哪台 PG、哪个库、什么口令，取决于本机/容器怎么起的，
 *   猜一个默认值的后果是「E2E 静默跑在开发库上」—— 那会往开发库里写测试数据，
 *   而测试全绿。看 `.env.example` 的 E2E 段。
 */

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // ⚠️ 30 秒是「等后端起来」的上限。Vue 首次编译整个 antd 包实测 8~12 秒，
  //    给 30 秒在 CI 的冷缓存下够用；再短就会偶发失败，而偶发失败比失败更贵
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: `http://127.0.0.1:${E2E_PORT}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // ⚠️ 录像只在**没有** `E2E_CHROMIUM_PATH` 时开：录像要 ffmpeg，而这个逃生口场景下
    //    （arm64 + Ubuntu 20.04）本机缓存里的 ffmpeg 版本与 Playwright 期望的对不上，
    //    于是每条用例都报「Executable doesn't exist ... ffmpeg」而一条都没跑。
    //    trace + 截图已经够定位了，录像只是锦上添花。
    ...(process.env.E2E_CHROMIUM_PATH === undefined ? { video: 'retain-on-failure' as const } : {}),
    // ⚠️ 固定 1366×768：docs/06 §2.1 定的验收分辨率，响应式另有验收分辨率
    viewport: { width: 1366, height: 768 },
    actionTimeout: 10_000,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // ⚠️ `E2E_CHROMIUM_PATH` 是**给本机准备的逃生口**：这台开发机是
        //    Ubuntu 20.04 / arm64，Playwright 官方**不提供**该组合的二进制
        //    （`playwright install` 直接报 "does not support chromium on
        //    ubuntu20.04-arm64"），而缓存里恰好有一份别的版本装下的 Chromium。
        //    指向它就能在本机跑；CI 上不设这个变量，用 Playwright 自带的。
        //
        // ⚠️ 用 `headless_shell: false`：缓存里只有完整 `chrome`，
        //    没有 `chrome-headless-shell`。
        ...(process.env.E2E_CHROMIUM_PATH === undefined
          ? {}
          : {
              launchOptions: {
                executablePath: process.env.E2E_CHROMIUM_PATH,
              },
            }),
      },
    },
  ],
  globalSetup: './e2e/global-setup.ts',
  webServer: [
    {
      // ⚠️ 后端**先起**：vite 的 proxy 指向它，而 Playwright 是**并发**等这两个
      //   webServer 的 —— 谁先就绪谁先放行，前端先就绪时第一条用例会在代理
      //   连接被拒上失败，且报错是 `ECONNREFUSED`，看不出是后端还没起
      command: `sh e2e/start-backend.sh ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/v1/readyz`,
      reuseExistingServer: !process.env.CI,
      timeout: 90_000,
      stdout: 'pipe',
    },
    {
      command: `pnpm --filter @garment/admin dev --port ${E2E_PORT} --strictPort`,
      url: `http://127.0.0.1:${E2E_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: 'pipe',
      // ⚠️ proxy 目标要用 `VITE_` 前缀的环境变量注入：vite.config.ts 里读的是
      //    `VITE_API_PROXY_TARGET`。不传的话它会用默认的 8000 端口 ——
      //    而那里可能正跑着开发用的后端，E2E 就会对着**错误的库**跑
      env: { VITE_API_PROXY_TARGET: `http://127.0.0.1:${API_PORT}` },
    },
  ],
})

export { API_PORT, E2E_PORT }
