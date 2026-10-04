/**
 * 导出（docs/05 §9.1；docs/06 §2.2「导出按钮与新建相邻」）。
 *
 * ## 三条必须由这里统一处理的规则
 *
 * 1. **文件名带筛选摘要与时间戳**：`{资源}_{筛选摘要}_{YYYYMMDD_HHmm}.xlsx`。
 *    只叫 `导出.xlsx` 的话，浏览器第二次下载会变成 `导出(1).xlsx`，用户根本分不清
 *    哪个才是刚才那份 —— 而导出是要拿去对账的。
 * 2. **筛选条件原样传过去**：导出与列表**共用**后端同一个 service 方法
 *    （docs/07 §3.2 铁律 3），所以前端只要把当前 `query` 原样传过去，
 *    「导出行数 == 列表 total」是结构上成立的，而不是靠两边记得同步。
 * 3. **回显实际导出行数**：后端在 `X-Row-Count` 里给了行数，界面报「已导出 N 行」。
 *    导出的行数与用户看到的列表对不上时，这句话是唯一的现场证据。
 *
 * ⚠️ 为什么要 revoke object URL：`URL.createObjectURL` 的 blob 一直挂在
 *    document 上，不 revoke 的话每次导出泄一份 xlsx，导十几次就是几十 MB 挂在
 *    页面上，标签页怎么关都回收不了。
 */
import { reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { formatCompactStamp } from '@garment/shared'
import type { DownloadResult } from '@garment/shared'
import type { BaseListQuery, BaseResourceApi } from '@/api/base'

export interface UseExportOptions {
  /** 中文资源名（进文件名与提示）。 */
  resourceLabel: string
  /**
   * 只要求有 `exportXlsx(query)` —— **不是**整个 `BaseResourceApi`。
   *
   * ⚠️ 别把类型写成 `BaseResourceApi`：款号这一族不是九个基础资料那种同构资源，
   *    走的是 `@/api/styles` 里手写的封装（6 种不同语义，塞不进注册表）。
   *    要求整个 `BaseResourceApi` 就等于逼款号页**伪造一个假 api 对象**才能导出，
   *    那份伪造品没有任何端点与它对应 —— 看起来能导出，实际请求打不到任何地方。
   */
  api: Pick<BaseResourceApi, 'exportXlsx'>
  /** 取当前筛选条件。**每次点击时求值**，不是创建时快照 —— 否则翻页/改筛选后导出的还是旧范围。 */
  currentQuery: () => BaseListQuery
  /** 筛选值 → 中文（用于文件名摘要）。缺省用原始值。 */
  labelOf?: (name: string, value: unknown) => string
}

/**
 * 触发浏览器下载。
 *
 * 抽成导出函数是为了单测能直接断言文件名与 revoke 行为 —— jsdom 里
 * `URL.createObjectURL` 不存在，测试里必须先把它 stub 上（见 `useExport.test.ts`）。
 */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/** 拼导出文件名（docs/05 §9.1）。 */
export function buildExportFilename(
  resourceLabel: string,
  query: BaseListQuery,
  labelOf?: (name: string, value: unknown) => string,
): string {
  // ⚠️ 摘要只取**真正在筛选**的字段：`page` / `size` 每次都不一样，
  //   把它们写进文件名会让同一个筛选导出两次得到两个文件名，用户以为导错了。
  const parts: string[] = []
  for (const [name, value] of Object.entries(query)) {
    if (name === 'page' || name === 'size') continue
    if (value === undefined || value === null || value === '' || value === true) continue
    parts.push(labelOf?.(name, value) ?? String(value))
  }
  const summary = parts.length === 0 ? '全部' : parts.join('-')
  return `${resourceLabel}_${summary}_${formatCompactStamp()}.xlsx`
}

export function useExport(options: UseExportOptions) {
  const exporting = ref(false)

  /**
   * 执行导出。
   *
   * @returns 实际导出的行数；用户取消或失败返回 `null`。
   */
  async function run(): Promise<number | null> {
    if (exporting.value) return null
    exporting.value = true
    try {
      const query = options.currentQuery()
      const result: DownloadResult = await options.api.exportXlsx(query)
      // ⚠️ 优先用**前端**按 05 §9.1 拼的名字（带筛选摘要）；服务端那个只有
      //   `colors-20261004-143000`，用户拿到手分不清是哪次筛选导出的。
      const filename = buildExportFilename(options.resourceLabel, query, options.labelOf)
      triggerBlobDownload(
        result.blob,
        filename || result.filename || `${options.resourceLabel}.xlsx`,
      )
      const rows = result.rowCount
      void message.success(rows === null ? '已导出' : `已导出 ${rows} 行`)
      return rows
    } catch (caught) {
      // 全局错误提示已由 `ApiClient.onError` 报过一次（docs/06 §5）；
      // 这里只在**没有业务文案**时补一句，避免用户只看到一个 HTTP 状态码。
      const text = caught instanceof Error ? caught.message : ''
      if (text === '' || /^\d{3}$/.test(text)) {
        void message.error('导出失败，请稍后重试')
      }
      return null
    } finally {
      exporting.value = false
    }
  }

  /**
   * ⚠️ 用 `reactive` 包一层，模板里才能写 `state.exporting` 而不是
   *    `state.exporting.value` —— 后者在模板表达式里虽然能读，但可读性差且容易
   *    在别处顺手写成赋值（解包后拿到的是 boolean，只读）。
   */
  return reactive({ exporting, run })
}
