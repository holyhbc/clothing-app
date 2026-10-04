/**
 * 九个基础资料资源的**声明式注册表** + 由它生成的请求封装（T-WEB-005）。
 *
 * ## 为什么不写 9 × 8 = 72 个函数
 *
 * 后端已经用声明式注册表解决了同构问题（`app/modules/base/resources.py::RESOURCES`，
 * 那里的注释写得很清楚：「写成九份重复的 handler 意味着同一条业务规则要改九遍」）。
 * 前端照抄成 72 个函数就是**把同一个错误换个语言再犯一遍**：改一个路径要改 9 处，
 * 漏一处的后果不是编译错误，而是那个资源**静默 404**。
 *
 * 所以这里是 `makeResourceApi(key)` 一个工厂 + 9 行声明。
 *
 * ## 单一真相在哪：注册表只写后端不可能知道的东西
 *
 * | 信息 | 唯一来源 | 为什么 |
 * | --- | --- | --- |
 * | 路径参数列 / 写权限 / **必填字段** / 字段类型 | **后端**（生成物 `BASE_DICT_CONTRACT`） | 前端另抄一份 = 9 份真相，后端加必填字段时前端无提示（任务卡缺口③） |
 * | 中文名 / 表格列 / 占位提示 / 枚举中文映射 | **本注册表** | 后端不可能知道界面上叫「色卡族」还是「色卡系列」 |
 * | 内置角标 / 真删还是软删 / 引用列 | **由契约派生** | 见下方「哪些开关不写在这里」 |
 *
 * ⚠️ **哪些开关刻意不写在这里**（写进来就是第二份真相）：
 *   - 「是否显示内置角标」「是否显示『恢复内置库』按钮」← `hasBuiltinFlag`
 *   - 删除确认框说「真删，不可撤销」还是「软删」← `allowPhysicalDelete`
 *   - 「引用」列 ← 每行的 `ref_count`（`null` 就显示 `—`）
 * 这三个都是后端注册表里已有的事实，抄一份就等着它漂移。
 *
 * ## 一致性守卫（三层，缺一层就会出现"9 份真相"）

 * ```
 * pydantic WRITE_MODELS（后端真相）
 *   → frontend/scripts/generate-frontend-contract.mjs（生成物）
 *     → BASE_DICT_CONTRACT（本文件 import 的那份）
 *       → 本注册表（只补界面口径）
 * ```
 *
 * - `backend/tests/modules/test_base_resource_registry.py`：生成物 == 后端模型，
 *   且注册表的 key 集合与后端 `RESOURCES` 一致（差集必须能被显式解释）
 * - `src/api/base.test.ts`：注册表声明的列 / 表单字段**必须存在**于契约里，
 *   且**必填标记完全由契约驱动**（注册表不得自己写必填）
 */
import { BASE_DICT_CONTRACT } from '@garment/shared'
import type {
  BaseDictContract,
  BaseDictKey,
  BaseDictRow,
  DeleteOut,
  DisableOut,
  DownloadResult,
  OptionOut,
  PageQuery,
  QueryValue,
} from '@garment/shared'
import { http } from './http'

// ------------------------------------------------------------------ 声明类型

/** 列表与导出共用的查询条件（后端 `_list_query` 收的就是这些）。 */
export interface BaseListQuery extends PageQuery {
  /** 编码 / 名称模糊搜索（docs/05 §9.5.1）。 */
  q?: string
  is_active?: boolean
  is_builtin?: boolean
  sort_by?: string
  sort_order?: 'asc' | 'desc'
  /** 以下五个是后端 `ListQuery.filters` 的白名单列（`resources.py::filter_columns`）。 */
  workshop_id?: string
  size_class?: string
  warehouse_type?: string
  color_family?: string
  is_piecework?: boolean
}

/**
 * 行字段名（`DictOut` 的属性名 + `ref_count`）。
 *
 * ⚠️ 表格列的 `name` 用这个类型而不是 `string`：写错一个字段名（`color_name` vs `name`
 *    —— REV-2026-10 第三批**真的**改过这个名）在这里是**编译错误**，而不是
 *    「表格里一整列空白且页面上零报错」。
 */
export type BaseRowField = keyof BaseDictRow

/** 表格列。 */
export interface BaseColumn {
  readonly name: BaseRowField
  readonly label: string
  readonly width: number
  /** 右对齐 + 等宽数字（数量 / 小数位，docs/06 §2.2）。 */
  readonly numeric?: boolean
}

/**
 * 表单字段的**界面口径**。
 *
 * ⚠️ 这里**没有** `required`：必填由 `BASE_DICT_CONTRACT[...].create.required` 决定。
 *    写在这里就多了一份真相，而后端加必填字段时前端不会跟着变。
 */
export interface BaseFormField {
  readonly name: string
  readonly label: string
  readonly placeholder?: string
  /** 枚举字段的中文映射（docs/06 §1「枚举值来自后端，前端只做中文映射」）。 */
  readonly options?: Readonly<Record<string, string>>
}

/** 筛选项。默认显示前两个，其余进「更多条件」（docs/06 §2.2）。 */
export interface BaseFilter {
  readonly name: string
  readonly label: string
  /**
   * `combo` = 候选来自另一个资源（走 `/options`，docs/06 §10 强制 Combo）。
   * 其余按文本框处理。
   */
  readonly control: 'text' | 'bool' | 'enum' | 'combo'
  /** `control === 'combo'` 时候选来源资源（如组别按车间筛）。 */
  readonly source?: BaseDictKey
  readonly placeholder?: string
  readonly options?: Readonly<Record<string, string>>
}

export interface BaseResourceDecl {
  readonly key: BaseDictKey
  /** 菜单项与页面标题。 */
  readonly title: string
  /** 单条中文名：「新建颜色」「还没有颜色」「编辑工序」。 */
  readonly itemLabel: string
  readonly columns: readonly BaseColumn[]
  readonly formFields: readonly BaseFormField[]
  readonly filters: readonly BaseFilter[]
}

// ------------------------------------------------------------------ 枚举中文映射

/** 尺码类（后端 PG enum `size_class`，docs/04 §7.4）。三个资源共用这一份。 */
const SIZE_CLASS_OPTIONS: Readonly<Record<string, string>> = {
  MENS: '男装',
  WOMENS: '女装',
  KIDS: '童装',
}

const REMARK: BaseFormField = { name: 'remark', label: '备注', placeholder: '可不填' }

// ------------------------------------------------------------------ 注册表

/**
 * 九条声明。顺序即菜单与页面顺序（与后端 `RESOURCES` 一致）。
 *
 * ⚠️ `key` 必须是 `BaseDictKey`（生成物推导出来的联合类型），打错一个字母
 *    **编译就红** —— 而手写 `'colour'` 这种 typo 的后果是那个资源静默 404。
 */
export const RESOURCE_REGISTRY = {
  workshops: {
    key: 'workshops',
    title: '车间',
    itemLabel: '车间',
    columns: [
      { name: 'code', label: '车间编码', width: 140 },
      { name: 'name', label: '车间名', width: 180 },
    ],
    formFields: [
      { name: 'code', label: '车间编码', placeholder: '全厂唯一，如 C01' },
      { name: 'name', label: '车间名' },
      REMARK,
    ],
    filters: [],
  },
  'workshop-groups': {
    key: 'workshop-groups',
    title: '组别',
    itemLabel: '组别',
    columns: [
      { name: 'group_no', label: '组别号', width: 140 },
      { name: 'name', label: '组别名', width: 180 },
      // ⚠️ 行里只有 `workshop_id`（UUID），没有车间名 —— 列表页用车间候选缓存翻译成
      //    「C01 一号车间」，翻译不到就显示 UUID 前 8 位。**不做二次请求**：
      //    docs/06 §2.2 的表格要的是"一眼看到"，逐行再查一次就是 N+1。
      { name: 'workshop_id', label: '所属车间', width: 180 },
    ],
    formFields: [
      {
        name: 'workshop_id',
        label: '所属车间',
        placeholder: '输入车间编码或名称搜索',
      },
      { name: 'group_no', label: '组别号', placeholder: '车间内唯一，如 G01' },
      { name: 'name', label: '组别名' },
      REMARK,
    ],
    filters: [{ name: 'workshop_id', label: '所属车间', control: 'combo', source: 'workshops' }],
  },
  warehouses: {
    key: 'warehouses',
    title: '仓库',
    itemLabel: '仓库',
    columns: [
      { name: 'code', label: '仓库编码', width: 140 },
      { name: 'name', label: '仓库名', width: 180 },
      { name: 'warehouse_type', label: '类型', width: 120 },
    ],
    formFields: [
      { name: 'code', label: '仓库编码', placeholder: '全厂唯一，如 WH-FABRIC' },
      { name: 'name', label: '仓库名' },
      {
        name: 'warehouse_type',
        label: '类型',
        // ⚠️ 后端是**自由文本**（`WarehouseCreate.warehouse_type: str`，只在
        //    description 里提了 FABRIC / TRIMMING / FINISHED_GOOD），DB 里没有
        //    枚举。所以这里给文本框 + 提示，而不是编一个下拉去限定取值
        //    （AGENTS §2.3：业务规则不允许 AI 自行发明）。
        placeholder: '如 FABRIC 面料库 / TRIMMING 辅料库 / FINISHED_GOOD 成衣库',
      },
      REMARK,
    ],
    filters: [],
  },
  'uom-units': {
    key: 'uom-units',
    title: '计量单位',
    itemLabel: '计量单位',
    columns: [
      { name: 'code', label: '单位码', width: 120 },
      { name: 'name', label: '单位名', width: 140 },
      { name: 'decimal_places', label: '小数位', width: 100, numeric: true },
    ],
    formFields: [
      { name: 'code', label: '单位码', placeholder: 'M / YD / PCS / KG' },
      { name: 'name', label: '单位名' },
      {
        name: 'decimal_places',
        label: '数量小数位',
        placeholder: '0~6，参与 numeric 精度对齐',
      },
      REMARK,
    ],
    filters: [],
  },
  'product-categories': {
    key: 'product-categories',
    title: '商品分类',
    itemLabel: '商品分类',
    columns: [
      { name: 'code', label: '分类码', width: 140 },
      { name: 'name', label: '分类名', width: 180 },
      { name: 'sort', label: '排序', width: 90, numeric: true },
    ],
    formFields: [
      { name: 'code', label: '分类码', placeholder: '全厂唯一' },
      { name: 'name', label: '分类名' },
      { name: 'sort', label: '排序', placeholder: '小的排前面' },
      REMARK,
    ],
    filters: [],
  },
  colors: {
    key: 'colors',
    title: '颜色',
    itemLabel: '颜色',
    columns: [
      { name: 'color_code', label: '色码', width: 110 },
      { name: 'name', label: '色名', width: 140 },
      { name: 'color_family', label: '色卡族', width: 150 },
      { name: 'pantone_code', label: '潘通号', width: 120 },
    ],
    formFields: [
      { name: 'color_code', label: '色码', placeholder: '全局唯一，如 NVY' },
      { name: 'name', label: '色名', placeholder: '如 藏青' },
      { name: 'color_family', label: '色卡族', placeholder: '如 Pantone TCX，可不填' },
      { name: 'pantone_code', label: '潘通号', placeholder: '要对色时补，可不填' },
      REMARK,
    ],
    filters: [{ name: 'color_family', label: '色卡族', control: 'text' }],
  },
  sizes: {
    key: 'sizes',
    title: '尺码',
    itemLabel: '尺码',
    columns: [
      { name: 'size_code', label: '尺码码', width: 110 },
      { name: 'name', label: '实际尺码', width: 180 },
      { name: 'size_class', label: '尺码类', width: 100 },
      { name: 'sort_order', label: '排序', width: 90, numeric: true },
    ],
    formFields: [
      { name: 'size_code', label: '尺码码', placeholder: '如 S / M / 3XL' },
      { name: 'name', label: '实际尺码', placeholder: '如 XL(170/92A)' },
      { name: 'size_class', label: '尺码类', options: SIZE_CLASS_OPTIONS },
      { name: 'sort_order', label: '排序', placeholder: '小的排前面' },
      REMARK,
    ],
    filters: [
      { name: 'size_class', label: '尺码类', control: 'enum', options: SIZE_CLASS_OPTIONS },
    ],
  },
  'size-groups': {
    key: 'size-groups',
    title: '尺码模板',
    itemLabel: '尺码模板',
    columns: [
      // ⚠️ 码表**没有编码列**（04 §7.4 取消了 `group_code`），`name` 本身就是唯一键，
      //    也是路径参数（`PATCH /size-groups/{name}`）。
      { name: 'name', label: '码表名', width: 220 },
      { name: 'size_class', label: '尺码类', width: 100 },
    ],
    formFields: [
      { name: 'name', label: '码表名', placeholder: '全局唯一，如 女款模板 S-M / L-XL' },
      { name: 'size_class', label: '尺码类', options: SIZE_CLASS_OPTIONS },
      // ⚠️ `items` 是**有序**尺码成员（`[{size_id, sort_order}]`，全量替换语义）。
      //    契约里它的 type 是 `list`、且**必填**（至少 1 个成员）—— 表单据此渲染
      //    「上下移动 + 移除」的有序编辑器，而不是一个逗号分隔的文本框。
      { name: 'items', label: '尺码成员（按顺序）' },
      REMARK,
    ],
    filters: [
      { name: 'size_class', label: '尺码类', control: 'enum', options: SIZE_CLASS_OPTIONS },
    ],
  },
  operations: {
    key: 'operations',
    title: '工序',
    itemLabel: '工序',
    columns: [
      { name: 'operation_no', label: '工序号', width: 110 },
      { name: 'name', label: '工序名', width: 160 },
      { name: 'is_piecework', label: '计件', width: 90 },
      { name: 'default_bundle_qty', label: '默认一扎件数', width: 130, numeric: true },
      { name: 'sort_order', label: '排序', width: 90, numeric: true },
    ],
    formFields: [
      { name: 'operation_no', label: '工序号', placeholder: '全厂唯一，如 01' },
      { name: 'name', label: '工序名', placeholder: '如 拼前' },
      { name: 'workshop_id', label: '所属车间', placeholder: '留空 = 通用工序' },
      { name: 'is_piecework', label: '是否计件工序' },
      { name: 'default_bundle_qty', label: '默认一扎件数', placeholder: '大于 0，最多 3 位小数' },
      { name: 'sort_order', label: '排序', placeholder: '小的排前面' },
      REMARK,
    ],
    filters: [
      { name: 'workshop_id', label: '所属车间', control: 'combo', source: 'workshops' },
      { name: 'is_piecework', label: '是否计件', control: 'bool' },
    ],
  },
} as const satisfies Record<string, BaseResourceDecl>

/** 注册表里的资源 key（`customers` 有端点但本卡不做页面，故不在此列）。 */
export type RegistryKey = keyof typeof RESOURCE_REGISTRY

export const REGISTRY_KEYS = Object.keys(RESOURCE_REGISTRY) as RegistryKey[]

/** 取某个资源的声明。传错 key 编译就红。 */
export function resourceDecl(key: RegistryKey): BaseResourceDecl {
  return RESOURCE_REGISTRY[key]
}

/** 取某个资源的后端契约（路径参数列 / 权限 / 必填字段）。 */
export function contractOf(key: RegistryKey): BaseDictContract {
  return BASE_DICT_CONTRACT[key]
}

/**
 * 可筛选字段白名单（后端 `resources.py::filter_columns` 展开后的五个 +
 * 两个由 `_apply_filters` 单独处理的开关）。
 *
 * ⚠️ 前端筛选项写错名字的后果不是报错，而是 `10001 {code}` —— 后端 `ListQuery.validate`
 *    会拒，而用户看到的是"查询失败，不知道为什么"。
 */
const FILTERABLE: ReadonlySet<string> = new Set([
  'is_active',
  'is_builtin',
  'workshop_id',
  'size_class',
  'warehouse_type',
  'color_family',
  'is_piecework',
])

/**
 * 编辑态**可改**的字段集合。
 *
 * ⚠️ 直接取后端 `patch.fields` —— 而不是"create 字段减去编码列"。两者的差集就是
 *    「建后不可改」的字段，而那个差集里确实有东西：**组别的所属车间**
 *    （`WorkshopGroupPatch` 只有 `name` / `remark`，§4.4 的写入模型如此）。
 *    前端若按"除了编码都能改"来渲染，编辑组别时能改车间 —— 提交必然被
 *    `extra=forbid` 的后端拒（`10001`），而界面上看不出任何异常。
 */
export function editableFieldNames(key: RegistryKey): ReadonlySet<string> {
  const contract = BASE_DICT_CONTRACT[key]
  return new Set<string>(
    contract.patch.fields.map((item) => item.name).filter((name) => name !== 'version'),
  )
}

/**
 * 校验一条声明与后端契约是否对得上。
 *
 * ## 为什么类型已经挡了一半还要运行时再挡一次
 *
 * `columns[].name` 有 `BaseRowField` 类型保护（写错编译就红），但
 * `formFields[].name` 只能是 `string` —— 因为表单字段名来自后端 `create.fields`，
 * 那是运行时数据。所以"表单里少了一个后端必填的字段"这类漂移只有运行时能抓，
 * 而它的症状是**用户填完提交才被 10001 拒**（后端
 * `tests/modules/test_base_resource_registry.py` 在源码级也查一遍，双保险）。
 *
 * @throws Error 声明与契约不一致时抛错，消息里指出差集。
 */
export function validateDecl(decl: BaseResourceDecl): void {
  const contract = BASE_DICT_CONTRACT[decl.key]
  const problems: string[] = []

  if (contract.key !== decl.key) problems.push('key 与契约不符')
  if (decl.title.trim() === '' || decl.itemLabel.trim() === '') {
    problems.push('title / itemLabel 不能为空（空标题会让菜单出现一个没名字的入口）')
  }

  // ⚠️ 显式写 `Set<string>`：契约是 `as const`，不写的话元素类型是该资源的**字面量
  //    联合**（`'color_code' | 'name' | …`），`has(任意字符串)` 会直接报 TS2345 ——
  //    而这里要检查的恰恰是"注册表写的名字在不在里面"。
  const formNames = new Set<string>(contract.create.fields.map((item) => item.name))
  for (const field of decl.formFields) {
    if (!formNames.has(field.name)) {
      problems.push(`表单字段 ${field.name} 不在 create 模型里（拼错了或后端已删）`)
    }
    if (
      contract.create.fields.find((item) => item.name === field.name)?.type === 'enum' &&
      !field.options
    ) {
      problems.push(`枚举字段 ${field.name} 缺中文映射（docs/06 §1：前端只做中文映射）`)
    }
  }
  const missing = contract.create.required.filter((name) => !decl.formFields.some((f) => f.name === name))
  if (missing.length > 0) {
    problems.push(`必填字段 ${missing.join('、')} 没在表单里出现 —— 用户没地方填`)
  }

  for (const filter of decl.filters) {
    if (!FILTERABLE.has(filter.name)) {
      problems.push(`筛选项 ${filter.name} 不在后端可筛选白名单里`)
    }
    if (filter.control === 'combo' && filter.source === undefined) {
      problems.push(`筛选项 ${filter.name} 是 combo 却没有候选来源资源`)
    }
  }

  if (problems.length > 0) {
    throw new Error(`${decl.key} 的声明与后端契约不一致：\n- ${problems.join('\n- ')}`)
  }
}

/**
 * 该资源的**必填字段清单**（新建用）。
 *
 * ⚠️ 直接来自后端模型，不在前端另写一份 —— 这是任务卡缺口③的答案。
 *    表单的红星、校验规则、以及"后端加了一个必填字段"的提示都读它。
 */
export function requiredFields(key: RegistryKey): readonly string[] {
  return BASE_DICT_CONTRACT[key].create.required
}

// ------------------------------------------------------------------ 请求封装

/** 写请求体。⚠️ 刻意不写死字段：九个资源的写入模型后端各自校验（`extra=forbid`）。 */
export type BaseDictPayload = Record<string, unknown>

export interface BaseResourceApi {
  readonly key: RegistryKey
  /** 该资源的写权限点（分类 / 工序与默认 `base:*` 不同，§4.4）。 */
  readonly permissions: BaseDictContract['permissions']
  /** 路径参数列（`color_code` / `operation_no` / 码表的 `name`）。 */
  readonly codeColumn: string
  list(query?: BaseListQuery): Promise<{ items: BaseDictRow[]; total: number }>
  /** 候选搜索，`value` 是**业务编码**（docs/05 §9.5.2）。 */
  options(keyword?: string): Promise<OptionOut[]>
  /**
   * 候选搜索，但 `value` 是**主键 id**。
   *
   * ⚠️ 为什么需要它：`workshop_id` / `size_id` 这类字段要的是 UUID，而
   *    `/options` 的 `value` 是业务编码（§9.5.2 明文规定）—— 直接拿来提交会被
   *    后端 `10001` 拒。所以走列表接口取行（`size ≤ 20` 与候选硬上限一致），
   *    再自己映射成 `{value: id}`。
   */
  optionsById(keyword?: string, extra?: BaseListQuery): Promise<OptionOut[]>
  get(code: string): Promise<BaseDictRow>
  create(payload: BaseDictPayload): Promise<BaseDictRow>
  /** 局部更新。⚠️ `version` 必传（后端不匹配返回 `10003`）。 */
  patch(code: string, payload: BaseDictPayload): Promise<BaseDictRow>
  /** 停用。原因必填（§4.4 / docs/06 §5）。 */
  disable(code: string, reason: string): Promise<DisableOut>
  remove(code: string): Promise<DeleteOut>
  /** 导出 xlsx（docs/05 §9.1，与列表**同一套筛选**）。 */
  exportXlsx(query?: BaseListQuery): Promise<DownloadResult>
}

/** 候选接口的硬上限（docs/05 §9.5.1「候选下拉强制 size ≤ 20」）。 */
const OPTION_LIMIT = 20

/** 组合式取值：`DictOut` 是"一个大模型 + 大量可空字段"，只能按名字动态取。 */
function field(row: BaseDictRow, name: string): unknown {
  return (row as unknown as Record<string, unknown>)[name]
}

/**
 * 生成一个资源的 8 个端点封装。
 *
 * ⚠️ 路径一律用 `contract.key` 拼，**不要在这里硬编码字符串**：后端改 URL 前缀时
 *    只有注册表一处要改，而漏改的后果是那个资源静默 404（页面上表现为"没有数据"）。
 */
function makeResourceApi(key: RegistryKey): BaseResourceApi {
  const contract = BASE_DICT_CONTRACT[key]
  const base = `/${contract.key}`

  return {
    key,
    permissions: contract.permissions,
    codeColumn: contract.codeColumn,

    async list(query = {}) {
      // ⚠️ 这里的返回类型是**手写**的（后端 `response_model` 是 `dict[str, Any]`，
      //    见本文件顶部注释）。字段名仍由 `BaseDictRow`（= 生成的 `DictOut`）约束。
      const result = await http.get<{
        items: BaseDictRow[]
        total: number
        page: number
        page_size: number
      }>(base, { query: { ...query } })
      return { items: result.items, total: result.total }
    },

    options(keyword = '') {
      return http.get<OptionOut[]>(`${base}/options`, { query: { q: keyword, size: OPTION_LIMIT } })
    },

    async optionsById(keyword = '', extra = {}) {
      const result = await this.list({ q: keyword, size: OPTION_LIMIT, ...extra })
      return result.items.map((row) => ({
        value: row.id,
        label: `${String(field(row, contract.codeColumn) ?? '')} ${String(field(row, contract.nameColumn) ?? '')}`.trim(),
        sub: null,
        disabled: row.is_active !== true,
      }))
    },

    get(code) {
      return http.get<BaseDictRow>(`${base}/${encodeURIComponent(code)}`)
    },

    create(payload) {
      return http.post<BaseDictRow>(base, payload)
    },

    patch(code, payload) {
      return http.patch<BaseDictRow>(`${base}/${encodeURIComponent(code)}`, payload)
    },

    disable(code, reason) {
      return http.post(`${base}/${encodeURIComponent(code)}/disables`, { reason })
    },

    remove(code) {
      return http.delete<DeleteOut>(`${base}/${encodeURIComponent(code)}`)
    },

    exportXlsx(query = {}) {
      // ⚠️ 后端 `exports` 与 `list` 共用同一个 service 方法，且**不加分页**
      //    （`repository.py::iter_export_rows`），所以这里把当前筛选原样传过去即可，
      //    不需要"先把 total 查出来再拼"。
      return http.download(`${base}/exports`, { query: { ...query } })
    },
  }
}

/** 九个资源的请求封装。key 与 `RESOURCE_REGISTRY` 一一对应。 */
export const baseApi = {
  workshops: makeResourceApi('workshops'),
  'workshop-groups': makeResourceApi('workshop-groups'),
  warehouses: makeResourceApi('warehouses'),
  'uom-units': makeResourceApi('uom-units'),
  'product-categories': makeResourceApi('product-categories'),
  colors: makeResourceApi('colors'),
  sizes: makeResourceApi('sizes'),
  'size-groups': makeResourceApi('size-groups'),
  operations: makeResourceApi('operations'),
} as const satisfies Record<RegistryKey, BaseResourceApi>

/** 把查询条件里的 `undefined` / 空串剔掉（导出与"筛选摘要"用）。 */
export function compactQuery(query: BaseListQuery): Record<string, QueryValue> {
  return Object.fromEntries(
    Object.entries(query).filter(([, value]) => value !== undefined && value !== ''),
  )
}