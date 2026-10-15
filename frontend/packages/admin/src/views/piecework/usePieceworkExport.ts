/**
 * 计件流水台账的 CSV 导出（从 `List.vue` 抽出，ADR-0030「单文件 ≤ 400 行」）。
 *
 * ⚠️ **不复用 `useExport`**：它把扩展名写死成 `.xlsx`、且要求 `exportXlsx` 签名，
 *    而计件导出后端给的是 CSV。这里只复用它的两个纯函数
 *    `triggerBlobDownload` / `buildExportFilename`（docs/05 §9.1）。
 */
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { exportPieceworkLogs } from '@/api/piecework'
import type { PieceworkLogQuery } from '@/api/piecework'
import { buildExportFilename, triggerBlobDownload } from '@/composables/useExport'

export function usePieceworkExport(currentQuery: () => PieceworkLogQuery) {
  const exporting = ref(false)

  async function onExport(): Promise<void> {
    if (exporting.value) return
    exporting.value = true
    try {
      const query = currentQuery()
      const result = await exportPieceworkLogs(query)
      const filename = buildExportFilename('计件流水', query).replace(/\.xlsx$/, '.csv')
      triggerBlobDownload(result.blob, result.filename ?? filename)
      void message.success(result.rowCount === null ? '已导出' : `已导出 ${result.rowCount} 行`)
    } catch (caught) {
      // 全局报错已由 `ApiClient.onError` 报过一次（06 §5）；只在没有业务文案时补一句
      const text = caught instanceof Error ? caught.message : ''
      if (text === '' || /^\d{3}$/.test(text)) void message.error('导出失败，请稍后重试')
    } finally {
      exporting.value = false
    }
  }

  return { exporting, onExport }
}
