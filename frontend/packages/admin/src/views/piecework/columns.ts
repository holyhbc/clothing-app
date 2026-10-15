import type { ColumnsType } from 'ant-design-vue/es/table'
import type { PieceworkLogRowOut } from '@garment/shared'

/**
 * 计件流水台账的列定义（T-PIECE-005）。纯数据，抽出来是为了让 `List.vue` 只管交互。
 *
 * ⚠️ 列顺序按主管对账的视线走：**先定位（日期/员工/款号/工序/码），再数量金额**。
 *    `hands`（第几手）与 `qty`（这次做了几件）**必须分开** —— 见 `LogRowOut` 的注：
 *    混用会让主管看到「第 1 手做了 60 件」而其实是「第 2 手做了 28 件」（部分生产）。
 */
export const columns: ColumnsType<PieceworkLogRowOut> = [
  { key: 'work_date', title: '日期', dataIndex: 'work_date', width: 108 },
  { key: 'employee', title: '员工', width: 140 },
  { key: 'style_no', title: '款号', dataIndex: 'style_no', width: 130 },
  { key: 'operation_no', title: '工序', width: 140 },
  { key: 'bundle_no', title: '打菲号', dataIndex: 'bundle_no', width: 220 },
  { key: 'color_size', title: '色·码', width: 130 },
  { key: 'hands', title: '第几手', width: 96, align: 'right' },
  { key: 'qty', title: '件数', dataIndex: 'qty', width: 90, align: 'right' },
  { key: 'unit_price', title: '单价', dataIndex: 'unit_price', width: 100, align: 'right' },
  { key: 'amount', title: '金额', dataIndex: 'amount', width: 110, align: 'right' },
  { key: 'log_type', title: '类型', dataIndex: 'log_type', width: 90 },
  { key: 'is_settled', title: '结算', width: 90 },
  { key: 'actions', title: '操作', width: 90, fixed: 'right' },
]
