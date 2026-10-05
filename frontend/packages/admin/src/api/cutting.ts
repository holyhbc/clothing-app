/**
 * 裁剪单接口（T-CUT-001c-1/2）。
 *
 * ## 为什么不用 `api/base.ts` 的注册表工厂
 *
 * 九个基础资料是**同构**的（列表 + 增删改停用 + 候选），注册表 + `makeResourceApi`
 * 合适。裁剪单不同构：它是**三层结构**的单据，四个写接口全是**全量替换**
 * （`PUT /lines` `PUT /colors` `PUT /size-lines`）而不是增删改，另有一个
 * 「按比例带出建议」的读接口和一个「切录入模式」的副作用接口 ——
 * 硬塞进注册表就是给 `ResourceList` 攒一串 `if`，那不是统一，是补丁板。
 *
 * ## 全量替换语义的含义（页面必须知道，否则会丢数据）
 *
 * 「全量替换」= 请求体里**没带的行会被软删**。所以：
 *
 * - 「删掉某一行」与「改某一行」走**同一个接口**：把页面上现有的行原样带回再改要改的
 * - 页面必须**先拉详情**再提交，不能拿列表页那一行（列表不含三层明细）直接改
 *
 * ⚠️ 三个 PUT 与 `PATCH` 都**必须带 `version`**（表头乐观锁）：不带 → `10001`，
 * 带了但过期 → `10003`。**页面必须在每次成功写之后把响应里的新 `version` 存下来**，
 * 继续用旧的下一次就会 10003 —— 而用户看到的只是「保存失败，不知道为什么」。
 *
 * ⚠️ 类型全部来自 `@garment/shared`（由 `openapi.json` 生成），**不手写 DTO**
 * （`docs/06 §7`）。
 */
import type {
  CuttingOrderCreateIn,
  CuttingOrderListOut,
  CuttingOrderOut,
  CuttingOrderPatchIn,
  EntryModeSwitchIn,
  PageData,
  PutColorsIn,
  PutLinesIn,
  PutSizeLinesIn,
  SuggestLinesOut,
} from '@garment/shared'
import { http } from './http'

/**
 * 页面直接 import 这些类型，省得每个页面再写一遍 import 列表。
 *
 * ⚠️ 用 `export type { X }`（不带 `from`）而不是 `export type { X } from '...'`：
 *    后者**不**在本地建绑定，函数签名里的同名类型就成了未定义 —— 报错还指向
 *    `has no exported member`，与真因（本地没绑定）隔了一层。
 */
export type {
  CuttingOrderCreateIn,
  CuttingOrderListOut,
  CuttingOrderOut,
  CuttingOrderPatchIn,
  EntryModeSwitchIn,
  PutColorsIn,
  PutLinesIn,
  PutSizeLinesIn,
  SuggestLinesOut,
}

const BASE = '/cutting-orders'

/**
 * 裁剪单列表查询。
 *
 * ⚠️ 必须 `extends Record<string, QueryValue>`：`usePageList<TQuery extends PageQuery>`
 *    的约束要求 TQuery 是索引签名类型，否则 `applyFilter({status})` 传不进去
 *    （`api/styles.ts` 的 `StyleListQuery` 是同一个原因这么写的）。
 */
export interface CuttingOrderListQuery extends Record<
  string,
  string | number | boolean | null | undefined
> {
  /** 单据状态（`DRAFT` / `SUBMITTED` / …）。 */
  status?: string
  /** 款号精确匹配。⚠️ 列表**没有**模糊搜索 —— 后端 `style_no` 是等值筛选。 */
  style_no?: string
  workshop_id?: string
  /** 起始日期（含）。 */
  doc_date_from?: string
  /** 结束日期（含）。 */
  doc_date_to?: string
  page?: number
  size?: number
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}

// ------------------------------------------------------------------ 读

/**
 * 裁剪单列表。
 *
 * ⚠️ **列表不含三层明细** —— 后端刻意不 `selectinload`（一次加载三层会让
 * 「取 20 张单」变成 20 + 20×10 + 20×10×50 = 上万行）。要看明细请走 `getCuttingOrder`。
 */
export async function listCuttingOrders(
  query: CuttingOrderListQuery = {},
): Promise<{ items: CuttingOrderListOut[]; total: number }> {
  const result = await http.get<PageData<CuttingOrderListOut>>(BASE, { query: { ...query } })
  return { items: result.items, total: result.total }
}

/** 裁剪单详情 = 三层结构（`lines[].colors[].size_lines[]`，ADR-0017）。 */
export function getCuttingOrder(orderId: string): Promise<CuttingOrderOut> {
  return http.get<CuttingOrderOut>(`${BASE}/${orderId}`)
}

/**
 * 按尺码比例带出手数建议。
 *
 * ⚠️ **这个接口有副作用**：它会把比例快照写进该单的行内颜色（同一事务内，
 * `modules/02 §7`）。所以它不是纯读 —— 不要在「用户只是看看」的场合调用。
 *
 * ⚠️ `missing_size_codes` 是**提示**（该款已定义但该色没配比例的尺码），
 * C19 规定**部分缺配不拦**；而完全没配会抛 `20006`、比例里有款号未定义的尺码抛 `20007`。
 */
export function suggestSizeLines(
  orderId: string,
  params: { style_no: string; color_code: string },
): Promise<SuggestLinesOut> {
  return http.get<SuggestLinesOut>(`${BASE}/${orderId}/suggest-lines`, { query: { ...params } })
}

// ------------------------------------------------------------------ 写

/** 建裁剪单（草稿态，表头 + 三层明细一次提交）。 */
export function createCuttingOrder(payload: CuttingOrderCreateIn): Promise<CuttingOrderOut> {
  return http.post<CuttingOrderOut>(BASE, payload)
}

/**
 * 改表头（仅草稿 / 已驳回）。
 *
 * ⚠️ **不动三层明细** —— 改明细走三条 PUT。只改表头这个限制是刻意的：
 * 让「改个备注」不必锁住整张单的行与明细。
 *
 * ⚠️ `version` 必传（乐观锁）。
 */
export function patchCuttingOrder(
  orderId: string,
  payload: CuttingOrderPatchIn,
): Promise<CuttingOrderOut> {
  return http.patch<CuttingOrderOut>(`${BASE}/${orderId}`, payload)
}

/**
 * 软删裁剪单（三层级联软删）。
 *
 * ⚠️ **只有草稿能删**，且 `version` 走**查询参数**而不是 body（后端是 `DELETE`，
 * 带 body 的 DELETE 在代理与网关里极易被吞掉）。
 */
export function deleteCuttingOrder(orderId: string, version: number): Promise<{ deleted: string }> {
  return http.delete<{ deleted: string }>(`${BASE}/${orderId}`, { query: { version } })
}

/** 布批行**全量替换** —— 不在 `items` 里的旧行被软删（连同其颜色与尺码明细）。 */
export function putCuttingOrderLines(
  orderId: string,
  payload: PutLinesIn,
): Promise<CuttingOrderOut> {
  return http.put<CuttingOrderOut>(`${BASE}/${orderId}/lines`, payload)
}

/** 行内颜色**全量替换**（只动那一行）。 */
export function putLineColors(
  orderId: string,
  lineId: string,
  payload: PutColorsIn,
): Promise<CuttingOrderOut> {
  return http.put<CuttingOrderOut>(`${BASE}/${orderId}/lines/${lineId}/colors`, payload)
}

/**
 * 尺码明细**全量替换**（只动那个行内颜色）。
 *
 * ⚠️ `items[].size_line_no` **省略**时后端分配（该颜色现存最大行号 + 1）。
 */
export function putSizeLines(
  orderId: string,
  payload: PutSizeLinesIn,
): Promise<CuttingOrderOut> {
  return http.put<CuttingOrderOut>(`${BASE}/${orderId}/size-lines`, payload)
}

/**
 * 切换**颜色级**录入模式（C26 / C27）。
 *
 * ⚠️ 从 `MASTER` 切走会**清掉该颜色现有的尺码明细** —— 所以 `confirm` 必须为
 * `true`，页面必须弹二次确认并明示「已有 N 行手数将被清空」。后端不接受
 * `confirm: false`：那个「用户可能没看见弹窗」的场景代价是精心填的手数被静默清空。
 */
export function switchEntryMode(
  orderId: string,
  payload: EntryModeSwitchIn,
): Promise<{ line_color_id: string; entry_mode: string }> {
  return http.post(`${BASE}/${orderId}/entry-mode`, payload)
}