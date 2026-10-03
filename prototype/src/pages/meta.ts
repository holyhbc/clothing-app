export interface PageMeta { key: string; title: string; icon: string; group: string; sub?: string }

export const PAGES: PageMeta[] = [
  { key: 'dashboard', title: '生产看板', icon: '▦', group: '总览' },
  { key: 'product', title: '商品管理', icon: '▤', group: '总览', sub: '款号档案' },
  { key: 'base', title: '基础资料', icon: '☰', group: '总览' },
  { key: 'purchase', title: '采购单', icon: '⇩', group: '仓储' },
  { key: 'ledger', title: '采购台账', icon: '☷', group: '仓储', sub: '供应商维度' },
  { key: 'stockops', title: '库存操作', icon: '⇅', group: '仓储' },
  { key: 'sizeratio', title: '尺码比例', icon: '⚖', group: '基础资料' },
  { key: 'cutting', title: '裁剪单', icon: '✂', group: '生产' },
  { key: 'bundling', title: '打菲单', icon: '▣', group: '生产' },
  { key: 'station', title: '工位机计件', icon: '⌨', group: '生产', sub: '扫码枪' },
  { key: 'trace', title: '生产追踪', icon: '⇗', group: '生产', sub: '一码到底' },
  { key: 'piecework', title: '计件流水', icon: '⎔', group: '生产' },
  { key: 'payroll', title: '工资结算', icon: '¥', group: '生产' },
  { key: 'rates', title: '工序与单价', icon: '⌗', group: '基础资料' },
  { key: 'stock', title: '库存台账', icon: '▤', group: '仓储' },
  { key: 'sales', title: '销售订单', icon: '⇥', group: '经营' },
  { key: 'ar', title: '应收往来', icon: '⇄', group: '经营' },
  { key: 'voucher', title: '记账凭证', icon: '≡', group: '财务' },
  { key: 'perm', title: '角色权限', icon: '⚿', group: '系统' },
]

export const MOBILE_PAGES = [
  { key: 'm-home', title: '今日计件' },
  { key: 'm-range', title: '区间累计' },
  { key: 'm-bind', title: '绑定工序' },
  { key: 'm-me', title: '我的' },
  { key: 'm-payroll', title: '我的工资' },
] as const