/**
 * `@garment/shared` 的统一出口。
 *
 * ⚠️ 页面**从这里** import，不要深入 `@garment/shared/api/client` 这类子路径 ——
 * 子路径是实现细节，改目录结构时只改这一个文件即可。
 *
 * @example
 * import { client, ApiError, formatMoney, statusText } from '@garment/shared'
 */

// ---- 请求层 ----
export {
  ApiClient,
  ApiError,
  buildQuery,
  createSessionTokenStorage,
  isPageResult,
  parseContentDispositionFilename,
  parseRowCount,
} from './api/client.ts'
export type {
  ApiEnvelope,
  ApiMutationResponseOf,
  ApiResponseOf,
  ClientHooks,
  ClientOptions,
  DownloadResult,
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
  ROLE_NAMES,
  ROLE_PERMISSIONS,
} from './enums/permissions.ts'
export type { PermissionCode, RoleCode } from './enums/permissions.ts'

export { BASE_DICT_CONTRACT } from './enums/baseDictFields.ts'
export type {
  BaseDictContract,
  BaseDictField,
  BaseDictFieldType,
  BaseDictKey,
  BaseDictWriteSpec,
} from './enums/baseDictFields.ts'

// ---- 格式化 ----
export {
  BUSINESS_TIMEZONE,
  formatCompactStamp,
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
  AuthChannel,
  AvailableOutputOut,
  BaseDictRow,
  BuiltinMissingOut,
  BuiltinRestoreOut,
  BuiltinRestoreRequest,
  BundleDetailOut,
  BundleListOut,
  BundlingOrderCreateIn,
  BundlingOrderListOut,
  BundlingOrderOut,
  BundlingOrderPatchIn,
  BundlingStatisticsOut,
  CancelIn,
  ChangePasswordRequest,
  CopiedPriceOut,
  CuttingEntryMode,
  CuttingOrderCreateIn,
  CuttingOrderListOut,
  CuttingOrderOut,
  CuttingOrderPatchIn,
  DeleteOut,
  DisableOut,
  DisableUserRequest,
  DocumentLogOut,
  EnableUserRequest,
  EntryModeSwitchIn,
  HandIncrementIn,
  LabelItemOut,
  LabelPrintIn,
  LabelPrintOut,
  LineColorIn,
  LineColorOut,
  LineIn,
  LineOut,
  ListQuery,
  LoginRequest,
  LoginResponse,
  LogoutResponse,
  MeResponse,
  OperationRateCreate,
  OperationRateOut,
  OperationRateSetOut,
  OrderLineIn,
  OrderLineOut,
  PageData,
  PasswordResetRequest,
  PermissionGroupOut,
  PermissionOut,
  OptionOut,
  PutBundlingLinesIn,
  PutColorsIn,
  PutLinesIn,
  PutSizeLinesIn,
  RateResolveOut,
  RatioItemIn,
  RatioListOut,
  RatioReplaceIn,
  RateSource,
  RefreshResponse,
  RejectIn,
  ReplacePermissionsRequest,
  ReverseIn,
  RoleBrief,
  RoleCreate,
  RoleDisable,
  RoleOptionOut,
  RoleOut,
  RolePatch,
  SizeLineIn,
  SizeLineOut,
  SuggestLinesOut,
  StockBatchOptionOut,
  StyleColorCreate,
  StyleColorOut,
  StyleCreate,
  StyleDetailOut,
  StyleDisableIn,
  StyleListOut,
  StyleOperationItemIn,
  StyleOperationOut,
  StyleOperationsListOut,
  StyleOperationsReplaceIn,
  StyleOut,
  StylePatch,
  StyleSizeCreate,
  StyleSizeOut,
  SuggestedStyleNoOut,
  TemplateCopyIn,
  TemplateCopyMode,
  TemplateCopyOut,
  UserBrief,
  UserCreate,
  UserOptionOut,
  UserOut,
  UserPatch,
  UserRoleAssign,
  DataScope,
  ApiModel,
  SortOrder,
  Versioned,
} from './types/index.ts'
