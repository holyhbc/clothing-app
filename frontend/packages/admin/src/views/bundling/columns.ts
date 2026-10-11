import type { ColumnsType } from 'ant-design-vue/es/table'
import type { BundlingOrderListOut } from '@garment/shared'

/** 打菲单列表的列定义。纯数据，抽出来是为了让 `List.vue` 只管交互。 */
export const columns: ColumnsType<BundlingOrderListOut> = [
  { key: 'doc_no', title: '打菲单号', dataIndex: 'doc_no', width: 160 },
  { key: 'doc_date', title: '单据日期', dataIndex: 'doc_date', width: 110 },
  { key: 'status', title: '状态', dataIndex: 'status', width: 100 },
  { key: 'style_no', title: '款号', dataIndex: 'style_no', width: 140 },
  { key: 'operation_no', title: '工序', dataIndex: 'operation_no', width: 100 },
  { key: 'color_code', title: '商品分类', dataIndex: 'color_code', width: 100 },
  { key: 'hands_total', title: '码数(手)', dataIndex: 'hands_total', width: 100, align: 'right' },
  { key: 'output_qty', title: '出数', dataIndex: 'output_qty', width: 100, align: 'right' },
  { key: 'balance_qty', title: '余数', dataIndex: 'balance_qty', width: 90, align: 'right' },
  { key: 'label_print_qty', title: '已打印', dataIndex: 'label_print_qty', width: 90, align: 'right' },
  { key: 'actions', title: '操作', width: 120, fixed: 'right' },
]
