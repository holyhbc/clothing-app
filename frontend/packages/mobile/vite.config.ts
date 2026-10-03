import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// ⚠️ `base` 必须是 `/mobile/`：生产镜像把本包的产物放在 nginx 的
// `/usr/share/nginx/mobile` 下（docker/frontend/Dockerfile 的 runtime 阶段），
// nginx 用 `location /mobile/` 别名暴露（docs/11 §4）。
// 写成 `./` 的话资源路径会变成 `/assets/...`，与 PC 端主站的资源重名冲突。
export default defineConfig({
  base: '/mobile/',
  plugins: [vue()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
