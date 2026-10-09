/**
 * 打菲单接口（T-BUND-009）。
 *
 * 类型全部来自 `@garment/shared`（由 `openapi.json` 生成），**不手写 DTO**
 * （`docs/06 §7`）。
 *
 * 列表查询参数：后端 OpenAPI 未生成 `parameters.query`（FastAPI 内联 Query），
 * 前端需手补 `BundlingOrderListQuery`。
 */
import type {
  BundlingOrderCreateIn,
  BundlingOrderListOut,
  BundlingOrderOut,
  BundlingOrderPatchIn,
  LineIn,
  LineOut,
  AvailableOutputOut,
  HandIncrementIn,
  SplitPreviewOut,
  CancelIn,
  RejectIn,
  ReverseIn,
  LabelPrintIn,
  PageData,
} from '@garment/shared'
import { http } from './http'
import { searchStyleOptionsById } from '@/api/cutting'

export type {
  BundlingOrderCreateIn,
  BundlingOrderListOut,
  BundlingOrderOut,
  BundlingOrderPatchIn,
  LineIn,
  LineOut,
  AvailableOutputOut,
  HandIncrementIn,
  SplitPreviewOut,
  CancelIn,
  RejectIn,
  ReverseIn,
  LabelPrintIn,
}

/**
 * 打菲单列表查询参数。
 *
 * ⚠️ 必须 `extends Record<string, QueryValue>`：`usePageList<TQuery extends PageQuery>`
 *    的约束要求 TQuery 是索引签名类型，否则 `applyFilter({status})` 传不进去
 *    （`api/styles.ts` 的 `StyleListQuery` 是同一个原因这么写的）。
 */
export interface BundlingOrderListQuery extends Record<
  string,
  string | number | boolean | null | undefined
> {
  /** 单据状态（`DRAFT` / `SUBMITTED` / …）。 */
  status?: string
  /** 款号精确匹配。⚠️ 列表**没有**模糊搜索 —— 后端 `style_no` 是等值筛选。 */
  style_no?: string
  /** 工序号精确匹配。 */
  operation_no?: string
  /** 色码精确匹配。 */
  color_code?: string
  /** 车间 UUID（数据范围过滤依据，INV-8）。 */
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

const BASE = '/bundling-orders'

// ------------------------------------------------------------------ 读

/**
 * 打菲单列表。
 *
 * ⚠️ **列表不含明细** —— 后端刻意不 `selectinload`（一次加载明细会让
 * 「取 20 张单」变成上万行）。要看明细请走 `getBundlingOrder`。
 */
export async function listBundlingOrders(
  query: BundlingOrderListQuery = {},
): Promise<{ items: BundlingOrderListOut[]; total: number }> {
  const result = await http.get<PageData<BundlingOrderListOut>>(BASE, { query: { ...query } })
  return { items: result.items, total: result.total }
}

/** 打菲单详情 = 表头 + 明细（`lines[]`）。 */
export function getBundlingOrder(orderId: string): Promise<BundlingOrderOut> {
  return http.get<BundlingOrderOut>(`${BASE}/${orderId}`)
}

/**
 * 可打菲来源明细（裁剪侧手数 + 现在还能打多少）。
 *
 * ⚠️ 款号不接受传入：一律取本单的 `style_no` —— 可用量按 (款号 + 色码 + 尺码)
 * 定位，让前端传款号就等于让它指定口径，而那个口径服务端并不认。
 * ⚠️ 余量为 0 的尺码**也在列表里**（不是被过滤掉）：主管要看见「这个尺码一件都打不了」。
 */
export function listAvailableOutputs(
  orderId: string,
  params: { style_no: string; color_code: string },
): Promise<AvailableOutputOut[]> {
  return http.get<AvailableOutputOut[]>(`${BASE}/${orderId}/available-outputs`, { query: { ...params } })
}

/**
 * 拆分预演（只读不落库：这张单会生成哪些码、各几件、余数多少）。
 *
 * ⚠️ **只读**：不写库、不预占、不写日志。所以权限点是 `bundling:read` 而不是
 * `bundling:approve` —— 它不改任何东西。
 * ⚠️ **预演结果不作为审核依据**：审核事务内**重算一遍**并跑断言（TOCTOU）。
 * ⚠️ `conflicts[]` **返回而不报错**：那是给主管看的清单（手序号与库内已有 ACTIVE 码冲突），
 * 真正拦截发生在 submit / approve（那里有唯一索引兜底 → `31005`）。
 */
export function previewSplit(
  orderId: string,
  payload?: { lines: { size_code: string; hands: number; cutting_size_line_id: string }[] },
): Promise<SplitPreviewOut> {
  return http.post<SplitPreviewOut>(`${BASE}/${orderId}/split`, payload ?? {})
}

// ------------------------------------------------------------------ 写

/** 建打菲单（草稿态，表头 + 明细一次提交）。 */
export function createBundlingOrder(payload: BundlingOrderCreateIn): Promise<BundlingOrderOut> {
  return http.post<BundlingOrderOut>(BASE, payload)
}

/**
 * 改表头（仅 DRAFT / REJECTED）。
 *
 * ⚠️ **不动明细** —— 改明细走 `PUT /lines`（全量替换）。只改表头这个限制是刻意的：
 * 让「改个备注」不必锁住整张单的行与明细。
 * ⚠️ `version` 必传（乐观锁）。
 */
export function patchBundlingOrder(
  orderId: string,
  payload: BundlingOrderPatchIn,
): Promise<BundlingOrderOut> {
  return http.patch<BundlingOrderOut>(`${BASE}/${orderId}`, payload)
}

/** 明细全量替换（软删旧行 + 插新行 + 重算汇总）。 */
export function putBundlingOrderLines(
  orderId: string,
  payload: { lines: LineIn[] },
): Promise<BundlingOrderOut> {
  return http.put<BundlingOrderOut>(`${BASE}/${orderId}/lines`, payload)
}

/** 提交（DRAFT/REJECTED → SUBMITTED，预占裁剪可用量）。 */
export function submitBundlingOrder(orderId: string): Promise<BundlingOrderOut> {
  return http.post<BundlingOrderOut>(`${BASE}/${orderId}/submissions`, {})
}

/** 审核（SUBMITTED → APPROVED，★ 按手批量生成打菲码；支持 Idempotency-Key）。 */
export function approveBundlingOrder(
  orderId: string,
  payload: { remark?: string } = {},
  idempotencyKey?: string,
): Promise<BundlingOrderOut> {
  return http.post<BundlingOrderOut>(
    `${BASE}/${orderId}/approvals`,
    payload,
    idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : undefined,
  )
}

/** 驳回（SUBMITTED → REJECTED，★ 必填原因 + 释放预占）。 */
export function rejectBundlingOrder(orderId: string, payload: RejectIn): Promise<BundlingOrderOut> {
  return http.post<BundlingOrderOut>(`${BASE}/${orderId}/rejections`, payload)
}

/** 撤回（SUBMITTED → DRAFT，★ 撤回人 = 制单人 + 释放预占）。 */
export function withdrawBundlingOrder(orderId: string): Promise<BundlingOrderOut> {
  return http.post<BundlingOrderOut>(`${BASE}/${orderId}/withdrawals`, {})
}

/** 反审核（APPROVED → SUBMITTED，★ 必填原因 + 全链路反向）。 */
export function reverseBundlingOrder(orderId: string, payload: ReverseIn): Promise<BundlingOrderOut> {
  return http.post<BundlingOrderOut>(`${BASE}/${orderId}/reversals`, payload)
}

/** 作废（DRAFT/REJECTED → CANCELLED，终态 + 必填原因）。 */
export function cancelBundlingOrder(orderId: string, payload: CancelIn): Promise<BundlingOrderOut> {
  return http.post<BundlingOrderOut>(`${BASE}/${orderId}/cancellations`, payload)
}

/** 审核后增手（APPROVED → 同，★ 只补生成新码；减手请走 /reversals）。 */
export function incrementBundlingOrderHands(
  orderId: string,
  payload: HandIncrementIn,
): Promise<BundlingOrderOut> {
  return http.post<BundlingOrderOut>(`${BASE}/${orderId}/hand-increments`, payload)
}

/** 批量打印登记（★ 一手一行留痕；重打必带 print_seq；支持 Idempotency-Key）。 */
export function registerLabelPrints(
  orderId: string,
  payload: LabelPrintIn,
  idempotencyKey?: string,
): Promise<{ print_ids: string[]; printed_count: number }> {
  return http.post<{ print_ids: string[]; printed_count: number }>(
    `${BASE}/${orderId}/label-prints`,
    payload,
    idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : undefined,
  )
}

/** 标签导出（一手一张：款号/色/码/工序/第 N 手 / 共 M 手/该手件数/二维码）。 */
export function exportLabels(
  orderId: string,
  params: { from_hands: number; to_hands: number; size_code?: string; format?: 'data' | 'csv' },
): Promise<{ data: unknown[]; csv_text?: string }> {
  return http.get<{ data: unknown[]; csv_text?: string }>(`${BASE}/${orderId}/labels`, { query: { ...params } })
}

/** 操作日志。 */
export function listBundlingOrderLogs(
  orderId: string,
  query: { page?: number; page_size?: number } = {},
): Promise<PageData<unknown>> {
  return http.get<PageData<unknown>>(`${BASE}/${orderId}/logs`, { query })
}

/** 款号候选（UUID，供新建打菲单用）。 */
export { searchStyleOptionsById }
