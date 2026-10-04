/**
 * PC 管理端入口。
 *
 * 装配顺序有讲究：Pinia → 路由 → 全局钩子 → 挂载。
 * 钩子必须在**第一次导航之前**注册好，否则 `restore()` 失败触发的
 * `onAuthExpired` 找不到处理器，用户被留在一个既没登录态也没跳转的白屏上。
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { message } from 'ant-design-vue'
import App from './App.vue'
import { createAppRouter } from '@/router'
import { permission } from '@/directives/permission'
import { registerAuthExpiredHandler, registerErrorHandler } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import './styles/tokens.css'
import './styles/global.css'

const pinia = createPinia()
const app = createApp(App)
const router = createAppRouter()

app.use(pinia)
app.use(router)
app.directive('can', permission)

// 登录态彻底失效：清本地 + 回登录页。redirect 指向当前页，
// 这样 token 过期打断的操作在重新登录后能接着做。
registerAuthExpiredHandler(() => {
  useAuthStore(pinia).clear()
  const current = router.currentRoute.value
  const target =
    current.name === 'login'
      ? { name: 'login' }
      : { name: 'login', query: { redirect: current.fullPath } }
  void router.replace(target)
})

// docs/06 §5：网络/业务错误的全局提示。⚠️ 带上 request_id —— 用户报障时报这串号
// 才能在后端日志里定位到那一次请求；只显示 "500 Internal Server Error" 是违规的。
registerErrorHandler(({ message: text, level, requestId }) => {
  const suffix = requestId === null ? '' : `（编号 ${requestId}）`
  void (level === 'error' ? message.error(text + suffix) : message.warning(text + suffix))
})

app.mount('#app')
