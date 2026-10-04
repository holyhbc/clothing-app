// ⚠️ 本文件由 scripts/generate-frontend-contract.mjs 生成，请勿手改。
// 改完请重跑 `node scripts/generate-frontend-contract.mjs`（docs/06 §7 禁止手写 DTO）。

/**
 * 九个基础资料资源的写入契约（docs/modules/01 §4；数据来自后端 `WRITE_MODELS`）。
 *
 * | 段 | 谁说了算 |
 * | --- | --- |
 * | `codeColumn` / `permissions` / `create` / `patch` | **后端**（本文件生成物） |
 * | 中文名、控件类型、表格列 | **前端**（`packages/admin/src/api/base.ts` 的注册表） |
 *
 * ⚠️ 必填字段清单只有一份，就在 `create.required` / `patch.required`。
 *    表单的必填红星与校验规则由它驱动，前端**不得**另写一份 —— 后端给某个
 *    模型加必填字段时，前端会立刻跟着变（重跑生成器即可），而不是让用户
 *    填完提交才被 `10001` 拒。
 */

/** 字段类型。与后端 pydantic 注解一一对应（未登记的类型会让生成器直接失败）。 */
export type BaseDictFieldType = "string" | "int" | "decimal" | "bool" | "uuid" | "enum" | "list"

export interface BaseDictField {
  /** 字段名 = 请求体里的 key，也是 `DictOut` 里的属性名。 */
  readonly name: string
  readonly type: BaseDictFieldType
  /** 允许 `null`（后端 `X | None = None`）。必填字段不会是 null。 */
  readonly nullable: boolean
  /** 仅 `type === 'enum'`：取值来自后端 PG enum，前端只做中文映射。 */
  readonly values?: readonly string[]
  /** 后端声明的默认值（表单初值）。缺省 = 无默认（必填，或默认就是 None）。 */
  readonly default?: string | number | boolean
}

export interface BaseDictWriteSpec {
  /** **必填字段清单**（pydantic 的 `required`）。表单的红星与校验都读它。 */
  readonly required: readonly string[]
  readonly fields: readonly BaseDictField[]
}

export interface BaseDictContract {
  /** 资源 key，同时是 URL 前缀（`/api/v1/{key}`）。 */
  readonly key: string
  /** 路径参数用的业务编码列（§4.4：`/colors/{color_code}`，不是 UUID）。 */
  readonly codeColumn: string
  readonly nameColumn: string
  /** 带 `is_builtin` 列（字典表有，组织表没有）→ 界面显示「内置」角标。 */
  readonly hasBuiltinFlag: boolean
  /** 允许**物理删除**（字典表）还是纯软删 —— 决定删除确认框的措辞。 */
  readonly allowPhysicalDelete: boolean
  /** 引用检查器的界面名（`style_operations` 等）。空 = 不参与引用检查。 */
  readonly refCheckers: readonly string[]
  /** 各动作的权限点。分类与工序与默认的 `base:*` 不同（§4.4）。 */
  readonly permissions: Readonly<
    Record<"create" | "update" | "disable" | "delete" | "export", string>
  >
  readonly create: BaseDictWriteSpec
  readonly patch: BaseDictWriteSpec
}

export const BASE_DICT_CONTRACT = {
  "workshops": {
    "key": "workshops",
    "codeColumn": "code",
    "nameColumn": "name",
    "hasBuiltinFlag": false,
    "allowPhysicalDelete": false,
    "refCheckers": [],
    "permissions": {
      "create": "base:create",
      "update": "base:update",
      "disable": "base:disable",
      "delete": "base:delete",
      "export": "base:export"
    },
    "create": {
      "required": ["code", "name"],
      "fields": [
        {
          "name": "code",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  },
  "workshop-groups": {
    "key": "workshop-groups",
    "codeColumn": "group_no",
    "nameColumn": "name",
    "hasBuiltinFlag": false,
    "allowPhysicalDelete": false,
    "refCheckers": [],
    "permissions": {
      "create": "base:create",
      "update": "base:update",
      "disable": "base:disable",
      "delete": "base:delete",
      "export": "base:export"
    },
    "create": {
      "required": ["workshop_id", "group_no", "name"],
      "fields": [
        {
          "name": "workshop_id",
          "nullable": false,
          "type": "uuid"
        },
        {
          "name": "group_no",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  },
  "warehouses": {
    "key": "warehouses",
    "codeColumn": "code",
    "nameColumn": "name",
    "hasBuiltinFlag": false,
    "allowPhysicalDelete": false,
    "refCheckers": [],
    "permissions": {
      "create": "base:create",
      "update": "base:update",
      "disable": "base:disable",
      "delete": "base:delete",
      "export": "base:export"
    },
    "create": {
      "required": ["code", "name", "warehouse_type"],
      "fields": [
        {
          "name": "code",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "warehouse_type",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "warehouse_type",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  },
  "uom-units": {
    "key": "uom-units",
    "codeColumn": "code",
    "nameColumn": "name",
    "hasBuiltinFlag": false,
    "allowPhysicalDelete": false,
    "refCheckers": [],
    "permissions": {
      "create": "base:create",
      "update": "base:update",
      "disable": "base:disable",
      "delete": "base:delete",
      "export": "base:export"
    },
    "create": {
      "required": ["code", "name", "decimal_places"],
      "fields": [
        {
          "name": "code",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "decimal_places",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "decimal_places",
          "nullable": true,
          "type": "int"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  },
  "product-categories": {
    "key": "product-categories",
    "codeColumn": "code",
    "nameColumn": "name",
    "hasBuiltinFlag": false,
    "allowPhysicalDelete": false,
    "refCheckers": [],
    "permissions": {
      "create": "base:category:manage",
      "update": "base:category:manage",
      "disable": "base:category:manage",
      "delete": "base:category:manage",
      "export": "base:category:manage"
    },
    "create": {
      "required": ["code", "name"],
      "fields": [
        {
          "name": "code",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "sort",
          "nullable": false,
          "type": "int",
          "default": 0
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "sort",
          "nullable": true,
          "type": "int"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  },
  "colors": {
    "key": "colors",
    "codeColumn": "color_code",
    "nameColumn": "name",
    "hasBuiltinFlag": true,
    "allowPhysicalDelete": true,
    "refCheckers": [],
    "permissions": {
      "create": "base:create",
      "update": "base:update",
      "disable": "base:disable",
      "delete": "base:delete",
      "export": "base:export"
    },
    "create": {
      "required": ["color_code", "name"],
      "fields": [
        {
          "name": "color_code",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "color_family",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "pantone_code",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "color_family",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "pantone_code",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  },
  "sizes": {
    "key": "sizes",
    "codeColumn": "size_code",
    "nameColumn": "name",
    "hasBuiltinFlag": true,
    "allowPhysicalDelete": true,
    "refCheckers": ["尺码模板"],
    "permissions": {
      "create": "base:create",
      "update": "base:update",
      "disable": "base:disable",
      "delete": "base:delete",
      "export": "base:export"
    },
    "create": {
      "required": ["size_code", "name", "size_class"],
      "fields": [
        {
          "name": "size_code",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "size_class",
          "nullable": false,
          "type": "enum",
          "values": ["MENS", "WOMENS", "KIDS"]
        },
        {
          "name": "sort_order",
          "nullable": false,
          "type": "int",
          "default": 0
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "size_class",
          "nullable": true,
          "type": "enum",
          "values": ["MENS", "WOMENS", "KIDS"]
        },
        {
          "name": "sort_order",
          "nullable": true,
          "type": "int"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  },
  "size-groups": {
    "key": "size-groups",
    "codeColumn": "name",
    "nameColumn": "name",
    "hasBuiltinFlag": true,
    "allowPhysicalDelete": true,
    "refCheckers": [],
    "permissions": {
      "create": "base:create",
      "update": "base:update",
      "disable": "base:disable",
      "delete": "base:delete",
      "export": "base:export"
    },
    "create": {
      "required": ["name", "size_class", "items"],
      "fields": [
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "size_class",
          "nullable": false,
          "type": "enum",
          "values": ["MENS", "WOMENS", "KIDS"]
        },
        {
          "name": "items",
          "nullable": false,
          "type": "list"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "size_class",
          "nullable": true,
          "type": "enum",
          "values": ["MENS", "WOMENS", "KIDS"]
        },
        {
          "name": "items",
          "nullable": true,
          "type": "list"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  },
  "operations": {
    "key": "operations",
    "codeColumn": "operation_no",
    "nameColumn": "name",
    "hasBuiltinFlag": false,
    "allowPhysicalDelete": false,
    "refCheckers": [],
    "permissions": {
      "create": "base:operation:manage",
      "update": "base:operation:manage",
      "disable": "base:operation:manage",
      "delete": "base:operation:manage",
      "export": "base:operation:manage"
    },
    "create": {
      "required": ["operation_no", "name"],
      "fields": [
        {
          "name": "operation_no",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "workshop_id",
          "nullable": true,
          "type": "uuid"
        },
        {
          "name": "is_piecework",
          "nullable": false,
          "type": "bool",
          "default": true
        },
        {
          "name": "default_bundle_qty",
          "nullable": false,
          "type": "decimal",
          "default": "1"
        },
        {
          "name": "sort_order",
          "nullable": false,
          "type": "int",
          "default": 0
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "workshop_id",
          "nullable": true,
          "type": "uuid"
        },
        {
          "name": "is_piecework",
          "nullable": true,
          "type": "bool"
        },
        {
          "name": "default_bundle_qty",
          "nullable": true,
          "type": "decimal"
        },
        {
          "name": "sort_order",
          "nullable": true,
          "type": "int"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  },
  "customers": {
    "key": "customers",
    "codeColumn": "code",
    "nameColumn": "name",
    "hasBuiltinFlag": false,
    "allowPhysicalDelete": false,
    "refCheckers": [],
    "permissions": {
      "create": "base:create",
      "update": "base:update",
      "disable": "base:disable",
      "delete": "base:delete",
      "export": "base:export"
    },
    "create": {
      "required": ["code", "name"],
      "fields": [
        {
          "name": "code",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "name",
          "nullable": false,
          "type": "string"
        },
        {
          "name": "short_name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "contact",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "phone",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "address",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "tax_no",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "settlement_period_days",
          "nullable": false,
          "type": "int",
          "default": 0
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    },
    "patch": {
      "required": ["version"],
      "fields": [
        {
          "name": "version",
          "nullable": false,
          "type": "int"
        },
        {
          "name": "name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "short_name",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "contact",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "phone",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "address",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "tax_no",
          "nullable": true,
          "type": "string"
        },
        {
          "name": "settlement_period_days",
          "nullable": true,
          "type": "int"
        },
        {
          "name": "remark",
          "nullable": true,
          "type": "string"
        }
      ]
    }
  }
} as const satisfies Record<string, BaseDictContract>

/** 资源 key 集合。前端注册表只能取这里的值（编译期即拦 typo）。 */
export type BaseDictKey = keyof typeof BASE_DICT_CONTRACT
