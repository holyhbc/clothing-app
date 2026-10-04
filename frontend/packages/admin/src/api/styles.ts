/**
 * 款号 / 色组 / 尺码 / 款号工序 / 尺码比例 / 模板复制（T-WEB-006）。
 *
 * ## 为什么不用 `api/base.ts` 那套注册表
 *
 * 九个基础资料资源是**同构**的，所以注册表 + `makeResourceApi` 工厂合适。款号这一族
 * 不是同构的：色组是「追加」、尺码是「单码或整套带出」、款号工序是「**全量替换**」、
 * 比例是「按 (款号, 颜色) 全量替换」、模板复制是**唯一带幂等键**的写操作 ——
 * 每一条的语义都不同（docs/modules/01 §3.2 / §3.4 / §5.1 / §5.3）。把它们硬塞进
 * 注册表，得到的不是统一，而是把 6 种语义压进 1 个形状，然后到处加 `if`。
 *
 * ⚠️ 类型全部来自 `@garment/shared`（由 `openapi.json` 生成），**不手写 DTO**
 * （docs/06 §7）。
 */
import type {
  CopiedPriceOut,
  DocumentLogOut,
  OperationRateCreate,
  OperationRateOut,
  OperationRateSetOut,
  OptionOut,
  PageData,
  RatioListOut,
  RatioReplaceIn,
  RateResolveOut,
  StyleColorCreate,
  StyleColorOut,
  StyleCreate,
  StyleDetailOut,
  StyleListOut,
  StyleOperationsListOut,
  StyleOperationsReplaceIn,
  StyleOut,
  StylePatch,
  StyleSizeCreate,
  StyleSizeOut,
  SuggestedStyleNoOut,
  TemplateCopyIn,
  TemplateCopyOut,
} from '@garment/shared'
import { http } from './http'

/**
 * 页面从 `@/api/styles` 直接 import 这些类型（写操作体的字段多，页面里再写一遍
 * import 列表只会变成噪音）。
 *
 * ⚠️ 用 `export type { X }`（不带 `from`）而不是 `export type { X } from '...'`：
 *    后者**不**在本地建绑定，函数签名里的同名类型就成了未定义 —— 报错还指向
 *    `has no exported member`，与真因（本地没绑定）隔了一层。
 */
export type {
  CopiedPriceOut,
  OperationRateCreate,
  OperationRateOut,
  OperationRateSetOut,
  RatioListOut,
  RatioReplaceIn,
  StyleColorCreate,
  StyleColorOut,
  StyleCreate,
  StyleDetailOut,
  StyleListOut,
  StyleOperationsListOut,
  StyleOperationsReplaceIn,
  StyleOut,
  StylePatch,
  StyleSizeCreate,
  StyleSizeOut,
  SuggestedStyleNoOut,
  TemplateCopyIn,
  TemplateCopyOut,
}

/** 编码类路径参数一律转义 —— 款号里有 `/` 时不转义会把路径截断。 */
function pathOf(styleNo: string): string {
  return `/styles/${encodeURIComponent(styleNo)}`
}

// ------------------------------------------------------------------ 款号

export interface StyleListQuery {
  /** 编码 / 款名 / 客户名 / 分类名模糊搜索（05 §9.5.1）。 */
  q?: string
  is_active?: boolean
  customer_id?: string
  category_id?: string
  merchandiser_id?: string
  page?: number
  size?: number
}

export async function listStyles(query: StyleListQuery = {}): Promise<{
  items: StyleListOut[]
  total: number
}> {
  const result = await http.get<PageData<StyleListOut>>('/styles', { query: { ...query } })
  return { items: result.items, total: result.total }
}

export function getStyle(styleNo: string): Promise<StyleDetailOut> {
  return http.get<StyleDetailOut>(pathOf(styleNo))
}

export function createStyle(payload: StyleCreate): Promise<StyleOut> {
  return http.post<StyleOut>('/styles', payload)
}

/** 局部更新。⚠️ `version` 必传（不匹配 → `10003`）；`style_no` 不可改（历史单据按它引用）。 */
export function patchStyle(styleNo: string, payload: StylePatch): Promise<StyleOut> {
  return http.patch<StyleOut>(pathOf(styleNo), payload)
}

/**
 * 取一个建议款号（**不建档**；⚠️ 会消耗一个序号）。
 *
 * ⚠️ 权限点是 `base:create` —— 取号是写操作。只给 `base:read` 的话任何能看款号的人
 *    都能狂点把某一年的序号消耗光（唯一性没问题，但建议号会跳得很难看）。
 */
export function suggestStyleNo(customerId: string | null): Promise<SuggestedStyleNoOut> {
  return http.get<SuggestedStyleNoOut>('/styles/suggested-no', {
    query: { customer_id: customerId ?? undefined },
  })
}

/** 候选（`Combo` 用）。⚠️ 默认按 `last_used_at DESC` —— 常用优先（05 §9.5.2）。 */
export function searchStyleOptions(keyword: string): Promise<OptionOut[]> {
  return http.get<OptionOut[]>('/styles/options', { query: { q: keyword, size: 20 } })
}

// ------------------------------------------------------------------ 色组

/** 追加一行色组（**不是**全量替换；色码重复 → `10001`）。 */
export function addStyleColor(
  styleNo: string,
  payload: StyleColorCreate,
): Promise<StyleColorOut[]> {
  return http.post<StyleColorOut[]>(`${pathOf(styleNo)}/colors`, payload)
}

// ------------------------------------------------------------------ 尺码

/**
 * 新增款号尺码：**单码或整套带出**（modules/01 §5.3）。
 *
 * ⚠️ 两种互斥（都传或都不传后端 `10001`）—— 所以这里给两个具名函数而不是让调用方
 * 自己拼 body：拼错的表现是"提交后报参数不合法"，而界面看不出是哪两个字段冲突。
 */
export function addStyleSize(styleNo: string, payload: StyleSizeCreate): Promise<StyleSizeOut[]> {
  return http.post<StyleSizeOut[]>(`${pathOf(styleNo)}/sizes`, payload)
}

// ------------------------------------------------------------------ 款号工序

export function listStyleOperations(
  styleNo: string,
  query: { is_piecework?: boolean; include_inactive?: boolean } = {},
): Promise<StyleOperationsListOut> {
  return http.get<StyleOperationsListOut>(`${pathOf(styleNo)}/operations`, { query: { ...query } })
}

/**
 * **全量替换**款号工序（≤500 行，必传 `version`）。
 *
 * ⚠️ 全量替换的语义：不在 `items` 里的旧行会被删掉。界面必须让用户看见"这会覆盖
 * 现有配置"，否则改一行工序会静默删掉其余工序。
 */
export function replaceStyleOperations(
  styleNo: string,
  payload: StyleOperationsReplaceIn,
): Promise<StyleOperationsListOut> {
  return http.put<StyleOperationsListOut>(`${pathOf(styleNo)}/operations`, payload)
}

// ------------------------------------------------------------------ 尺码比例

/** 某款某色的手数比例。**查询永远返回空集**（缺配只提示，不抛 `20006`）。 */
export function getStyleRatios(styleNo: string, colorCode: string): Promise<RatioListOut> {
  return http.get<RatioListOut>('/style-color-size-ratios', {
    query: { style_no: styleNo, color_code: colorCode },
  })
}

/**
 * 按 (款号, 颜色) **全量替换**比例（≤100 行，必传款号的 `version`）。
 *
 * ⚠️ `version` 取的是**款号聚合行**的版本，不是比例行的 —— 全量替换会把旧行删光，
 *    子表自己的 version 每次从 1 重来（后端 `RatioReplaceIn` 的注释）。
 */
export function replaceStyleRatios(payload: RatioReplaceIn): Promise<RatioListOut> {
  return http.put<RatioListOut>('/style-color-size-ratios', payload)
}

// ------------------------------------------------------------------ 模板复制

/**
 * 工序与单价模板复制（ADR-0009 §3）。
 *
 * ⚠️ 带 `Idempotency-Key`：docs/05 §5 要求高频写接口幂等，而这条**重复点会静默复制两遍**
 * （复制两遍会让同一工序有两条单价记录，后面取价命中哪条要看 created_at）。
 * 前端重复点击靠这个兜底。
 */
export function copyStyleTemplate(
  styleNo: string,
  sourceStyleNo: string,
  payload: TemplateCopyIn,
): Promise<TemplateCopyOut> {
  return http.post<TemplateCopyOut>(
    `${pathOf(styleNo)}/operations/copy-from/${encodeURIComponent(sourceStyleNo)}`,
    payload,
    { idempotent: true },
  )
}

// ------------------------------------------------------------------ 工序单价

export interface RateListQuery {
  /** ⚠️ **可选**（ADR-0026）：档位 2 / 3 的行 `style_no` 本来就是 NULL，
   *  必传的话这两档「建得出来、却查不到」。 */
  style_no?: string
  operation_no?: string
  product_category_id?: string
  effective_from?: string
  effective_to?: string
  page?: number
  size?: number
}

export async function listRates(query: RateListQuery = {}): Promise<{
  items: OperationRateOut[]
  total: number
}> {
  const result = await http.get<PageData<OperationRateOut>>('/operation-rates', {
    query: { ...query },
  })
  return { items: result.items, total: result.total }
}

/**
 * 设价 / 调价。
 *
 * ⚠️ **只 INSERT，永不 UPDATE `unit_price`**（R11 / ADR-0026）：调价 = 旧行只改
 * `effective_to` + 插入新区间。所以「改今天的价」必须换一个生效日。
 * ⚠️ **调价必须填 `reason`**（R20 → `10006`），首次设价可空。
 */
export function setOperationRate(payload: OperationRateCreate): Promise<OperationRateSetOut> {
  return http.post<OperationRateSetOut>('/operation-rates', payload)
}

/** 取价预演（只读不写库）。缺省 `workDate` = 今天（工厂当地日历）。 */
export function resolveRate(params: {
  style_no: string
  operation_no: string
  work_date?: string
}): Promise<RateResolveOut> {
  return http.get<RateResolveOut>('/operation-rates/resolve', { query: { ...params } })
}

// ------------------------------------------------------------------ 变更历史

/**
 * 变更历史（docs/06 §2.3 的抽屉）。
 *
 * ⚠️ `doc_type` 与 `doc_no` **都必填**：审计表没有归属列，"查全部日志"在数据范围
 * （Q-P0-05 跟单只看自己的款号）下根本无法表达。
 */
export async function listDocumentLogs(params: {
  doc_type: string
  doc_no: string
  page?: number
  size?: number
}): Promise<PageData<DocumentLogOut>> {
  return http.get<PageData<DocumentLogOut>>('/document-logs', { query: { ...params } })
}
