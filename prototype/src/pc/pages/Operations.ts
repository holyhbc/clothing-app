import { h } from '../../components/h'
import { Combo } from '../../components/Combo'
import { defineComponent, ref } from 'vue'

import { StatusTag, Pager, Modal, useToast, ToastHost } from '../../components/ui'
import { pageHead, card, descGrid } from '../shared'
import { styles, purchaseOrders, purchaseLines, sizeRatios, cuttingCalc, money, qtyFmt } from '../../mock'
import type { DocStatus } from '../../mock'

/* ===================== 采购单（ADR-0012） ===================== */
export const PurchasePage = defineComponent({
  name: 'PurchasePage',
  setup() {
    const toast = useToast()
    const view = ref<'list' | 'detail'>('list')
    const detail = ref(false)
    const reverse = ref(false)
    const reason = ref('')
    const reasonErr = ref('')
    const arrival = ref(false)

    const list = () => [
      pageHead('采购单', '缸号 / 颜色 / 门幅 / 重量 / 匹数 / 用途 / 备注；**审核即入库**（不再有独立采购入库单，ADR-0012）', [
        h('button', { class: 'btn' }, '导出'),
        h('button', { class: 'btn' }, '导入采购单'),
        h('button', { class: 'btn primary' }, '+ 新建采购单'),
      ]),
      card('列表', [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '单据号'), h('th', {}, '供应商'), h('th', {}, '单据日期'), h('th', {}, '到货时间'),
            h('th', {}, '收货仓库'), h('th', { class: 'right' }, '行数'), h('th', { class: 'right' }, '金额'),
            h('th', {}, '状态'), h('th', {}, '制单'), h('th', { style: 'width:200px' }, '操作'),
          ])),
          h('tbody', {}, purchaseOrders.map((o) => h('tr', { key: o.docNo }, [
            h('td', { class: 'mono nowrap' }, o.docNo),
            h('td', { class: 'nowrap' }, o.supplier),
            h('td', { class: 'nowrap' }, o.date),
            h('td', { class: 'nowrap muted' }, o.arrived),
            h('td', { class: 'nowrap' }, o.warehouse),
            h('td', { class: 'right tabular' }, String(o.lines)),
            h('td', { class: 'right amount', style: 'font-weight:600' }, money(o.total)),
            h('td', {}, h(StatusTag, { status: o.status })),
            h('td', { class: 'nowrap muted' }, o.user),
            h('td', { class: 'row-actions' }, [
              h('a', { onClick: () => { detail.value = true } }, '详情'),
              o.status === 'DRAFT' ? h('a', {}, '编辑') : null,
              o.status === 'SUBMITTED' ? h('a', { onClick: () => toast.ok(`已审核（演示）：生成 ${o.lines} 个批次 + 应付 ${money(o.total)} + 入库凭证`) }, '审核') : null,
              o.status === 'SUBMITTED' ? h('a', { class: 'danger' }, '驳回') : null,
              o.status === 'APPROVED' ? h('a', { onClick: () => { reverse.value = true } }, '反审核') : null,
              o.status === 'APPROVED' ? h('a', { onClick: () => (arrival.value = true) }, '到货登记') : null,
            ]),
          ]))),
        ])),
        h(Pager, { total: purchaseOrders.length, page: 1 }),
      ], undefined, true),

      card('采购用途与成本口径（ADR-0012）', descGrid([
        ['正常采购 NORMAL', h('span', {}, ['借 ', h('code', {}, '1405 库存商品-面料'), '；毛利报表混入主料成本'])],
        ['返修补回 REWORK_RECEIPT', h('span', {}, ['借 ', h('code', {}, '5403 制造费用-返修补布费'), '；毛利报表', h('b', {}, '单独列示'), '；必填返修说明（10006）'])],
        ['客供料退回 RETURN', '冲减 1406 在产品成本'],
        ['样布 SAMPLE', '进 6602 管理费用，不进产品成本'],
        ['计量主次', '重量 kg（主）+ 门幅 cm（必填）+ 匹数；三者差异 > 5% 只警告不阻断'],
        ['批次唯一键', h('span', { class: 'mono' }, 'UNIQUE(warehouse, material, dye_lot_no, bolt_no)')],
        ['审核效果', '建批次 + 台账 + 应付 + 凭证，同事务，任一失败整体回滚'],
        ['反审核限制', '批次已被裁剪消耗 → 40005，必须先冲销下游裁剪单'],
      ], 2)),
    ]

    const detailView = () => [
      h('div', { style: 'margin-bottom:12px' }, h('button', { class: 'btn sm', onClick: () => (detail.value = false) }, '‹ 返回列表')),
      pageHead('采购单详情', undefined, [
        h('button', { class: 'btn danger', onClick: () => (reverse.value = true) }, '反审核'),
        h('button', { class: 'btn' }, '打印'),
      ]),
      h('div', { style: 'display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap' }, [
        h('span', { class: 'mono', style: 'font-size:18px;font-weight:600' }, 'PO-20261018-000012'),
        h('button', { class: 'btn sm' }, '复制'),
        h(StatusTag, { status: 'APPROVED' }),
        h('span', { class: 'muted' }, '单号规则 PO-YYYYMMDD-6位（09 §2.1）'),
      ]),
      card('单据头', descGrid([
        ['供应商', '盛虹纺织有限公司 · 联系人 陈经理 · 月结 30 天'],
        ['单据日期', '2026-10-18'],
        ['收货仓库', '原料一仓'],
        ['发票号', 'SH-260918-0032'],
        ['到货时间', '2026-10-18 14:20'],
        ['金额合计', h('b', { class: 'amount' }, money('267,683.20'))],
      ], 3)),
      card('采购明细（一行一缸；同缸多匹按匹号拆批次）', [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '#'), h('th', {}, '物料'), h('th', {}, '颜色'), h('th', {}, '缸号'),
            h('th', {}, '用途'), h('th', { class: 'right' }, '匹数'), h('th', { class: 'right' }, '每匹m'),
            h('th', { class: 'right' }, '总米数'), h('th', { class: 'right' }, '重量kg'), h('th', { class: 'right' }, '门幅cm'),
            h('th', { class: 'right' }, '单价'), h('th', { class: 'right' }, '金额'), h('th', {}, '备注'),
          ])),
          h('tbody', {}, purchaseLines.map((l) => h('tr', { key: l.lineNo }, [
            h('td', { class: 'tabular' }, String(l.lineNo)),
            h('td', {}, [h('div', { class: 'mono' }, l.material), h('div', { class: 'muted' }, l.name)]),
            h('td', { class: 'nowrap' }, l.color),
            h('td', { class: 'mono nowrap' }, l.dyeLot),
            h('td', {}, h('span', {
              class: 'status-tag ' + (l.purpose.startsWith('NORMAL') ? 'st-draft' : 'st-rejected'),
            }, [h('span', { class: 'dot' }), l.purpose.includes('返修') ? '返修补回' : '正常采购'])),
            h('td', { class: 'right amount' }, qtyFmt(l.bolt)),
            h('td', { class: 'right amount muted' }, l.perBolt),
            h('td', { class: 'right amount' }, l.totalM),
            h('td', { class: 'right amount', style: 'font-weight:600' }, l.weight),
            h('td', { class: 'right amount' }, l.width),
            h('td', { class: 'right amount' }, l.price),
            h('td', { class: 'right amount', style: 'font-weight:600' }, money(l.amount)),
            h('td', { style: 'font-size:12px' }, [
              h('div', {}, l.remark),
              l.rework ? h('div', { class: 'tl-reason', style: 'margin-top:4px' }, '返修说明：' + l.rework) : null,
            ]),
          ]))),
        ])),
        h('div', { class: 'alert warn', style: 'margin:12px 16px 0' }, [
          '计量一致性检查：重量 88.6kg vs 匹数×米数 72.4m，按布密度折算约 ',
          h('b', {}, '差异 4.8%'),
          '，在 5% 阈值内，',
          h('b', {}, '仅提示不阻断'),
          '（ADR-0012 规则 3）。',
        ]),
        h('div', { class: 'sticky-actions' }, [
          h('span', { class: 'muted', style: 'margin-right:auto' }, '已生成 20 个批次（B-2610-001 ~ B-2610-020），批次成本恒定不可改（INV-4）'),
          h('b', { class: 'amount' }, '合计 ¥' + money('267,683.20')),
        ]),
      ], undefined, true),
      card('已生成批次', h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
        h('thead', {}, h('tr', {}, [
          h('th', {}, '缸号'), h('th', {}, '匹号'), h('th', {}, '颜色'), h('th', { class: 'right' }, '门幅'),
          h('th', { class: 'right' }, '重量kg'), h('th', { class: 'right' }, '批次成本(元/m)'), h('th', {}, '用途'),
        ])),
        h('tbody', {}, [
          { lot: 'DY-2609-01', bolt: 'B-2610-001 ~ 012', color: 'WHT', width: '152.0', w: '732.5', cost: '28.50', purpose: 'NORMAL' },
          { lot: 'DY-2609-02', bolt: 'B-2610-013 ~ 018', color: 'WHT', width: '150.5', w: '352.4', cost: '29.20', purpose: 'NORMAL' },
          { lot: 'DY-2609-03', bolt: 'B-2610-019 ~ 020', color: 'WHT', width: '149.0', w: '88.6', cost: '15.00', purpose: 'REWORK_RECEIPT' },
        ].map((b) => h('tr', { key: b.lot }, [
          h('td', { class: 'mono' }, b.lot),
          h('td', { class: 'mono muted' }, b.bolt),
          h('td', { class: 'nowrap' }, b.color),
          h('td', { class: 'right amount' }, b.width),
          h('td', { class: 'right amount' }, b.w),
          h('td', { class: 'right amount', style: 'font-weight:600' }, b.cost),
          h('td', {}, h('span', { class: 'status-tag ' + (b.purpose === 'NORMAL' ? 'st-draft' : 'st-rejected') }, [h('span', { class: 'dot' }), b.purpose === 'NORMAL' ? '正常' : '返修'])),
        ]))),
      ])), undefined, true),
      card('审核生成的连锁单据', h('div', { class: 'desc-grid' }, [
        ['库存批次', h('span', { class: 'mono' }, 'material_stocks × 20（缸号+匹号）')],
        ['台账', h('span', {}, [h('code', {}, 'stock_ledgers'), ' direction=', h('code', {}, "'IN'"), ' + ', h('code', {}, 'stock_ledger_lines')])],
        ['供应商应付', h('span', { class: 'mono' }, 'ap_ledgers ¥267,683.20（未核销）')],
        ['入库凭证', h('span', { class: 'mono' }, '借 1405/5403 · 贷 2202 应付账款（借贷平衡 INV-5）')],
        ['对账校验', 'v_stock_reconciliation 与 v_stock_cost_check 必须恒 0 行'],
      ], 2)),
    ]

    return () => {
      const els: unknown[] = [detail.value ? detailView() : list()]

      els.push(reverse.value
        ? h(Modal, { title: '反审核采购单' }, {
            body: h('div', {}, [
              h('div', { class: 'alert danger' }, ['! ', '反审核会同事务反向冲销：① 红字反向凭证 ② 冲销应付 ③ 按同一批 stock_ledger_lines 逐条反向冲回台账 ④ 释放批次行。']),
              h('div', { class: 'alert warn' }, ['! ', '前置校验：该批次若已被下游裁剪消耗 → 拒绝 ', h('code', {}, '40005'), '，必须先冲销下游裁剪单。']),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '原因'),
                h('div', { class: 'grow' }, [
                  h('textarea', { class: 'inp', rows: 3, placeholder: '例如：门幅实测 149 不足 150，与供方协商退 2 匹', value: reason.value, onInput: (e: Event) => { reason.value = (e.target as HTMLTextAreaElement).value; reasonErr.value = '' } }),
                  reasonErr.value ? h('div', { class: 'form-err' }, reasonErr.value) : null,
                ]),
              ]),
            ]),
            footer: [
              h('button', { class: 'btn' }, '取消'),
              h('button', { class: 'btn danger', onClick: () => { if (!reason.value.trim()) { reasonErr.value = '反审核必须填写原因（10006）'; return } toast.ok('已反审核并冲销（演示）'); reverse.value = false; reason.value = '' } }, '确认反审核'),
            ],
          })
        : null)

      els.push(arrival.value
        ? h(Modal, { title: '到货登记' }, {
            body: h('div', {}, [
              h('div', { class: 'alert info' }, ['到货登记只补录实际到货信息；缸号与门幅等以采购行为准，如需修改请先反审核再改。']),
              h('div', { class: 'form-row' }, [h('label', { class: 'required' }, '实际到货时间'), h('div', { class: 'grow' }, h('input', { class: 'inp', style: 'max-width:240px', type: 'datetime-local' }))]),
              h('div', { class: 'form-row' }, [h('label', {}, '到货备注'), h('div', { class: 'grow' }, h('textarea', { class: 'inp', rows: 2, placeholder: '例如：其中 1 匹外包装破损，已拍照留档' }))]),
            ]),
            footer: [h('button', { class: 'btn' }, '取消'), h('button', { class: 'btn primary', onClick: () => { toast.ok('到货登记已保存（演示）'); arrival.value = false } }, '保存')],
          })
        : null)

      els.push(h(ToastHost, { items: toast.items }))
      return els.filter(Boolean) as any
    }
  },
})

/* ===================== 库存操作（入库/出库/调拨/盘点） ===================== */
export const StockOpsPage = defineComponent({
  name: 'StockOpsPage',
  setup() {
    const toast = useToast()
    const ops = [
      { key: 'in', name: '入库', desc: '采购单审核 / 生产退料 / 盘盈', perm: 'purchase:approve · stock:in', tone: 'st-approved' },
      { key: 'out', name: '出库', desc: '裁剪耗料 / 领辅料 / 销售出库 / 报废', perm: 'cutting:approve · stock:out', tone: 'st-rejected' },
      { key: 'transfer', name: '调拨', desc: '仓库之间移库，批次成本随批次走不变', perm: 'stock:transfer', tone: 'st-submitted' },
      { key: 'stocktake', name: '盘点', desc: '账实核对，差异生成盘盈/盘亏，整件口径', perm: 'stock:stocktake', tone: 'st-draft' },
      { key: 'scrap', name: '报废', desc: '不合格布料/辅料出库并计入损耗', perm: 'stock:out', tone: 'st-cancelled' },
    ]
    const lotPick = ref(false)
    return () => [
      pageHead('库存管理', '七个操作入口（入库/出库/调拨/盘点/报废）+ 批次台账 + 库存预警（ADR-0011 选批规则）', [
        h('button', { class: 'btn' }, '导出库存台账'),
        h('button', { class: 'btn', onClick: () => (lotPick.value = true) }, '按缸查批次'),
      ]),
      h('div', { class: 'kpi-grid' }, ops.map((o) => h('div', { class: 'kpi', style: 'cursor:pointer' }, [
        h('div', { class: 'kpi-label' }, [h('span', { class: 'status-tag ' + o.tone }, [h('span', { class: 'dot' }), o.name]), h('span', { class: 'muted', style: 'margin-left:auto' }, '›')]),
        h('div', { style: 'margin-top:10px;font-size:13px' }, o.desc),
        h('div', { class: 'kpi-sub mono', style: 'font-size:11px' }, o.perm),
      ]))),
      card('库存预警', [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '物料'), h('th', {}, '仓库'), h('th', { class: 'right' }, '结存'), h('th', { class: 'right' }, '安全库存'),
            h('th', { class: 'right' }, '缺口'), h('th', {}, '状态'), h('th', { style: 'width:150px' }, '操作'),
          ])),
          h('tbody', {}, [
            { m: 'F-CT-000128 弹力斜纹 本白', w: '原料一仓', q: '92.7', s: '200', gap: '107.3' },
            { m: 'T-FL-000091 涤纶线 402# 本白', w: '辅料仓', q: '3,240', s: '5,000', gap: '1,760' },
            { m: 'T-BU-000033 四合扣 15# 黑', w: '辅料仓', q: '2,100', s: '3,000', gap: '900' },
          ].map((r) => h('tr', { key: r.m }, [
            h('td', { class: 'mono nowrap' }, r.m),
            h('td', { class: 'nowrap' }, r.w),
            h('td', { class: 'right amount', style: 'font-weight:600' }, r.q),
            h('td', { class: 'right tabular muted' }, r.s),
            h('td', { class: 'right tabular', style: 'color:var(--color-danger)' }, r.gap),
            h('td', {}, h('span', { class: 'status-tag st-pending' }, [h('span', { class: 'dot' }), '低于安全库存'])),
            h('td', { class: 'row-actions' }, [h('a', {}, '生成采购单'), h('a', {}, '安全库存设置')]),
          ]))),
        ])),
      ], undefined, true),
      card('选批规则（出库时强制按批结转，ADR-0011）', descGrid([
        ['① 人工指定批次', '裁剪单按客户指定缸号配料（最常见）'],
        ['② FEFO', '有有效期/保质期的辅料，先到期先出'],
        ['③ FIFO', '无有效期物料默认按入库日期升序'],
        ['禁止', '跨批次按平均价出库（已撤销移动加权）'],
        ['台账明细', '每条出库台账写 stock_ledger_lines：用了哪几缸各多少米各多少钱'],
        ['布头 vs 尾数', '布头（可再裁的整段布）入库；尾数（不足一件）不入库，计裁剪损耗'],
      ], 2)),
      lotPick.value
        ? h(Modal, { title: '按缸号查批次（成本追溯）', wide: true }, {
            body: h('div', {}, [
              h('div', { class: 'form-row' }, [h('label', {}, '缸号'), h('div', { class: 'grow' }, h('input', { class: 'inp', style: 'width:100%', value: 'DY-2609-01' }))]),
              h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
                h('thead', {}, h('tr', {}, [
                  h('th', {}, '缸号'), h('th', {}, '匹号'), h('th', {}, '仓库'), h('th', { class: 'right' }, '门幅'),
                  h('th', { class: 'right' }, '重量kg'), h('th', { class: 'right' }, '结存m'), h('th', { class: 'right' }, '锁定m'),
                  h('th', { class: 'right' }, '可用m'), h('th', { class: 'right' }, '批次成本'), h('th', {}, '用途'),
                ])),
                h('tbody', {}, ['001', '002', '003', '004', '005'].map((n) => h('tr', { key: n }, [
                  h('td', { class: 'mono' }, 'DY-2609-01'),
                  h('td', { class: 'mono' }, `B-2610-00${n}`),
                  h('td', { class: 'nowrap' }, '原料一仓'),
                  h('td', { class: 'right amount' }, '152.0'),
                  h('td', { class: 'right amount' }, '61.0'),
                  h('td', { class: 'right amount', style: 'font-weight:600' }, qtyFmt(120 - Number(n) * 6)),
                  h('td', { class: 'right amount muted' }, n === '001' ? '24.0' : '0'),
                  h('td', { class: 'right amount', style: 'color:var(--color-primary)' }, qtyFmt(120 - Number(n) * 6 - (n === '001' ? 24 : 0))),
                  h('td', { class: 'right amount' }, '28.50'),
                  h('td', {}, h('span', { class: 'status-tag st-draft' }, [h('span', { class: 'dot' }), '正常'])),
                ]))),
              ])),
            ]),
            footer: [
              h('button', { class: 'btn' }, '导出该缸台账'),
              h('button', { class: 'btn primary', onClick: () => { toast.ok('已关闭（演示）'); lotPick.value = false } }, '关闭'),
            ],
          })
        : null,
      h(ToastHost, { items: toast.items }),
    ]
  },
})

/* ===================== 尺码比例配置（ADR-0013） ===================== */
export const SizeRatioPage = defineComponent({
  name: 'SizeRatioPage',
  setup() {
    const toast = useToast()
    const calcOpen = ref(false)
    const groups = ['WHT 本白', 'BLK 黑']
    const selected = ref('WHT 本白')
    const byColor = (c: string) => sizeRatios.filter((r) => r.color === c)
    const hands = (c: string) => byColor(c).reduce((s, r) => s + Number(r.code), 0)

    return () => [
      pageHead('款号尺码比例', 'ADR-0013：按**款号 × 颜色**预置手数；每手件数在裁剪单上输入；明细 = floor(比例 × 每手件数)', [
        h('button', { class: 'btn' }, '导入比例'),
        h('button', { class: 'btn', onClick: () => (calcOpen.value = true) }, '试算裁剪明细'),
        h('button', { class: 'btn primary' }, '保存比例'),
      ]),
      card(null, h('div', { class: 'filters' }, [
        h('div', { class: 'field' }, [h('label', {}, '款号'), h(Combo, {
          options: styles.map((st) => ({ value: st.styleNo, label: st.styleNo, sub: `${st.name}（${st.customer}）` })),
          modelValue: 'HB-2026-0018',
          placeholder: '输入款号或款名搜索…（INV-9）',
          width: '240px',
          onChange: () => toast.ok('已切换款号（演示）：比例表按款号 × 颜色过滤'),
        })]),
        h('div', { class: 'chip' }, [h('span', { class: 'k' }, '客户'), h('span', { class: 'v' }, '恒博服饰')]),
        h('div', { class: 'chip' }, [h('span', { class: 'k' }, '尺码组'), h('span', { class: 'v' }, '男装码表（7 个）')]),
        h('div', { class: 'chip' }, [h('span', { class: 'k' }, '单位'), h('span', { class: 'v' }, '手（1 手 = N 件，由裁剪单输入）')]),
      ])),
      ...groups.map((g) => card(
        h('span', {}, [g, '　', h('span', { class: 'muted', style: 'font-weight:400;font-size:12px' }, `手数合计 ${hands(g).toFixed(4)} 手`)]),
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '尺码'), h('th', {}, '尺码码'), h('th', { style: 'width:220px' }, '手数比例'),
            h('th', { class: 'right' }, '占比'), h('th', {}, '说明'),
          ])),
          h('tbody', {}, byColor(g).map((r) => {
            const pct = hands(g) ? ((Number(r.code) / hands(g)) * 100).toFixed(1) : '0'
            return h('tr', { key: r.size }, [
              h('td', {}, r.size),
              h('td', { class: 'mono muted' }, r.size),
              h('td', {}, h('input', { class: 'inp', style: 'width:140px', value: r.code, onInput: () => {} })),
              h('td', { class: 'right amount' }, pct + '%'),
              h('td', { class: 'muted', style: 'font-size:12px' }, Number(r.code) >= 1 ? '常用码' : '小众码'),
            ])
          })),
        ])),
      )),
      card('规则', h('div', { class: 'desc-grid' }, [
        ['比例维度', '款号 × 颜色 × 尺码 → 手数；同款不同颜色比例独立'],
        ['完整性', '该款该颜色下的每个尺码都必须有比例，缺配报 20006'],
        ['取整', '件数 = floor(比例 × 每手件数)；余量并入 balance_qty（09 §4.2 整件口径）'],
        ['每手件数', '在裁剪单上输入，**不做基础资料**（业务方：每次裁剪不同）'],
        ['单据覆盖', '裁剪单可临时覆盖比例，必填原因（10006），不改主数据，写日志'],
        ['模板复制', '款号工序模板复制**不复制尺码比例**（比例与销售构成强绑定，需重新定）'],
      ], 2)),

      calcOpen.value
        ? h(Modal, { title: '裁剪明细试算（只填每手件数）', wide: true }, {
            body: h('div', {}, [
              h('div', { class: 'form-row' }, [
                h('label', {}, '款号'), h('div', { class: 'grow' }, h('input', { class: 'inp', style: 'width:100%', value: cuttingCalc.styleNo, disabled: true })),
              ]),
              h('div', { class: 'form-row' }, [
                h('label', {}, '颜色'), h('div', { class: 'grow' }, h(Combo, {
                  options: [
                    { value: 'RED', label: 'RED 红色', sub: '基础色卡' },
                    { value: 'BLK', label: 'BLK 黑', sub: '基础色卡' },
                    { value: 'WHT', label: 'WHT 本白', sub: '基础色卡' },
                  ],
                  modelValue: 'WHT',
                  placeholder: '输入颜色编码或名称搜索…',
                  width: '100%',
                })),
              ]),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '每手件数'),
                h('div', { class: 'grow' }, [
                  h('input', { class: 'inp', style: 'max-width:160px', type: 'number', value: String(cuttingCalc.qtyPerHand), onInput: (e: Event) => (cuttingCalc.qtyPerHand = Number((e.target as HTMLInputElement).value)) }),
                  h('div', { class: 'form-hint' }, '业务方口径：**每次裁剪输入，不同数据**。改这个值即可重算全部尺码明细。'),
                ]),
              ]),
              h('div', { class: 'alert ok' }, [
                '系统按比例自动算出 ',
                cuttingCalc.totalHands, ' 手的明细（无需逐行填件数）：',
              ]),
              h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
                h('thead', {}, h('tr', {}, [
                  h('th', {}, '尺码'), h('th', { class: 'right' }, '比例（手）'), h('th', {}, '计算式'),
                  h('th', { class: 'right' }, '出数（件）'),
                ])),
                h('tbody', {}, cuttingCalc.rows.map((r) => h('tr', { key: r.size }, [
                  h('td', { class: 'mono' }, r.size),
                  h('td', { class: 'right amount' }, r.ratio),
                  h('td', { class: 'mono muted' }, `floor(${r.ratio} × ${cuttingCalc.qtyPerHand})`),
                  h('td', { class: 'right amount', style: 'font-weight:600' }, String(Math.floor(Number(r.ratio) * cuttingCalc.qtyPerHand))),
                ]))),
                h('tfoot', {}, h('tr', {}, [
                  h('td', { class: 'right' }, '合计'),
                  h('td', { class: 'right amount' }, '6.0000 手'),
                  h('td', {}),
                  h('td', { class: 'right amount', style: 'font-weight:700' }, String(Math.floor(cuttingCalc.rows.reduce((s, r) => s + Number(r.ratio) * cuttingCalc.qtyPerHand, 0)))),
                ])),
              ])),
              h('div', { class: 'alert info', style: 'margin-top:12px' }, [
                '耗料 = Σ(各尺码件数 × BOM.单件用量) + 损耗。',
                h('b', {}, 'BOM 只算耗料，不再反推件数'),
                '（与旧口径相反）。',
              ]),
            ]),
            footer: [
              h('button', { class: 'btn' }, '取消'),
              h('button', { class: 'btn', onClick: () => { toast.ok('已用该比例生成裁剪单草稿（演示）'); calcOpen.value = false } }, '按此生成裁剪单'),
            ],
          })
        : null,
      h(ToastHost, { items: toast.items }),
    ]
  },
})