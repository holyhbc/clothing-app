/**
 * 标签二维码/条码生成（T-BUND-010）。
 *
 * - 二维码：纯文本 `bundle_no`，纠错等级 M，尺寸 ≥ 20mm（打印时 18mm ≈ 20mm @ 300dpi）
 * - 条码：Code128 字符集 B，内容同 `bundle_no`
 * - 结果缓存避免重复生成
 */

import QRCode from 'qrcode'
import * as bwipjs from 'bwip-js'

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
    if (barcodeCache.has(key)) return barcodeCache.get(key)!
    const dataUrl = await bwipjs.toDataURL({
      bcid: 'code128',
      text: content,
      scale: 2,
      width: widthPx,
      height: heightPx,
      includetext: false,
      textalign: 'center',
      font: 'monospace',
      fontsize: 10,
    })
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
