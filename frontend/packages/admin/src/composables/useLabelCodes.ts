/**
 * 标签二维码/条码生成（T-BUND-010）。
 *
 * - 二维码：纯文本 `bundle_no`，纠错等级 M，尺寸 ≥ 20mm（打印时 18mm ≈ 20mm @ 300dpi）
 * - 条码：Code128 字符集 B，内容同 `bundle_no`
 * - 结果缓存避免重复生成
 */

import QRCode from 'qrcode'
// ⚠️ **必须显式用 `/browser` 入口**（见下面 `generateBarcode` 的长注释）。
import bwipjs from 'bwip-js/browser'

const qrCache = new Map<string, string>()
const barcodeCache = new Map<string, string>()

export function useLabelCodes() {
  /**
   * 生成二维码 DataURL（缓存）。
   * @param content 二维码内容（`bundle_no`）
   * @param sizePx 画布像素尺寸（屏幕预览用 80px，打印用 18mm ≈ 212px @ 300dpi）
   */
  async function generateQR(content: string, sizePx = 80): Promise<string> {
    const key = `${content}:${sizePx}`
    if (qrCache.has(key)) return qrCache.get(key)!
    const dataUrl = await QRCode.toDataURL(content, {
      errorCorrectionLevel: 'M',
      width: sizePx,
      margin: 1,
      color: { dark: '#000000', light: '#ffffff' },
    })
    qrCache.set(key, dataUrl)
    return dataUrl
  }

  /**
   * 生成 Code128 条码 DataURL（缓存）。
   * @param content 条码内容（`bundle_no`）
   * @param widthPx 条码宽度像素
   * @param heightPx 条码高度像素
   */
  async function generateBarcode(content: string, widthPx = 80, heightPx = 80): Promise<string> {
    const key = `${content}:${widthPx}:${heightPx}`
    const cached = barcodeCache.get(key)
    if (cached !== undefined) return cached
    // ⚠️ ★ **浏览器构建没有 `toDataURL`，只有 `toCanvas`（要一块 `<canvas>`）**；
    //   `toDataURL` 只存在于 **node** 构建（`bwip-js-node`）。
    //   而 `import bwipjs from 'bwip-js'` 在**类型侧**解析到的恰好是 node 那份
    //   声明（`moduleResolution: bundler` 下 `exports` 的 `browser`/`node`
    //   两个条件谁先命中取决于解析器），于是：
    //
    //     vue-tsc 全绿 ── 浏览器里 100% 抛 `bwipjs.toDataURL is not a function`
    //
    //   标签预览一张都出不来（`loadPreview` 的 catch 把它吞成一条
    //   「预览加载失败」，看不出根因）。单测也抓不到：`useLabelCodes`
    //   在 `LabelPrint.test.ts` 里整个被 mock 掉了。
    //   → 所以这里**显式走 `/browser` 入口**：类型与运行时拿到的是同一份构建。
    const canvas = document.createElement('canvas')
    // ⚠️ **不要**传 `textalign` / `font` / `fontsize`：那是 node 那份声明里的
    //   名字，浏览器构建的 `RenderOptions` 里对应的是 `textxalign` / `textfont` /
    //   `textsize`，而且条码**不印人眼可读文字**（`includetext: false`，
    //   03 §5.4：可读文字由标签模板自己排），所以这几个参数一律不要。
    bwipjs.toCanvas(canvas, {
      bcid: 'code128',
      text: content,
      scale: 2,
      width: widthPx,
      height: heightPx,
      includetext: false,
    })
    const dataUrl = canvas.toDataURL('image/png')
    barcodeCache.set(key, dataUrl)
    return dataUrl
  }

  /** 清空缓存（测试或内存压力时） */
  function clearCache(): void {
    qrCache.clear()
    barcodeCache.clear()
  }

  return { generateQR, generateBarcode, clearCache }
}
