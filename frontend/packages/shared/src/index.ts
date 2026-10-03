/**
 * `@garment/shared` 的统一出口。
 *
 * ⚠️ 页面**从这里** import，不要深入 `@garment/shared/api/client` 这类子路径 ——
 * 子路径是实现细节，改目录结构时只改这一个文件即可。
 *
 * @example
 * import { client, ApiError, formatMoney, PERM, statusText } from '@garment/shared'
 */

// ---- 请求层 ----
export {
  ApiClient,
  ApiError,
  buildQuery,
  createSessionTokenStorage,
  isPageResult,
} from './api/client.ts'
export type {
  ApiEnvelope,
  ApiMutationResponseOf,
  ApiResponseOf,
  ClientHooks,
  ClientOptions,
  PageQuery,
  PageResult,
  QueryValue,
  RequestOptions,
  SchemaOperations,
  SchemaPaths,
  TokenStorage,
} from './api/client.ts'

// ---- 枚举 ----
export {
  DOCUMENT_STATUSES,
  DOCUMENT_STATUS_META,
  statusColor,
  statusMeta,
  statusText,
} from './enums/status.ts'
export type { DocumentStatus, StatusMeta } from './enums/status.ts'

export {
  PERM,
  PERMISSION_CODES,
  PERMISSION_NAMES,
  ROLE_CODES,
  ROLE_PERMISSIONS,
} from './enums/permissions.ts'
export type { PermissionCode, RoleCode } from './enums/permissions.ts'

// ---- 格式化 ----
export {
  BUSINESS_TIMEZONE,
  formatDate,
  formatDateTime,
  formatMoney,
  formatQty,
  formatRatio,
  formatUnitPrice,
} from './utils/format.ts'

// ---- 接口类型 ----
export type {
  ActiveFilter,
  ApiEnvelope as ApiEnvelopeType,
  ListQuery,
  OperationRateOut,
  OptionOut,
  PageData,
  RateResolveOut,
  RatioListOut,
  ApiModel,
  SortOrder,
  StyleDetailOut,
  StyleListOut,
  StyleOperationOut,
  Versioned,
} from './types/index.ts'
