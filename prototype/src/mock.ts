/** 概念原型 mock 数据 —— 无后端交互 */

/* ---------- 枚举与映射（照 docs/06 §1 状态色映射） ---------- */
export type DocStatus = 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'REJECTED' | 'CANCELLED' | 'PAID'

export const STATUS_TEXT: Record<DocStatus, string> = {
  DRAFT: '草稿',
  SUBMITTED: '待审核',
  APPROVED: '已审核',
  REJECTED: '已驳回',
  CANCELLED: '已作废',
  PAID: '已发放',
}

export const STATUS_CLASS: Record<DocStatus, string> = {
  DRAFT: 'st-draft',
  SUBMITTED: 'st-submitted',
  APPROVED: 'st-approved',
  REJECTED: 'st-rejected',
  CANCELLED: 'st-cancelled',
  PAID: 'st-paid',
}

const num = (n: number | string): number => {
  if (typeof n === 'number') return n
  const v = Number(String(n).replace(/[,\s¥]/g, ''))
  return Number.isFinite(v) ? v : 0
}

/** 金额一律显示到分；字符串先剥离千分位与货币符号，避免 NaN */
export const money = (n: number | string): string =>
  num(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export const qtyFmt = (n: number | string): string =>
  num(n).toLocaleString('zh-CN', { maximumFractionDigits: 3 })

/* ---------- 看板 ---------- */
export const dashboard = {
  kpis: [
    { label: '本月生产件数', value: '12,480', unit: '件', sub: '较上月 +8.2%', trend: 'up', tip: '按裁剪审核口径统计' },
    { label: '本月计件工资', value: '¥ 186,420', unit: '', sub: '涉及 86 人', trend: '', tip: '已审核结算单合计' },
    { label: '在制款号', value: '17', unit: '个', sub: '3 个待审核', trend: '', tip: '' },
    { label: '库存预警', value: '6', unit: '项', sub: '3 项低于安全库存', trend: 'down', tip: '' },
  ],
  todos: [
    { type: '待审核', title: '裁剪单 CT-20261018-000142', sub: 'HB-2026-0018 · 裁 1,200 件', time: '10 分钟前', status: 'SUBMITTED' as DocStatus },
    { type: '待审核', title: '打菲单 BD-20261018-000031', sub: 'HB-2026-0018 · 打边 300 扎', time: '22 分钟前', status: 'SUBMITTED' as DocStatus },
    { type: '待审核', title: '计件结算单 PW-202610-001', sub: '缝制一组 · 2026-10-01 ~ 2026-10-15', time: '1 小时前', status: 'SUBMITTED' as DocStatus },
    { type: '库存预警', title: '面料 F-CT-000128（弹力斜纹）', sub: '可用 128 米，安全库存 200 米', time: '2 小时前', status: 'PENDING' as unknown as DocStatus },
  ],
  styleProgress: [
    { style: 'HB-2026-0018', name: '男式商务长袖衬衫', orderQty: 2400, cut: 2400, bundled: 2200, finished: 1180, delivery: '2026-10-28', state: '生产中' },
    { style: 'HB-2026-0021', name: '女装针织开衫', orderQty: 1200, cut: 1200, bundled: 1200, finished: 860, delivery: '2026-10-22', state: '接近完成' },
    { style: 'HB-2026-0024', name: '男式休闲西裤', orderQty: 900, cut: 900, bundled: 520, finished: 0, delivery: '2026-11-05', state: '打菲中' },
    { style: 'HB-2026-0027', name: '女装风衣', orderQty: 600, cut: 600, bundled: 0, finished: 0, delivery: '2026-11-12', state: '待裁' },
    { style: 'HB-2026-0031', name: '男式夹克', orderQty: 450, cut: 0, bundled: 0, finished: 0, delivery: '2026-11-20', state: '待投料' },
  ],
}

/* ---------- 裁剪单 ---------- */
export const cuttingOrders = [
  { docNo: 'CT-20261018-000142', styleNo: 'HB-2026-0018', colorGroup: 'RB-2601W 主白', workshop: '裁床组', fabric: 'F-CT-000128', fabricQty: 1280.5, output: 2400, balance: 0.5, waste: 62.4, wasteRate: 4.9, status: 'SUBMITTED' as DocStatus, user: '王裁缝', time: '2026-10-18 09:12', docDate: '2026-10-18' , hands: 40, lot: 'DY-2609-01 / B-2610-001' },
  { docNo: 'CT-20261018-000141', styleNo: 'HB-2026-0021', colorGroup: 'RB-2602K 卡其', workshop: '裁床组', fabric: 'F-KN-000214', fabricQty: 960.0, output: 1200, balance: 0, waste: 41.0, wasteRate: 4.3, status: 'APPROVED' as DocStatus, user: '王裁缝', time: '2026-10-18 08:40', docDate: '2026-10-18' , hands: 24, lot: 'DY-2607-04 / B-2611-011' },
  { docNo: 'CT-20261017-000139', styleNo: 'HB-2026-0018', colorGroup: 'RB-2601B 黑', workshop: '裁床组', fabric: 'F-CT-000128', fabricQty: 640.0, output: 1200, balance: 0, waste: 33.2, wasteRate: 5.2, status: 'APPROVED' as DocStatus, user: '李裁缝', time: '2026-10-17 16:20', docDate: '2026-10-17' , hands: 20, lot: 'DY-2609-02 / B-2610-002' },
  { docNo: 'CT-20261017-000138', styleNo: 'HB-2026-0024', colorGroup: 'RB-2603N 藏青', workshop: '裁剪一组', fabric: 'F-WO-000056', fabricQty: 720.0, output: 900, balance: 0, waste: 39.6, wasteRate: 5.5, status: 'REJECTED' as DocStatus, user: '张裁缝', time: '2026-10-17 14:05', docDate: '2026-10-17' , hands: 15, lot: 'DY-2608-03 / B-2604-021' },
  { docNo: 'CT-20261016-000136', styleNo: 'HB-2026-0027', colorGroup: 'RB-2604G 军绿', workshop: '裁剪二组', fabric: 'F-CT-000131', fabricQty: 810.0, output: 600, balance: 0, waste: 36.5, wasteRate: 4.5, status: 'DRAFT' as DocStatus, user: '张裁缝', time: '2026-10-16 11:30', docDate: '2026-10-16' , hands: 10, lot: 'DY-2608-03 / B-2604-022' },
  { docNo: 'CT-20261015-000134', styleNo: 'HB-2026-0021', colorGroup: 'RB-2602G 灰', workshop: '裁床组', fabric: 'F-KN-000214', fabricQty: 480.0, output: 600, balance: 0, waste: 22.1, wasteRate: 4.6, status: 'CANCELLED' as DocStatus, user: '王裁缝', time: '2026-10-15 10:00', docDate: '2026-10-15' , hands: 10, lot: 'DY-2607-04 / B-2611-012' },
]

export const cuttingDetail = {
  ...cuttingOrders[0],
  styleName: '男式商务长袖衬衫',
  customer: '恒博服饰（HengBo）',
  customerStyleNo: 'HB-SH-2601W',
  delivery: '2026-10-28',
  plys: 24,
  lines: [
    { color: '本白', colorCode: 'WHT', size: 'S(155/80A)', boltNo: 'B-2601-001', dyeLot: 'DY-2609-01', fabricQty: 160.1, usage: 0.667, output: 240, balance: 0 },
    { color: '本白', colorCode: 'WHT', size: 'M(160/84A)', boltNo: 'B-2601-002', dyeLot: 'DY-2609-01', fabricQty: 220.2, usage: 0.825, output: 267, balance: 0 },
    { color: '本白', colorCode: 'WHT', size: 'L(165/88A)', boltNo: 'B-2601-003', dyeLot: 'DY-2609-01', fabricQty: 288.4, usage: 1.067, output: 270, balance: 0 },
    { color: '本白', colorCode: 'WHT', size: 'XL(170/92A)', boltNo: 'B-2601-004', dyeLot: 'DY-2609-01', fabricQty: 296.3, usage: 1.117, output: 265, balance: 0.4 },
    { color: '本白', colorCode: 'WHT', size: 'XXL(175/96A)', boltNo: 'B-2601-005', dyeLot: 'DY-2609-01', fabricQty: 265.5, usage: 1.083, output: 245, balance: 0 },
  ],
  logs: [
    { who: '王裁缝', time: '2026-10-18 09:12', action: '创建单据', from: null, to: 'DRAFT' },
    { who: '王裁缝', time: '2026-10-18 09:14', action: '修改', from: 'DRAFT', to: 'DRAFT', reason: '按 BOM 重算了 M 码用量' },
    { who: '王裁缝', time: '2026-10-18 09:20', action: '提交审核', from: 'DRAFT', to: 'SUBMITTED' },
  ],
}

/* ---------- 打菲 ---------- */
export const bundlingOrders = [
  { docNo: 'BD-20261018-000031', styleNo: 'HB-2026-0018', operation: '打边', workshop: '缝制一组', colorGroup: 'RB-2601W 主白', bundleQty: 1, bundleCount: 300, printed: 0, counted: 0, status: 'SUBMITTED' as DocStatus, user: '组长 一', time: '2026-10-18 09:40' },
  { docNo: 'BD-20261018-000029', styleNo: 'HB-2026-0021', operation: '查勘', workshop: '检验组', colorGroup: 'RB-2602K 卡其', bundleQty: 12, bundleCount: 20, printed: 20, counted: 20, status: 'APPROVED' as DocStatus, user: '组长 二', time: '2026-10-18 08:05' },
  { docNo: 'BD-20261017-000026', styleNo: 'HB-2026-0018', operation: '拼前', workshop: '缝制一组', colorGroup: 'RB-2601B 黑', bundleQty: 1, bundleCount: 480, printed: 480, counted: 431, status: 'APPROVED' as DocStatus, user: '组长 一', time: '2026-10-17 17:20' },
  { docNo: 'BD-20261017-000024', styleNo: 'HB-2026-0024', operation: '打边', workshop: '缝制二组', colorGroup: 'RB-2603N 藏青', bundleQty: 1, bundleCount: 260, printed: 0, counted: 0, status: 'DRAFT' as DocStatus, user: '组长 三', time: '2026-10-17 13:00' },
]

export const bundleSamples = Array.from({ length: 12 }, (_, i) => ({
  bundleNo: `BD-20261018-000031-${String(i + 1).padStart(7, '0')}`,
  styleNo: 'HB-2026-0018',
  color: '本白',
  size: ['S', 'M', 'L', 'XL', 'XXL'][i % 5],
  op: '打边',
}))

/* ---------- 工序与单价 ---------- */
export const operations = [
  { op: '01', name: '打边', bundleQty: 1, piecework: true, active: true },
  { op: '02', name: '拼前', bundleQty: 1, piecework: true, active: true },
  { op: '03', name: '装袖', bundleQty: 1, piecework: true, active: true },
  { op: '04', name: '合缝', bundleQty: 1, piecework: true, active: true },
  { op: '05', name: '锁眼钉扣', bundleQty: 1, piecework: true, active: true },
  { op: '06', name: '查勘', bundleQty: 12, piecework: true, active: true },
  { op: '07', name: '整烫包装', bundleQty: 12, piecework: true, active: true, isFinal: true },
  { op: '08', name: '返修', bundleQty: 1, piecework: true, active: true },
  { op: '90', name: '抽检', bundleQty: 1, piecework: false, active: true },
  { op: '91', name: '样品确认', bundleQty: 1, piecework: false, active: false },
]

export const rateHistory = [
  { range: '2026-08-01 ~ 2026-08-31', price: '0.350000', tag: '已过期', cls: 'past' },
  { range: '2026-09-01 ~ 2026-10-15', price: '0.380000', tag: '已过期', cls: 'past' },
  { range: '2026-10-16 ~ 至今', price: '0.420000', tag: '当前生效', cls: 'current' },
  { range: '2026-11-01 ~ （待生效）', price: '0.450000', tag: '未来生效', cls: 'future' },
]

export const styleRateRows = [
  { op: '01', name: '打边', bundleQty: 1, price: '0.420000', from: '2026-10-16', to: '—' },
  { op: '02', name: '拼前', bundleQty: 1, price: '0.850000', from: '2026-09-01', to: '—' },
  { op: '03', name: '装袖', bundleQty: 1, price: '1.200000', from: '2026-09-01', to: '—' },
  { op: '04', name: '合缝', bundleQty: 1, price: '0.680000', from: '2026-09-01', to: '—' },
  { op: '05', name: '锁眼钉扣', bundleQty: 1, price: '0.450000', from: '2026-09-01', to: '—' },
  { op: '06', name: '查勘', bundleQty: 12, price: '0.350000', from: '2026-09-01', to: '—' },
  { op: '07', name: '整烫包装', bundleQty: 12, price: '0.900000', from: '2026-09-01', to: '—' },
]

/* ---------- 计件流水 ---------- */
export const pieceworkLogs = [
  { bundleNo: 'BD-20261017-000026-0000431', styleNo: 'HB-2026-0018', op: '拼前', employee: '陈美玲', no: 'E1024', group: '缝制一组', qty: 1, price: '0.850000', amount: '0.85', type: '扫码', date: '2026-10-17', settled: true },
  { bundleNo: 'BD-20261017-000026-0000432', styleNo: 'HB-2026-0018', op: '拼前', employee: '陈美玲', no: 'E1024', group: '缝制一组', qty: 1, price: '0.850000', amount: '0.85', type: '扫码', date: '2026-10-17', settled: true },
  { bundleNo: 'BD-20261017-000026-0000433', styleNo: 'HB-2026-0018', op: '拼前', employee: '林小燕', no: 'E1031', group: '缝制一组', qty: 1, price: '0.850000', amount: '0.85', type: '扫码', date: '2026-10-17', settled: false },
  { bundleNo: 'BD-20261017-000026-0000399', styleNo: 'HB-2026-0018', op: '拼前', employee: '陈美玲', no: 'E1024', group: '缝制一组', qty: -1, price: '0.850000', amount: '-0.85', type: '红冲', date: '2026-10-17', settled: false },
  { bundleNo: 'BD-20261017-000026-0000399', styleNo: 'HB-2026-0018', op: '拼前', employee: '林小燕', no: 'E1031', group: '缝制一组', qty: 1, price: '0.850000', amount: '0.85', type: '补录', date: '2026-10-17', settled: false },
  { bundleNo: 'BD-20261016-000021-0000210', styleNo: 'HB-2026-0021', op: '查勘', employee: '周敏', no: 'E1008', group: '检验组', qty: 12, price: '0.350000', amount: '4.20', type: '扫码', date: '2026-10-16', settled: true },
]

/* ---------- 结算周期与工资 ---------- */
export const periods = [
  { code: '2026-10-16 ~ 2026-10-31', from: '2026-10-16', to: '2026-10-31', source: '手工创建', status: 'OPEN', cnt: 2864, amount: '38,912.60' },
  { code: '2026-10-01 ~ 2026-10-15', from: '2026-10-01', to: '2026-10-15', source: '生成整月', status: 'OPEN', cnt: 7102, amount: '96,480.20' },
  { code: '2026-08-01 ~ 2026-09-30', from: '2026-08-01', to: '2026-09-30', source: '两月一次', status: 'CLOSED', cnt: 21460, amount: '291,336.40' },
  { code: '裁剪 20260901-20260930', from: '2026-09-01', to: '2026-09-30', source: '按裁剪完成日', status: 'CLOSED', cnt: 9832, amount: '133,684.00' },
]

export const payrollRows = [
  { no: 'E1024', name: '陈美玲', group: '缝制一组', qty: 1264, amount: '1,073.60', detail: '打边 320×0.42 / 拼前 480×0.85 / 查勘 464×0.35' },
  { no: 'E1031', name: '林小燕', group: '缝制一组', qty: 1188, amount: '1,022.40', detail: '打边 300×0.42 / 拼前 452×0.85 / 查勘 436×0.35' },
  { no: 'E1008', name: '周敏', group: '检验组', qty: 3420, amount: '1,197.00', detail: '查勘 2400×0.35 / 整烫 1020×0.35' },
  { no: 'E1042', name: '刘芳', group: '缝制二组', qty: 986, amount: '842.30', detail: '合缝 510×0.68 / 锁眼 476×0.45' },
  { no: 'E1055', name: '赵丽', group: '缝制二组', qty: 1102, amount: '928.10', detail: '合缝 562×0.68 / 锁眼 540×0.45' },
  { no: 'E1061', name: '孙丽娟', group: '缝制一组', qty: 1340, amount: '1,108.60', detail: '打边 340×0.42 / 拼前 500×0.85 / 查勘 500×0.35' },
]

/* ---------- 库存（批次） ---------- */
export const stocks = [
  { material: 'F-CT-000128', name: '弹力斜纹 本白', type: '面料', supplier: '苏州恒信纺织', color: '本白', width: '152.0', weight: '1,240.000', warehouse: '原料一仓', dyeLot: 'DY-2609-01', bolt: 'B-2610-001 ~ 005', qty: 286.4, cost: '28.500000', value: '8,162.40', safety: 200, status: '正常' },
  { material: 'F-CT-000128', name: '弹力斜纹 本白', type: '面料', supplier: '苏州恒信纺织', color: '本白', width: '150.5', weight: '520.000', warehouse: '原料一仓', dyeLot: 'DY-2609-02', bolt: 'B-2610-006 ~ 008', qty: 92.7, cost: '29.200000', value: '2,706.84', safety: 200, status: '低于安全库存' },
  { material: 'F-CT-000128', name: '弹力斜纹 黑', type: '面料', supplier: '苏州恒信纺织', color: '黑', width: '150.0', weight: '1,580.000', warehouse: '原料一仓', dyeLot: 'DY-2609-03', bolt: 'B-2609-021 ~ 026', qty: 412.0, cost: '28.500000', value: '11,742.00', safety: 200, status: '正常' },
  { material: 'F-KN-000214', name: '羊毛混纺针织 卡其', type: '面料', supplier: '绍兴华纺布业', color: '卡其', width: '160.0', weight: '1,920.000', warehouse: '原料二仓', dyeLot: 'DY-2607-04', bolt: 'B-2611-011 ~ 018', qty: 1240.5, cost: '76.500000', value: '94,898.25', safety: 300, status: '正常' },
  { material: 'F-CT-000131', name: '防绒卡其 军绿', type: '面料', supplier: '广州锦纺面料', color: '军绿', width: '145.0', weight: '780.000', warehouse: '原料一仓', dyeLot: 'DY-2608-03', bolt: 'B-2604-021 ~ 026', qty: 2140.0, cost: '78.000000', value: '166,920.00', safety: 200, status: '正常' },
  { material: 'T-ZL-000456', name: '树脂拉链 20# 藏青', type: '辅料', supplier: '义乌小商品城', color: '藏青', width: '—', weight: '—', warehouse: '辅料仓', dyeLot: '—', bolt: '—', qty: 18600, cost: '0.850000', value: '15,810.00', safety: 5000, status: '正常' },
  { material: 'T-FL-000091', name: '涤纶线 402# 本白', type: '辅料', supplier: '义乌小商品城', color: '本白', width: '—', weight: '—', warehouse: '辅料仓', dyeLot: '—', bolt: '—', qty: 3240, cost: '0.420000', value: '1,360.80', safety: 5000, status: '低于安全库存' },
]

/* ---------- 销售与往来 ---------- */
export const salesOrders = [
  { docNo: 'SO-20261002-000021', customer: '恒博服饰', styleNo: 'HB-2026-0018', color: '本白', qty: 2400, amount: '312,000.00', delivery: '2026-10-28', delivered: 0, status: 'SUBMITTED' as DocStatus },
  { docNo: 'SO-20261005-000024', customer: '云裳国际', styleNo: 'HB-2026-0021', color: '卡其', qty: 1200, amount: '168,000.00', delivery: '2026-10-22', delivered: 620, status: 'APPROVED' as DocStatus },
  { docNo: 'SO-20260912-000012', customer: '东尚贸易', styleNo: 'HB-2026-0011', color: '藏青', qty: 1500, amount: '186,000.00', delivery: '2026-10-08', delivered: 1500, status: 'APPROVED' as DocStatus },
  { docNo: 'SO-20261010-000028', customer: '锦程制衣', styleNo: 'HB-2026-0015', color: '黑', qty: 800, amount: '92,000.00', delivery: '2026-11-02', delivered: 0, status: 'DRAFT' as DocStatus },
]

export const arLedger = [
  { customer: '东尚贸易', source: 'SO-20260912-000012', date: '2026-09-20', amount: '186,000.00', settled: '186,000.00', balance: '0.00', aging: 0 },
  { customer: '云裳国际', source: 'SH-20261016-000041', date: '2026-10-16', amount: '86,800.00', settled: '50,000.00', balance: '36,800.00', aging: 2 },
  { customer: '恒博服饰', source: 'SH-20261008-000035', date: '2026-10-08', amount: '124,000.00', settled: '0.00', balance: '124,000.00', aging: 10 },
  { customer: '云裳国际', source: 'SH-20260926-000028', date: '2026-09-26', amount: '52,000.00', settled: '0.00', balance: '52,000.00', aging: 22 },
  { customer: '锦程制衣', source: 'SH-20260905-000014', date: '2026-09-05', amount: '46,000.00', settled: '20,000.00', balance: '26,000.00', aging: 43 },
]

/* ---------- 凭证 ---------- */
export const vouchers = [
  { docNo: 'VO-2026-10-000128', date: '2026-10-18', source: '裁剪单 CT-20261018-000142', desc: '裁剪耗料结转', debit: '54,231.60', credit: '54,231.60', status: 'DRAFT' },
  { docNo: 'VO-2026-10-000127', date: '2026-10-18', source: '销售出库 SH-20261018-000046', desc: '发货确认收入 + 结转成本', debit: '142,000.00', credit: '142,000.00', status: 'POSTED' },
  { docNo: 'VO-2026-10-000126', date: '2026-10-17', source: '采购入库 RC-20261017-000121', desc: '面料入库（含运费分摊）', debit: '186,420.00', credit: '186,420.00', status: 'POSTED' },
  { docNo: 'VO-2026-10-000125', date: '2026-10-15', source: '工资单 PR-20261015-002', desc: '计件工资计提', debit: '96,480.20', credit: '96,480.20', status: 'POSTED' },
  { docNo: 'VO-2026-10-000124', date: '2026-10-15', source: '工资单 PR-20261015-002', desc: '工资发放付款', debit: '96,480.20', credit: '96,480.20', status: 'POSTED' },
]

/* ---------- 角色权限 ---------- */
export const roles = [
  { code: 'super_admin', name: '系统管理员', scope: '全厂', users: 2 },
  { code: 'factory_manager', name: '厂长', scope: '全厂', users: 1 },
  { code: 'workshop_supervisor', name: '车间主管', scope: '本车间', users: 4 },
  { code: 'line_leader', name: '组长', scope: '本组', users: 12 },
  { code: 'warehouse_keeper', name: '仓管', scope: '全厂', users: 3 },
  { code: 'accountant', name: '财务', scope: '全厂', users: 2 },
  { code: 'piecework_settler', name: '计件员', scope: '全厂', users: 1 },
  { code: 'employee', name: '员工', scope: '仅本人', users: 86 },
]

export const permMatrix: Record<string, string[]> = {
  '裁剪': ['cutting:read', 'cutting:create', 'cutting:approve', 'cutting:reverse', 'cutting:export'],
  '打菲': ['bundling:read', 'bundling:create', 'bundling:approve', 'bundling:print', 'bundling:code:void'],
  '计件': ['piecework:read', 'piecework:count', 'piecework:manual', 'piecework:rate:manage', 'piecework:reverse'],
  '工资': ['payroll:read', 'payroll:settle', 'payroll:approve', 'payroll:pay', 'payroll:export'],
  '库存': ['stock:read', 'stock:in', 'stock:out', 'stock:transfer', 'stock:cost:view'],
  '销售': ['sales:read', 'sales:order:create', 'sales:ship', 'sales:approve', 'sales:export'],
  '往来': ['finance:ar:read', 'finance:receipt', 'finance:settle', 'finance:statement', 'finance:export'],
  '凭证': ['finance:voucher:read', 'finance:voucher:post', 'finance:voucher:reverse', 'finance:period:close', 'finance:period:reopen'],
}

export const roleGrants: Record<string, string[]> = {
  '厂长': ['裁剪', '打菲', '计件', '工资', '库存', '销售', '往来', '凭证'],
  '车间主管': ['裁剪', '打菲', '计件', '工资'],
  '仓管': ['库存'],
  '财务': ['往来', '凭证'],
  '计件员': ['计件', '工资'],
  '员工': [],
}

/* ===================== 采购（ADR-0012） ===================== */
export const purchaseOrders = [
  { docNo: 'PO-20261018-000012', supplier: '盛虹纺织', date: '2026-10-18', arrived: '2026-10-18', warehouse: '原料一仓', lines: 3, total: '186,420.00', status: 'SUBMITTED' as DocStatus, user: '采购 陈明' },
  { docNo: 'PO-20261017-000011', supplier: '华鼎面料', date: '2026-10-17', arrived: '2026-10-17', warehouse: '原料一仓', lines: 1, total: '68,340.00', status: 'APPROVED' as DocStatus, user: '采购 陈明' },
  { docNo: 'PO-20261015-000009', supplier: '永新辅料', date: '2026-10-15', arrived: '2026-10-16', warehouse: '辅料仓', lines: 5, total: '24,860.00', status: 'APPROVED' as DocStatus, user: '采购 刘洋' },
  { docNo: 'PO-20261012-000008', supplier: '协作厂返修', date: '2026-10-12', arrived: '2026-10-12', warehouse: '返修布仓', lines: 2, total: '8,420.00', status: 'APPROVED' as DocStatus, user: '采购 刘洋' },
  { docNo: 'PO-20261011-000007', supplier: '盛虹纺织', date: '2026-10-11', arrived: '—', warehouse: '原料一仓', lines: 4, total: '142,000.00', status: 'DRAFT' as DocStatus, user: '采购 陈明' },
]

export const purchaseLines = [
  { lineNo: 1, material: 'F-CT-000128', name: '弹力斜纹 本白', color: 'WHT 本白', dyeLot: 'DY-2609-01', purpose: 'NORMAL 正常采购', bolt: 12, perBolt: '50.0', totalM: '600.0', weight: '732.5', width: '152.0', price: '28.50', amount: '17,100.00', remark: '门幅 152 常规' },
  { lineNo: 2, material: 'F-CT-000128', name: '弹力斜纹 本白', color: 'WHT 本白', dyeLot: 'DY-2609-02', purpose: 'NORMAL 正常采购', bolt: 6, perBolt: '48.5', totalM: '291.0', weight: '352.4', width: '150.5', price: '29.20', amount: '8,497.20', remark: '门幅略窄' },
  { lineNo: 3, material: 'F-CT-000128', name: '弹力斜纹 本白', color: 'WHT 本白', dyeLot: 'DY-2609-03', purpose: 'REWORK_RECEIPT 返修补回', bolt: 2, perBolt: '36.2', totalM: '72.4', weight: '88.6', width: '149.0', price: '15.00', amount: '1,086.00', rework: 'HB-2026-0018 黑色第二批返修退回，来自协作厂（免费）' },
]

/* ===================== 尺码比例（ADR-0013） ===================== */
export const sizeRatios = [
  { color: 'WHT 本白', size: 'S(155/80A)', code: '1.0000' },
  { color: 'WHT 本白', size: 'M(160/84A)', code: '2.0000' },
  { color: 'WHT 本白', size: 'L(165/88A)', code: '3.0000' },
  { color: 'WHT 本白', size: 'XL(170/92A)', code: '3.0000' },
  { color: 'WHT 本白', size: '2XL(175/96A)', code: '2.0000' },
  { color: 'BLK 黑', size: 'S(155/80A)', code: '1.0000' },
  { color: 'BLK 黑', size: 'M(160/84A)', code: '2.0000' },
  { color: 'BLK 黑', size: 'L(165/88A)', code: '4.0000' },
  { color: 'BLK 黑', size: 'XL(170/92A)', code: '2.0000' },
  { color: 'BLK 黑', size: '2XL(175/96A)', code: '1.0000' },
]

export const cuttingCalc = {
  styleNo: 'HB-2026-0018',
  color: 'RED 红色',
  qtyPerHand: 50,
  totalHands: 6,
  rows: [
    { size: 'L(165/88A)', ratio: '1.0000', hands: '1.0000', qty: 50 },
    { size: 'XL(170/92A)', ratio: '2.0000', hands: '2.0000', qty: 100 },
    { size: 'XXL(175/96A)', ratio: '2.0000', hands: '2.0000', qty: 100 },
    { size: '3XL(180/96A)', ratio: '1.0000', hands: '1.0000', qty: 50 },
  ],
}

/* ===================== 商品 / 款号档案 ===================== */
export const styles = [
  { styleNo: 'HB-2026-0018', cat: 'DRESS', name: '男式商务长袖衬衫', customer: '恒博服饰', season: '2601 秋冬', delivery: '2026-10-28', orderQty: 2400, cutQty: 2400, bundled: 2200, finished: 1180, colors: 4, sizes: 6, fabricCost: '92,640.00', laborCost: '58,420.00', saleAmount: '312,000.00', margin: '38.6', state: '生产中' },
  { styleNo: 'HB-2026-0021', cat: 'SET', name: '女装针织开衫', customer: '云裳国际', season: '2602 秋冬', delivery: '2026-10-22', orderQty: 1200, cutQty: 1200, bundled: 1200, finished: 860, colors: 3, sizes: 5, fabricCost: '46,200.00', laborCost: '24,800.00', saleAmount: '168,000.00', margin: '40.1', state: '接近完成' },
  { styleNo: 'HB-2026-0024', cat: 'TROUSERS', name: '男式休闲西裤', customer: '锦程制衣', season: '2602 秋冬', delivery: '2026-11-05', orderQty: 900, cutQty: 900, bundled: 520, finished: 0, colors: 2, sizes: 6, fabricCost: '38,700.00', laborCost: '0.00', saleAmount: '0.00', margin: '—', state: '打菲中' },
  { styleNo: 'HB-2026-0027', cat: 'DRESS', name: '女装风衣', customer: '东尚贸易', season: '2602 秋冬', delivery: '2026-11-12', orderQty: 600, cutQty: 600, bundled: 0, finished: 0, colors: 2, sizes: 5, fabricCost: '31,200.00', laborCost: '0.00', saleAmount: '0.00', margin: '—', state: '待打菲' },
  { styleNo: 'HB-2026-0031', cat: 'SET', name: '男式夹克', customer: '恒博服饰', season: '2603 春夏', delivery: '2026-11-20', orderQty: 450, cutQty: 0, bundled: 0, finished: 0, colors: 3, sizes: 6, fabricCost: '0.00', laborCost: '0.00', saleAmount: '0.00', margin: '—', state: '待投料' },
  { styleNo: 'HB-2026-0009', cat: 'DRESS', name: '女装 A 字半裙', customer: '云裳国际', season: '2601 秋冬', delivery: '2026-10-08', orderQty: 1500, cutQty: 1500, bundled: 1500, finished: 1500, colors: 3, sizes: 5, fabricCost: '52,500.00', laborCost: '31,200.00', saleAmount: '186,000.00', margin: '54.9', state: '已完工' },
]

/* ===================== 基础资料字典 ===================== */
export const colors = [
  { code: 'WHT', name: '本白', family: 'Pantone 11-0605 TCX', builtin: true, refs: 42 },
  { code: 'BLK', name: '黑', family: 'Pantone Black 6 C', builtin: true, refs: 38 },
  { code: 'NVY', name: '藏青', family: 'Pantone 19-4052 TCX', builtin: true, refs: 16 },
  { code: 'KHK', name: '卡其', family: 'Pantone 18-1215 TCX', builtin: true, refs: 11 },
  { code: 'GRN', name: '军绿', family: 'Pantone 19-0622 TCX', builtin: true, refs: 7 },
  { code: 'BEG', name: '米色', family: 'Pantone 14-1110 TCX', builtin: true, refs: 5 },
  { code: 'RED', name: '正红', family: 'Pantone 19-1662 TCX', builtin: true, refs: 9 },
  { code: 'GRY', name: '深灰', family: 'Pantone 17-5103 TCX', builtin: true, refs: 14 },
]

export const sizeClasses = [
  { cls: '女款模板 S-M / L-XL', kind: '内置 WOMENS', sizes: 'S  M  L  XL', count: 4, refs: 24, builtin: true },
  { cls: '男款模板 L XL XXL 3XL', kind: '内置 MENS', sizes: 'L  XL  XXL  3XL', count: 4, refs: 31, builtin: true },
  { cls: '童装 100-160（自建）', kind: '自建 KIDS', sizes: '100 110 120 130 140 150 160', count: 7, refs: 3, builtin: false },
]

export const materials = [
  { code: 'F-CT-000128', name: '弹力斜纹', cat: '面料', comp: '棉 97% 氨纶 3%', width: '150.0', unit: '米', price: '28.50', spec: '幅宽 150cm 克重 260g/m²', refs: 6 },
  { code: 'F-KN-000214', name: '羊毛混纺针织', cat: '面料', comp: '羊毛 50% 腈纶 50%', width: '160.0', unit: '米', price: '76.00', spec: '幅宽 160cm', refs: 3 },
  { code: 'F-CT-000131', name: '防绒卡其', cat: '面料', comp: '棉 88% 聚酯 12%', width: '145.0', unit: '米', price: '34.80', spec: '幅宽 145cm 克重 320g/m²', refs: 4 },
  { code: 'T-ZL-000456', name: '树脂拉链 20#', cat: '辅料', comp: '尼龙', width: '—', unit: '条', price: '0.85', spec: '20# 55cm 藏青', refs: 12 },
  { code: 'T-FL-000091', name: '涤纶线 402#', cat: '辅料', comp: '聚酯', width: '—', unit: '个', price: '0.42', spec: '402# 本白', refs: 9 },
]

export const customers = [
  { code: 'C-HB-001', name: '恒博服饰有限公司', contact: '张经理', phone: '138****6621', settle: '月结 30 天', credit: '¥500,000', balance: '¥124,000.00', active: true },
  { code: 'C-YS-002', name: '云裳国际', contact: '李主管', phone: '139****3388', settle: '月结 45 天', credit: '¥300,000', balance: '¥88,800.00', active: true },
  { code: 'C-DS-003', name: '东尚贸易有限公司', contact: '王总', phone: '136****9900', settle: '款到发货', credit: '¥200,000', balance: '¥0.00', active: true },
  { code: 'C-JC-004', name: '锦程制衣', contact: '陈小姐', phone: '137****1122', settle: '月结 30 天', credit: '¥150,000', balance: '¥26,000.00', active: false },
]

/* ===================== Excel 导入校验结果（演示） ===================== */
export const importDemo = {
  total: 128,
  valid: 120,
  errors: [
    { row: 7, field: 'dye_lot_no', value: '(空)', msg: '缸号不能为空' },
    { row: 23, field: 'width_cm', value: '约1.5', msg: '门幅必须是 > 0 的数字（cm）' },
    { row: 41, field: 'supplier', value: 'SUP-0009', msg: '供应商编码 SUP-0009 不存在或已停用' },
    { row: 58, field: 'purpose', value: '返修', msg: '用途取值非法，应为 NORMAL / REWORK_RECEIPT / RETURN / SAMPLE' },
    { row: 66, field: 'weight_kg', value: '-50', msg: '重量必须 > 0' },
    { row: 92, field: 'color_code', value: 'WT', msg: '颜色编码 WT 不存在，请用 WHT（本白）' },
    { row: 108, field: 'bolt_count', value: '0', msg: '匹数必须 > 0' },
    { row: 119, field: 'doc_date', value: '2026/13/40', msg: '日期格式非法，应为 YYYY-MM-DD' },
  ],
}

/* ===================== 员工端时间段汇总 ===================== */
export const myRange = [
  { label: '今天', from: '10-18', to: '10-18', qty: 128, amount: '53.76', days: 1 },
  { label: '本周（10-13 ~ 10-18）', from: '10-13', to: '10-18', qty: 584, amount: '245.28', days: 6 },
  { label: '本月（10-01 ~ 10-18）', from: '10-01', to: '10-18', qty: 3214, amount: '1286.40', days: 18 },
  { label: '上月（9-01 ~ 9-30）', from: '09-01', to: '09-30', qty: 2146, amount: '901.32', days: 30 },
  { label: '近 7 天', from: '10-12', to: '10-18', qty: 792, amount: '332.64', days: 7 },
]

export const myDaily = [
  { d: '10-12', qty: 168, amount: '70.56' }, { d: '10-13', qty: 144, amount: '60.48' },
  { d: '10-14', qty: 112, amount: '47.04' }, { d: '10-15', qty: 136, amount: '57.12' },
  { d: '10-16', qty: 104, amount: '43.68' }, { d: '10-17', qty: 128, amount: '53.76' },
  { d: '10-18', qty: 128, amount: '53.76' },
]


/* ===================== ADR-0014 裁剪三种录入模式 ===================== */
export const cutModes = [
  { mode: 'MASTER', name: '按比例带出', desc: '常规：比例固定，只填每手件数', icon: 'A' },
  { mode: 'UNIFORM', name: '统一件数', desc: '所有尺码件数一样', icon: 'B' },
  { mode: 'MANUAL', name: '自定义明细', desc: '个别尺码数量不一致，可增删改', icon: 'C' },
] as const

export const cutLines = {
  MASTER: [
    { lineNo: 1, size: 'L(165/88A)', hands: '1.0000', perHand: '60', outputQty: 60, from: '比例建议' },
    { lineNo: 2, size: 'XL(170/92A)', hands: '2.0000', perHand: '60', outputQty: 120, from: '比例建议' },
    { lineNo: 3, size: 'XXL(175/96A)', hands: '2.0000', perHand: '60', outputQty: 120, from: '比例建议' },
    { lineNo: 4, size: '3XL(180/100A)', hands: '1.0000', perHand: '60', outputQty: 60, from: '比例建议' },
  ],
  MANUAL: [
    { lineNo: 1, size: 'L(165/88A)', hands: '1.0000', perHand: '60', outputQty: 60, from: '比例建议' },
    { lineNo: 2, size: 'XL(170/92A)', hands: '2.0000', perHand: '60', outputQty: 120, from: '比例建议' },
    { lineNo: 3, size: 'XL(170/92A)', hands: '1.0000', perHand: '30', outputQty: 30, from: '★ 新增行' },
    { lineNo: 4, size: 'XXL(175/96A)', hands: '2.0000', perHand: '60', outputQty: 120, from: '比例建议' },
    { lineNo: 5, size: '3XL(180/100A)', hands: '1.0000', perHand: '60', outputQty: 60, from: '比例建议' },
  ],
  UNIFORM: [
    { lineNo: 1, size: 'L(165/88A)', hands: '1.6667', perHand: '60', outputQty: 100, from: '统一 100 件' },
    { lineNo: 2, size: 'XL(170/92A)', hands: '1.6667', perHand: '60', outputQty: 100, from: '统一 100 件' },
    { lineNo: 3, size: 'XXL(175/96A)', hands: '1.6667', perHand: '60', outputQty: 100, from: '统一 100 件' },
    { lineNo: 4, size: '3XL(180/100A)', hands: '1.6667', perHand: '60', outputQty: 100, from: '统一 100 件' },
  ],
}

/* ===================== ADR-0015 分批到货 ===================== */
export const arrivals = [
  { no: 1, date: '2026-10-18 09:30', lines: 2, bolts: '12 匹', amount: '164,540.00', freight: '1,480.00', operator: '采购 陈明', remark: '全部完好', stockIds: 'B-2610-001 ~ 012' },
  { no: 2, date: '2026-10-21 14:10', lines: 1, bolts: '6 匹', amount: '84,972.00', freight: '740.00', operator: '采购 陈明', remark: '其中 1 匹外包装破损，已拍照留档', stockIds: 'B-2610-013 ~ 018' },
  { no: 3, date: '2026-10-25 10:05', lines: 1, bolts: '2 匹', amount: '10,860.00', freight: '0.00', operator: '采购 刘洋', remark: '返修补回布（协作厂免费退）', stockIds: 'B-2610-019 ~ 020' },
]


/* ===================== ADR-0016 打菲按手（一码一手） ===================== */
export const bundleHands = [
  { size: 'L(165/88A)', hands: 1, cuttingQty: 60, codes: [
    { no: 1, bundleNo: 'BD-20261018-000031-L01-0001', bundleQty: 60, employee: '王海涛', time: '09:44', state: '已计' },
  ] },
  { size: 'XL(170/92A)', hands: 2, cuttingQty: 120, codes: [
    { no: 1, bundleNo: 'BD-20261018-000031-XL01-0001', bundleQty: 60, employee: '陈美玲', time: '09:44', state: '已计' },
    { no: 2, bundleNo: 'BD-20261018-000031-XL02-0001', bundleQty: 60, employee: '林小燕', time: '—', state: '未计' },
  ] },
  { size: 'XXL(175/96A)', hands: 2, cuttingQty: 120, codes: [
    { no: 1, bundleNo: 'BD-20261018-000031-XXL01-0001', bundleQty: 60, employee: '刘芳', time: '—', state: '未计' },
    { no: 2, bundleNo: 'BD-20261018-000031-XXL02-0001', bundleQty: 60, employee: '—', time: '—', state: '未计' },
  ] },
  { size: '3XL(180/100A)', hands: 1, cuttingQty: 60, codes: [
    { no: 1, bundleNo: 'BD-20261018-000031-3XL01-0001', bundleQty: 60, employee: '—', time: '—', state: '未计' },
  ] },
]

/* ===================== ADR-0017 裁剪行=布批 + 行内多色 ===================== */
export const cuttingBatches = [
  {
    lineNo: 1, styleNo: 'HB-2026-0018', dyeLot: 'DY-2609-01', bolt: 'B-2610-001',
    width: '152.0', weight: '61.0', fabricQty: '96.0', waste: '1.8',
    colors: [
      { code: 'WHT', name: '本白', mode: 'MASTER', perHand: '60', qty: 360, detail: [
        { size: 'L(165/88A)', hands: '1.0000', perHand: '60', qty: 60 },
        { size: 'XL(170/92A)', hands: '2.0000', perHand: '60', qty: 120 },
        { size: 'XXL(175/96A)', hands: '2.0000', perHand: '60', qty: 120 },
        { size: '3XL(180/100A)', hands: '1.0000', perHand: '60', qty: 60 },
      ] },
      { code: 'BLK', name: '黑', mode: 'MASTER', perHand: '50', qty: 250, detail: [
        { size: 'L(165/88A)', hands: '1.0000', perHand: '50', qty: 50 },
        { size: 'XL(170/92A)', hands: '2.0000', perHand: '50', qty: 100 },
        { size: 'XXL(175/96A)', hands: '2.0000', perHand: '50', qty: 100 },
      ] },
    ],
  },
  {
    lineNo: 2, styleNo: 'HB-2026-0018', dyeLot: 'DY-2609-02', bolt: 'B-2610-002',
    width: '150.5', weight: '29.4', fabricQty: '31.5', waste: '0.6',
    colors: [
      { code: 'WHT', name: '本白', mode: 'MANUAL', perHand: '60', qty: 270, detail: [
        { size: 'XL(170/92A)', hands: '2.0000', perHand: '60', qty: 120 },
        { size: 'XL(170/92A)', hands: '1.0000', perHand: '30', qty: 30, tag: '★追加行' },
        { size: 'XXL(175/96A)', hands: '2.0000', perHand: '60', qty: 120 },
      ] },
    ],
  },
]

/* ===================== ADR-0020 商品分类 ===================== */
export const productCategories = [
  { code: 'SET', name: '套装', sort: 1, styles: 3, active: true },
  { code: 'DRESS', name: '单衣', sort: 2, styles: 5, active: true },
  { code: 'TROUSERS', name: '单裤', sort: 3, styles: 4, active: true },
  { code: 'UNDERWEAR', name: '棉毛', sort: 4, styles: 1, active: true },
  { code: 'VEST', name: '背心', sort: 5, styles: 2, active: true },
  { code: 'THERMAL', name: '打底裤', sort: 6, styles: 2, active: true },
]

/* ===================== ADR-0022 采购台账（供应商维度） ===================== */
export const suppliers = [
  { code: 'S-001', name: '苏州恒信纺织', contact: '周主管', phone: '139****2210', settle: '月结 30 天' },
  { code: 'S-002', name: '绍兴华纺布业', contact: '沈老板', phone: '137****8845', settle: '款到发货' },
  { code: 'S-003', name: '广州锦纺面料', contact: '黄经理', phone: '135****6690', settle: '月结 45 天' },
]

export const ledgerSummary = [
  { supplier: '苏州恒信纺织', receipts: 4, weight: '4,860.0', bolts: 62, goods: '458,200.00', freight: '3,600.00', dyeing: '12,400.00', processing: '0.00', payable: '458,200.00', paid: '300,000.00', unpaid: '158,200.00', rate: '65.5%' },
  { supplier: '绍兴华纺布业', receipts: 2, weight: '1,920.0', bolts: 24, goods: '146,880.00', freight: '1,480.00', dyeing: '0.00', processing: '2,860.00', payable: '146,880.00', paid: '146,880.00', unpaid: '0.00', rate: '100%' },
  { supplier: '广州锦纺面料', receipts: 1, weight: '780.0', bolts: 10, goods: '60,840.00', freight: '740.00', dyeing: '0.00', processing: '0.00', payable: '60,840.00', paid: '0.00', unpaid: '60,840.00', rate: '0%' },
]

export const ledgerDetails = [
  { date: '2026-10-25', supplier: '苏州恒信纺织', material: '弹力斜纹', color: '本白', dyeLot: 'DY-2610-05', weight: '120.000', price: '28.500000', amount: '3,420.00', po: 'PO-20261020-000041', arr: 'AR-20261025-000003', user: '采购 陈明' },
  { date: '2026-10-18', supplier: '苏州恒信纺织', material: '弹力斜纹', color: '本白', dyeLot: 'DY-2609-01', weight: '1,240.000', price: '28.500000', amount: '35,340.00', po: 'PO-20260915-000037', arr: 'AR-20261018-000001', user: '采购 陈明' },
  { date: '2026-10-12', supplier: '绍兴华纺布业', material: '防绒卡其', color: '军绿', dyeLot: 'DY-2608-03', weight: '1,920.000', price: '76.500000', amount: '146,880.00', po: 'PO-20261008-000039', arr: 'AR-20261012-000002', user: '采购 刘洋' },
  { date: '2026-10-05', supplier: '广州锦纺面料', material: '羊毛混纺针织', color: '卡其', dyeLot: 'DY-2607-04', weight: '780.000', price: '78.000000', amount: '60,840.00', po: 'PO-20260928-000035', arr: 'AR-20261005-000001', user: '采购 刘洋' },
  { date: '2026-09-28', supplier: '苏州恒信纺织', material: '弹力斜纹', color: '黑', dyeLot: 'DY-2609-02', weight: '1,580.000', price: '28.500000', amount: '45,030.00', po: 'PO-20260920-000036', arr: 'AR-20260928-000001', user: '采购 陈明' },
]

/* ===================== ADR-0018 三段链路台账（WIP / 成衣） ===================== */
export const wipStocks = [
  { styleNo: 'HB-2026-0018', cat: 'DRESS', color: '本白', size: 'L(165/88A)', qty: 360, inQty: 360, outQty: 240, last: '整烫 陈美玲', state: '在制' },
  { styleNo: 'HB-2026-0018', cat: 'DRESS', color: '本白', size: 'XL(170/92A)', qty: 600, inQty: 600, outQty: 420, last: '整烫 林小燕', state: '在制' },
  { styleNo: 'HB-2026-0018', cat: 'DRESS', color: '黑', size: 'XL(170/92A)', qty: 250, inQty: 250, outQty: 0, last: '—', state: '在制' },
  { styleNo: 'HB-2026-0021', cat: 'SET', color: '本白', size: 'M(160/84A)', qty: 240, inQty: 240, outQty: 240, last: '包装 赵敏', state: '已转成衣' },
]

export const finishedStocks = [
  { styleNo: 'HB-2026-0018', cat: 'DRESS', color: '本白', size: 'L(165/88A)', qty: 240, unit: '62.180000', amount: '14,923.20', in: '最后工序自动入库' },
  { styleNo: 'HB-2026-0021', cat: 'SET', color: '本白', size: 'M(160/84A)', qty: 240, unit: '48.600000', amount: '11,664.00', in: '最后工序自动入库' },
]

/* ===================== ADR-0023 生产追踪（款号 → 手 → 码 → 计件） ===================== */
export const traceRows = [
  { bundleNo: 'BD-20261018-000031-XL01-0001', styleNo: 'HB-2026-0018', cat: 'DRESS', cutNo: 'CT-20261018-000012', dyeLot: 'DY-2609-01', bolt: 'B-2610-001', op: '打边', hand: 'XL 第 1 手 / 共 2 手', qty: 60, counted: 60, emp: '陈美玲', time: '09:44', amount: '18.00', stage: '已完工入库' },
  { bundleNo: 'BD-20261018-000031-L01-0001', styleNo: 'HB-2026-0018', cat: 'DRESS', cutNo: 'CT-20261018-000012', dyeLot: 'DY-2609-01', bolt: 'B-2610-001', op: '打边', hand: 'L 第 1 手 / 共 1 手', qty: 60, counted: 60, emp: '王海涛', time: '09:44', amount: '18.00', stage: '已完工入库' },
  { bundleNo: 'BD-20261018-000031-XL02-0001', styleNo: 'HB-2026-0018', cat: 'DRESS', cutNo: 'CT-20261018-000012', dyeLot: 'DY-2609-01', bolt: 'B-2610-001', op: '打边', hand: 'XL 第 2 手 / 共 2 手', qty: 60, counted: 0, emp: '—', time: '—', amount: '—', stage: '待计件' },
  { bundleNo: 'BD-20261018-000031-XXL01-0001', styleNo: 'HB-2026-0018', cat: 'DRESS', cutNo: 'CT-20261018-000012', dyeLot: 'DY-2609-01', bolt: 'B-2610-001', op: '打边', hand: 'XXL 第 1 手 / 共 2 手', qty: 60, counted: 0, emp: '—', time: '—', amount: '—', stage: '待打边' },
  { bundleNo: 'BD-20261017-000026-M01-0003', styleNo: 'HB-2026-0018', cat: 'DRESS', cutNo: 'CT-20261017-000009', dyeLot: 'DY-2609-01', bolt: 'B-2610-001', op: '拼前', hand: 'M 第 3 手 / 共 6 手', qty: 50, counted: 28, emp: '林小燕', time: '16:20', amount: '5.60', stage: '部分生产' },
]

/* ===================== 裁剪矩阵（ADR-0020/0022/0023：手数直接输入） ===================== */
export const cutMatrix = {
  docNo: 'CT-20261018-000012', status: 'APPROVED' as DocStatus,
  styleNo: 'HB-2026-0018', styleName: '男式商务长袖衬衫', category: 'DRESS',
  customer: '恒博服饰', customerStyleNo: 'HB-26-0912', workshop: '裁剪一组',
  docDate: '2026-10-18', colors: ['WHT', 'BLK'], delivery: '2026-10-28',
  qtyPerHandDefault: 60, mode: 'MASTER' as 'MASTER' | 'UNIFORM' | 'MANUAL',
  sizes: ['L(165/88A)', 'XL(170/92A)', 'XXL(175/96A)', '3XL(180/100A)'],
  suggest: { 'WHT': { 'L(165/88A)': 1, 'XL(170/92A)': 2, 'XXL(175/96A)': 2, '3XL(180/100A)': 1 }, 'BLK': { 'L(165/88A)': 0, 'XL(170/92A)': 1, 'XXL(175/96A)': 1, '3XL(180/100A)': 0 } },
  batches: [
    { id: 'L1', supplier: '苏州恒信纺织', material: '弹力斜纹', color: '本白', dyeLot: 'DY-2609-01', bolt: 'B-2610-001', width: '152.0', avail: '286.400', used: '96.0', counted: true,
      cells: { WHT: { 'L(165/88A)': 1, 'XL(170/92A)': 2, 'XXL(175/96A)': 2, '3XL(180/100A)': 1 }, BLK: { 'L(165/88A)': 0, 'XL(170/92A)': 1, 'XXL(175/96A)': 1, '3XL(180/100A)': 0 } } },
    { id: 'L2', supplier: '苏州恒信纺织', material: '弹力斜纹', color: '本白', dyeLot: 'DY-2609-02', bolt: 'B-2610-002', width: '150.5', avail: '92.700', used: '31.5', counted: false,
      cells: { WHT: { 'L(165/88A)': 0, 'XL(170/92A)': 1, 'XXL(175/96A)': 1, '3XL(180/100A)': 0 }, BLK: { 'L(165/88A)': 0, 'XL(170/92A)': 0, 'XXL(175/96A)': 0, '3XL(180/100A)': 0 } } },
  ],
}
