/**
 * 计件流水接口（T-PIECE-005，消费 T-PIECE-003 的只读端点）。
 *
 * 类型全部来自 `@garment/shared`（由 `openapi.json` 生成），**不手写 DTO**
 * （`docs/06 §7`）。
 *
 * ⚠️ 列表查询参数：后端 FastAPI 用**内联 `Query(...)`**（`query_router._query`），
 *    OpenAPI 没有生成 `parameters.query`，前端需手补 `PieceworkLogQuery`。
 */
import type {
  DownloadResult,
  PageData,
  PieceworkLogDetailOut,
  PieceworkLogRowOut,
  PieceworkLogType,
} from '@garment/shared'
import { http } from './http'

export type { PieceworkLogDetailOut, PieceworkLogRowOut, PieceworkLogType }

const BASE = '/piecework-logs'

/**
 * 计件流水列表查询参数。
 *
 * ⚠️ 必须 `extends Record<string, QueryValue>`：`usePageList<TQuery extends PageQuery>`
 *    的约束要求 TQuery 是索引签名类型（同 `api/styles.ts` 的 `StyleListQuery`）。
 */
export interface PieceworkLogQuery extends Record<
  string,
  string | number | boolean | null | undefined
> {
  /** 工作日期起始（含）；`YYYY-MM-DD`。与 `work_date_to` 成对使用。 */
  work_date_from?: string
  /** 工作日期结束（含）。 */
  work_date_to?: string
  /** 款号精确匹配（后端等值筛选，无模糊）。 */
  style_no?: string
  /** 工序号精确匹配。 */
  operation_no?: string
  /** 车间 id（UUID）。 */
  workshop_id?: string
  /** 组别号精确匹配。 */
  group_no?: string
  /** 员工 id（UUID）。⚠️ 本页暂不暴露（无候选端点），保留给后续卡。 */
  employee_id?: string
  /** 流水类型。 */
  log_type?: PieceworkLogType
  /** 打菲号精确匹配。 */
  bundle_no?: string
  /** 是否已结算（进入工资）。 */
  is_settled?: boolean
  /** 排序字段；`-` 前缀为倒序。默认 `-work_date`。 */
  sort?: string
  page?: number
  size?: number
}

/**
 * 流水列表（`GET /api/v1/piecework-logs`）。
 *
 * ⚠️ 后端返回 `ApiResponse[PageData[LogRowOut]]`；`http.get` 已拆掉 `data` 外壳
 *    并把 `PageData` 归一成 `{ items, total }`（与列表页 `usePageList` 约定一致）。
 *    其他列表端点（`listBundlingOrders` 等）是同一约定。
 */
export function listPieceworkLogs(
  query: PieceworkLogQuery,
): Promise<{ items: PieceworkLogRowOut[]; total: number }> {
  return http.get<PageData<PieceworkLogRowOut>>(BASE, { query: { ...query } })
}

/** 流水详情（`GET /api/v1/piecework-logs/{log_id}`）：本体 + 码信息 + 红冲关联。 */
export function getPieceworkLog(logId: string): Promise<PieceworkLogDetailOut> {
  return http.get<PieceworkLogDetailOut>(`${BASE}/${logId}`)
}

/**
 * 导出流水 CSV（`GET /api/v1/piecework-logs/export`）。
 *
 * ⚠️ 与列表**同一套筛选**（后端三个端点共用 `query_router._query`），所以这里
 *    把当前 `query` 原样传过去即可，「导出行数 == 列表 total」是结构上成立的。
 * ⚠️ 需要**两个**权限点：`piecework:export` + `system:export:manage`（后端已校验，
 *    前端只用 `piecework:export` 控制按钮显隐）。
 */
export function exportPieceworkLogs(query: PieceworkLogQuery): Promise<DownloadResult> {
  return http.download(`${BASE}/export`, { query: { ...query } })
}
