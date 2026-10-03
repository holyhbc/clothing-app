/**
 * 员工端 H5 入口 —— **占位**，页面在 P2 实现（docs/06 §3.1 的 5 个页面）。
 *
 * ⚠️ 本包存在的唯一理由：`docker/frontend/Dockerfile` 的 runtime 阶段有一行
 * `COPY --from=builder /build/packages/mobile/dist /usr/share/nginx/mobile`，
 * 不建这个包则**闸门 5 的 web 镜像必然构建失败**（T-INFRA-002 TC-011 已实测确认）。
 * 它不是"提前把页面写了"，而是为了让流水线能跑通。
 */
import { createApp, h } from 'vue'

const app = createApp({
  name: 'MobilePlaceholder',
  render: () =>
    h('main', { style: 'padding:24px;font:14px/1.6 system-ui,sans-serif;color:#1d2129' }, [
      h('h1', { style: 'font-size:18px;margin:0 0 8px' }, '员工端'),
      h(
        'p',
        { style: 'margin:0;color:#4e5969' },
        '页面在 P2（扫码计件）阶段交付。构建产物已就绪，供 nginx /mobile/ 路由使用。',
      ),
    ]),
})

app.mount('#app')
