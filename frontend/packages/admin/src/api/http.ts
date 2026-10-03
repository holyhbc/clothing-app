/**
 * 请求层单例（docs/03 §2.1 第 5 条：所有请求走 `api/` 层封装）。
 *
 * ## 为什么 hooks 用「注册」而不是直接引用
 *
 * 请求层需要在「登录态失效」时做两件事：清 store、跳 `/login`。而 store 和 router
 * 都依赖请求层（登录、拉 `me`），于是最自然的写法会形成环：
 *
 * ```
 * api/http → stores/auth → api/http
 * api/http → router     → stores/auth → api/http
 * ```
 *
 * ESM 能跑通这种环（引用都在函数体内，模块求值期不触发），但它把初始化顺序变成
 * 隐式约束：谁先被 import 谁就得先把自己的导出准备好，加一个模块就可能变成
 * "undefined is not a function"，而且报错完全指不到真正的原因。
 *
 * 所以这里只暴露注册函数，`main.ts` / `router` 在装配时把实现塞进来。shared 的
 * `ApiClient` 本来就是 hooks 注入式的（`ClientOptions.hooks`），这里只是把最后一环
 * 也交给宿主。
 */
import { ApiClient } from '@garment/shared'
import type { RefreshResponse } from '@garment/shared'

export type ErrorHandler = (info: {
  message: string
  level: 'warn' | 'error'
  requestId: string | null
}) => void

export type AuthExpiredHandler = () => void

let errorHandler: ErrorHandler | null = null
let authExpiredHandler: AuthExpiredHandler | null = null

/** 注册全局错误提示（`main.ts` 里接 Ant Design Vue 的 `message`）。 */
export function registerErrorHandler(handler: ErrorHandler): void {
  errorHandler = handler
}

/** 注册「登录态彻底失效」的处理（清 store + 跳登录）。 */
export function registerAuthExpiredHandler(handler: AuthExpiredHandler): void {
  authExpiredHandler = handler
}

/** 仅供测试：清掉已注册的 handler，避免用例之间互相污染。 */
export function resetHandlers(): void {
  errorHandler = null
  authExpiredHandler = null
}

// ⚠️ 必须显式标注 `: ApiClient`。`refresh` 钩子里引用了 `http` 自己
//    （钩子是在实例化之后才被调用的，运行时没问题），但 TS 顺着这个自引用
//    推不出类型，直接报 TS7022 "implicitly has type 'any'" 连带 TS2347。
//    标注之后类型从声明处确定，不再需要推断。
export const http: ApiClient = new ApiClient({
  baseURL: '/api/v1',
  hooks: {
    /**
     * 用 HttpOnly Cookie 里的 refresh token 换新的 access token（docs/07 §1.1）。
     *
     * - `auth: false`：这次请求**没有** access token 可带，带了反而会被判成
     *   "拿过期 token 来刷新"（真实原因被掩盖成一条看不懂的 401）。
     * - 不手动设 `credentials`：Cookie 是 `samesite=lax`，而 `/api` 同源
     *   （生产走 nginx 反代，开发走 vite 代理），fetch 默认的
     *   `credentials: 'same-origin'` 就够了。
     * - 返回 `null` 而不是抛错：`ApiClient` 约定"刷新失败 → 跳登录"，让失败走
     *   统一分支，不要在这里弹两次提示。
     */
    refresh: async () => {
      try {
        // ⚠️ `post(path, body, options)` —— 选项在**第三个**参数。写成第二参数的话
        // `{auth:false}` 会被当成请求体发出去，接口收到一个莫名其妙的 JSON 对象。
        const result = await http.post<RefreshResponse>('/auth/refresh', undefined, {
          auth: false,
        })
        http.setAccessToken(result.access_token)
        return result.access_token
      } catch {
        return null
      }
    },
    onAuthExpired: () => {
      authExpiredHandler?.()
    },
    onError: (info) => {
      errorHandler?.(info)
    },
  },
})
