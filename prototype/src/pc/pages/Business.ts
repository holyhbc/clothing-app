import { h } from '../../components/h'
import { Combo } from '../../components/Combo'
import { defineComponent, ref } from 'vue'

import { StatusTag, Pager, Modal, useToast, ToastHost } from '../../components/ui'
import { pageHead, card, descGrid } from '../shared'
import { stocks, salesOrders, arLedger, vouchers, roles, permMatrix, roleGrants, suppliers, customers, styles, money, qtyFmt } from '../../mock'

/* ===================== 库存台账（批次实际成本） ===================== */
export const StockPage = defineComponent({
  name: 'StockPage',
  setup() {
    const toast = useToast()
    const showCost = ref(true)

    const rows = () =>
      stocks.map((s) => {
        const low = s.status !== '正常'
        return h('tr', { key: s.material + s.dyeLot + s.bolt }, [
          h('td', { class: 'nowrap' }, s.supplier),
          h('td', { class: 'mono nowrap' }, s.material),
          h('td', {}, [h('div', {}, s.name), h('div', { class: 'muted' }, s.type)]),
          h('td', { class: 'nowrap' }, s.color),
          h('td', { class: 'nowrap' }, s.warehouse),
          h('td', { class: 'mono nowrap' }, s.dyeLot),
          h('td', { class: 'mono muted nowrap' }, s.bolt),
          h('td', { class: 'right amount muted nowrap' }, s.width === '—' ? '—' : s.width + ' cm'),
          h('td', { class: 'right amount muted nowrap' }, s.weight === '—' ? '—' : s.weight + ' kg'),
          h('td', { class: 'right amount', style: 'font-weight:600' }, qtyFmt(s.qty)),
          ...(showCost.value
            ? [h('td', { class: 'right amount' }, s.cost), h('td', { class: 'right amount', style: 'font-weight:600' }, money(s.value))]
            : []),
          h('td', { class: 'right tabular muted' }, qtyFmt(s.safety)),
          h('td', {}, low
            ? h('span', { class: 'status-tag st-pending' }, [h('span', { class: 'dot' }), '低于安全库存'])
            : h('span', { class: 'status-tag st-approved' }, [h('span', { class: 'dot' }), '正常'])),
          h('td', { class: 'row-actions' }, [
            h('a', { onClick: () => toast.err('40002 批次成本不可直接改，需红冲原入库单后重新入库') }, '调整成本'),
            h('a', {}, '批次台账'),
            h('a', {}, '领料出库'),
          ]),
        ])
      })

    const head = () =>
      h('tr', {}, [
        h('th', {}, '供应商'), h('th', {}, '物料编码'), h('th', {}, '名称 / 类别'), h('th', {}, '颜色'), h('th', {}, '仓库'),
        h('th', {}, '缸号'), h('th', {}, '匹号'), h('th', { class: 'right' }, '门幅'), h('th', { class: 'right' }, '重量'),
        h('th', { class: 'right' }, '可用数量'),
        ...(showCost.value ? [h('th', { class: 'right' }, '批次成本(元)'), h('th', { class: 'right' }, '金额')] : []),
        h('th', { class: 'right' }, '安全库存'), h('th', {}, '状态'), h('th', { style: 'width:170px' }, '操作'),
      ])

    return () =>
      [
        pageHead('库存台账 · 三段链路（① 原材料 ② 裁片 WIP ③ 成衣）', 'ADR-0011 逐批实际成本 + ADR-0022 供应商维度 + ADR-0018 三段独立台账。裁剪选料只列「可用 > 0」的缸号/匹号。★ 成本不含运费（ADR-0019）', [
          h('label', { class: 'chip', style: 'cursor:pointer' }, [
            h('input', { type: 'checkbox', checked: showCost.value, onChange: (e: Event) => (showCost.value = (e.target as HTMLInputElement).checked) }),
            ' 展示成本单价 ',
            h('span', { class: 'muted' }, '（权限 stock:cost:view，仓管默认无）'),
          ]),
          h('button', { class: 'btn' }, '导出'),
          h('button', { class: 'btn primary' }, '+ 采购入库单'),
        ]),

        card('物料批次库存', [
          h('div', { class: 'filters', style: 'margin-bottom:12px' }, [
            h('div', { class: 'field' }, [h('label', {}, '物料'), h(Combo, {
              options: [
                { value: 'F-CT-000128', label: 'F-CT-000128', sub: '弹力斜纹　幅宽 150-152cm' },
                { value: 'F-KN-000214', label: 'F-KN-000214', sub: '羊毛混纺针织　幅宽 160cm' },
                { value: 'F-CT-000131', label: 'F-CT-000131', sub: '防绒卡其　幅宽 145cm' },
                { value: 'T-ZL-000456', label: 'T-ZL-000456', sub: '树脂拉链 20#' },
              ],
              modelValue: 'F-CT-000128',
              placeholder: '输入物料编码或名称搜索…（INV-9）',
              width: '230px',
            })]),
            h('div', { class: 'field' }, [h('label', {}, '缸号'), h(Combo, {
              options: [
                { value: 'DY-2609-01', label: 'DY-2609-01', sub: '苏州恒信纺织　可用 286.4 kg' },
                { value: 'DY-2609-02', label: 'DY-2609-02', sub: '苏州恒信纺织　可用 92.7 kg' },
                { value: 'DY-2607-04', label: 'DY-2607-04', sub: '绍兴华纺布业　可用 1240.5 kg' },
                { value: 'DY-2608-03', label: 'DY-2608-03', sub: '广州锦纺面料　可用 2140.0 kg' },
              ],
              modelValue: '',
              placeholder: '输入缸号搜索…（只列可用 > 0）',
              width: '230px',
            })]),
            h('div', { class: 'field' }, [h('label', {}, '供应商'), h(Combo, {
              options: [
                { value: '全部', label: '全部供应商' },
                ...suppliers.map((v) => ({ value: v.name, label: `${v.code} ${v.name}`, sub: v.contact })),
              ],
              modelValue: '全部',
              placeholder: '输入供应商搜索…（INV-9）',
              width: '220px',
            })]),
            h('div', { class: 'field' }, [h('label', {}, '仓库'), h('select', { class: 'sel' }, ['全部', '原料一仓', '原料二仓', '辅料仓'].map((x) => h('option', {}, x)))]),
            h('div', { class: 'field' }, [h('label', {}, '只看低于安全库存'), h('input', { type: 'checkbox' })]),
            h('button', { class: 'btn primary' }, '查询'),
          ]),
          h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [h('thead', {}, head()), h('tbody', {}, rows())])),
          h(Pager, { total: stocks.length, page: 1 }),
        ], undefined, true),

        card('成本结转与选批规则（ADR-0011）', descGrid([
          ['批次粒度', h('span', { class: 'mono' }, '仓库 + 物料 + dye_lot_no + bolt_no + 颜色')],
          ['批次成本', '入库审核时一次确定，此后永不改变（INV-4）'],
          ['入库定成本', '采购入库 = (采购金额 + 分摊费用) ÷ 入库数量；退料沿用原批次；盘盈用同缸最近批次'],
          ['出库选批优先级', '① 人工指定缸号（裁剪按客户指定缸配料）② FEFO ③ FIFO'],
          ['出库明细', h('span', {}, ['每条出库台账写 ', h('code', {}, 'stock_ledger_lines'), ' 记录用了哪几缸各多少米各多少钱'])],
          ['成衣成本', h('span', {}, ['成衣单位成本 = Σ', h('code', {}, 'stock_ledger_lines.amount'), ' + Σ', h('code', {}, 'piecework_logs.amount')])],
          ['布头 vs 尾数', '布头（可再裁的整段布）入库；尾数（不足一件）不入库，计裁剪损耗'],
          ['对账', h('span', {}, [h('code', {}, 'v_stock_reconciliation'), ' 与 ', h('code', {}, 'v_stock_cost_check'), ' 必须恒为 0 行'])],
        ], 2)),
      ]
  },
})

/* ===================== 销售订单 ===================== */
export const SalesPage = defineComponent({
  name: 'SalesPage',
  setup() {
    const toast = useToast()
    return () =>
      [
        pageHead('销售订单', '发货即出库（单据 SH）：审核触发扣成衣库存 + 生成应收 + 出库凭证；金额一律本位币 RMB', [
          h('button', { class: 'btn' }, '导出'),
          h('button', { class: 'btn primary' }, '+ 新建订单'),
        ]),
        card('列表', [
          h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
            h('thead', {}, h('tr', {}, [
              h('th', {}, '订单号'), h('th', {}, '客户'), h('th', {}, '款号 / 颜色'),
              h('th', { class: 'right' }, '订单件数'), h('th', { class: 'right' }, '已发货'),
              h('th', { class: 'right' }, '订单金额'), h('th', {}, '交期'), h('th', {}, '状态'),
              h('th', { style: 'width:190px' }, '操作'),
            ])),
            h('tbody', {}, salesOrders.map((o) =>
              h('tr', { key: o.docNo }, [
                h('td', { class: 'mono nowrap' }, o.docNo),
                h('td', {}, o.customer),
                h('td', {}, [h('div', { class: 'mono' }, o.styleNo), h('div', { class: 'muted' }, o.color)]),
                h('td', { class: 'right amount' }, qtyFmt(o.qty)),
                h('td', { class: 'right tabular' }, o.delivered ? qtyFmt(o.delivered) : '—'),
                h('td', { class: 'right amount', style: 'font-weight:600' }, money(o.amount)),
                h('td', { class: 'nowrap' }, o.delivery),
                h('td', {}, h(StatusTag, { status: o.status })),
                h('td', { class: 'row-actions' }, [
                  h('a', { onClick: () => toast.ok(`已发货 ${qtyFmt(o.qty)} 件（演示）：扣库存 + 生成应收 + 出库凭证`) }, '发货出库'),
                  h('a', {}, '变更历史'),
                  h('a', { class: o.status === 'APPROVED' ? '' : 'disabled' }, '打印'),
                ]),
              ]),
            )),
          ])),
        ], undefined, true),

        card('整件口径说明（09 §4.2）', h('div', { class: 'alert info' }, [
          '生产端尾数不入库 → 成衣库存里',
          h('b', {}, '只有整件'),
          ' → 订单与发货数量必须为',
          h('b', {}, '正整数'),
          '，发不出不足一件的数量（10001）。末批零星发货（如订单 1,050 件最后一批发 8 件）标记 ',
          h('code', {}, 'is_balance_line'),
          '，但仍是整数件。币种固定 CNY，不支持多币。',
        ])),
      ]
  },
})

/* ===================== 应收往来 ===================== */
export const ArPage = defineComponent({
  name: 'ArPage',
  setup() {
    const toast = useToast()
    const buckets = [
      { label: '未到期 / 0-30 天', v: 320600, cls: 'ok' },
      { label: '31-60 天', v: 52000, cls: 'warn' },
      { label: '61-90 天', v: 26000, cls: '' },
      { label: '90 天以上', v: 0, cls: '' },
    ]
    const total = arLedger.reduce((s, r) => s + Number(r.balance.replace(/[,]/g, '')), 0)
    return () =>
      [
        pageHead('应收往来', '收款单 SK → 往来流水 → 核销 ST → 客户对账单 SN；核销额 ≤ 未核销额（50006）', [
          h('button', { class: 'btn' }, '生成客户对账单'),
          h('button', { class: 'btn primary' }, '+ 收款单'),
        ]),
        h('div', { class: 'kpi-grid' }, [
          h('div', { class: 'kpi' }, [
            h('div', { class: 'kpi-label' }, '应收余额合计'),
            h('div', { class: 'kpi-value' }, ['¥', money(total)]),
            h('div', { class: 'kpi-sub' }, `${arLedger.length} 笔未结清`),
          ]),
          ...buckets.map((b) =>
            h('div', { class: 'kpi' }, [
              h('div', { class: 'kpi-label' }, b.label),
              h('div', { class: 'kpi-value', style: 'font-size:var(--font-size-xl)' }, ['¥', money(b.v)]),
              h('div', { class: ['progress', b.cls], style: 'margin-top:8px' }, [h('i', { style: `width:${Math.min(100, b.v / 3206)}%` })]),
            ]),
          ),
        ]),
        card('客户应收明细', [
          h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
            h('thead', {}, h('tr', {}, [
              h('th', {}, '客户'), h('th', {}, '来源单据'), h('th', {}, '日期'),
              h('th', { class: 'right' }, '发生额'), h('th', { class: 'right' }, '已核销'),
              h('th', { class: 'right' }, '未核销余额'), h('th', { class: 'right' }, '账龄(天)'),
              h('th', {}, '状态'), h('th', { style: 'width:160px' }, '操作'),
            ])),
            h('tbody', {}, arLedger.map((r) => {
              const cls = r.aging === 0 ? 'st-approved' : r.aging <= 30 ? 'st-submitted' : r.aging <= 60 ? 'st-pending' : 'st-rejected'
              const txt = r.aging === 0 ? '已结清' : r.aging <= 30 ? '正常' : r.aging <= 60 ? '关注' : '逾期'
              return h('tr', { key: r.source }, [
                h('td', {}, r.customer),
                h('td', { class: 'mono nowrap' }, r.source),
                h('td', { class: 'nowrap' }, r.date),
                h('td', { class: 'right amount' }, money(r.amount)),
                h('td', { class: 'right amount muted' }, money(r.settled)),
                h('td', { class: 'right amount', style: 'font-weight:600;color:var(--color-danger)' }, money(r.balance)),
                h('td', { class: 'right tabular' }, r.aging),
                h('td', {}, h('span', { class: 'status-tag ' + cls }, [h('span', { class: 'dot' }), txt])),
                h('td', { class: 'row-actions' }, [
                  h('a', { class: r.balance === '0.00' ? 'disabled' : '', onClick: () => toast.ok(`已生成核销单草稿（演示）：${r.customer} ${money(r.balance)}`) }, '核销'),
                  h('a', {}, '对账单'),
                ]),
              ])
            })),
          ])),
          h(Pager, { total: arLedger.length, page: 1 }),
        ], undefined, true),
      ]
  },
})

/* ===================== 记账凭证 ===================== */
export const VoucherPage = defineComponent({
  name: 'VoucherPage',
  setup() {
    const toast = useToast()
    const periodOpen = ref(false)
    const templates: Array<[string, string, string]> = [
      ['发货确认收入', '1122 应收账款', '6001 主营业务收入'],
      ['发货结转成本', '6401 主营业务成本', '1403 库存商品-产成品'],
      ['裁剪耗料结转', '1406 库存商品-在产品', '1405 库存商品-面料（缸号批次实际成本）'],
      ['计件工资计提', '5101 制造费用-计件工资', '2211 应付职工薪酬'],
      ['工资发放付款', '2211 应付职工薪酬', '1002 银行存款'],
      ['采购入库', '1405 库存商品-面料', '2202 应付账款'],
    ]
    return () =>
      [
        pageHead('记账凭证', 'ADR-0007：阶段一按发货确认收入；INV-5 借贷必须平衡；已过账凭证不可改，只能红冲', [
          h('button', { class: 'btn', onClick: () => (periodOpen.value = true) }, '会计期间关账'),
          h('button', { class: 'btn' }, '导出'),
          h('button', { class: 'btn primary' }, '+ 手工凭证'),
        ]),
        card('凭证列表', [
          h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
            h('thead', {}, h('tr', {}, [
              h('th', {}, '凭证号'), h('th', {}, '日期'), h('th', {}, '来源单据'), h('th', {}, '摘要'),
              h('th', { class: 'right' }, '借方合计'), h('th', { class: 'right' }, '贷方合计'),
              h('th', {}, '平衡'), h('th', {}, '状态'), h('th', { style: 'width:170px' }, '操作'),
            ])),
            h('tbody', {}, vouchers.map((v) =>
              h('tr', { key: v.docNo }, [
                h('td', { class: 'mono nowrap' }, v.docNo),
                h('td', { class: 'nowrap' }, v.date),
                h('td', { class: 'nowrap' }, v.source),
                h('td', {}, v.desc),
                h('td', { class: 'right amount' }, money(v.debit)),
                h('td', { class: 'right amount' }, money(v.credit)),
                h('td', {}, v.debit === v.credit
                  ? h('span', { class: 'status-tag st-approved' }, [h('span', { class: 'dot' }), '平'])
                  : h('span', { class: 'status-tag st-rejected' }, [h('span', { class: 'dot' }), '不平'])),
                h('td', {}, h('span', { class: 'status-tag ' + (v.status === 'POSTED' ? 'st-approved' : 'st-draft') }, [h('span', { class: 'dot' }), v.status === 'POSTED' ? '已过账' : '草稿'])),
                h('td', { class: 'row-actions' }, [
                  h('a', {}, '查看分录'),
                  v.status === 'DRAFT'
                    ? h('a', { onClick: () => toast.ok('已过账（演示）') }, '过账')
                    : h('a', { class: 'disabled' }, '修改'),
                  v.status === 'POSTED'
                    ? h('a', { class: 'danger', onClick: () => toast.err('已过账凭证不可删除，只能红冲（60002）') }, '红冲')
                    : null,
                ]),
              ]),
            )),
          ])),
        ], undefined, true),

        card('凭证自动生成模板（借贷方向，ADR-0007）', [
          h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
            h('thead', {}, h('tr', {}, [h('th', {}, '业务'), h('th', {}, '借方'), h('th', {}, '贷方')])),
            h('tbody', {}, [
              ...templates.map((r) => h('tr', { key: r[0] }, [h('td', {}, r[0]), h('td', { class: 'mono' }, r[1]), h('td', { class: 'mono' }, r[2])])),
              h('tr', {}, [
                h('td', {}, '尾数 balance_qty'),
                h('td', { class: 'muted' }, '不生成分录（09 §4.2）'),
                h('td', { class: 'muted' }, '不生成分录'),
              ]),
            ]),
          ])),
          h('div', { class: 'alert ok', style: 'margin:12px 16px 16px' }, [
            '发布前强制对账：', h('code', {}, 'v_voucher_balance'), ' 必须 0 行（借贷不平行数 = 0）。',
          ]),
        ], undefined, true),

        periodOpen.value
          ? h(Modal, { title: '会计期间关账' }, {
              body: h('div', {}, [
                h('div', { class: 'alert warn' }, ['关账后该期间禁止新增/修改单据与凭证（', h('code', {}, '60003'), '）。重开需 ', h('code', {}, 'finance:period:reopen'), '。']),
                h('div', { class: 'form-row' }, [h('label', {}, '期间'), h('div', { class: 'grow' }, h('select', { class: 'sel', style: 'width:100%' }, ['2026-10（进行中）', '2026-09（已关闭）'].map((x) => h('option', {}, x))))]),
                h('div', { class: 'alert info' }, ['关账前校验：① ', h('code', {}, 'v_voucher_balance'), ' 借贷平 ② 库存与台账对账 ③ 该期间无未过账凭证（否则 60005）']),
                h('div', { class: 'form-row' }, [h('label', { class: 'required' }, '关账原因'), h('div', { class: 'grow' }, h('textarea', { class: 'inp', rows: 3, placeholder: '例如：10 月账已核完，与客户对账一致' }))]),
              ]),
              footer: [
                h('button', { class: 'btn' }, '取消'),
                h('button', { class: 'btn primary', onClick: () => { toast.ok('2026-10 已关账（演示）'); periodOpen.value = false } }, '确认关账'),
              ],
            })
          : null,
        h(ToastHost, { items: toast.items }),
      ]
  },
})

/* ===================== 角色权限 ===================== */
export const PermPage = defineComponent({
  name: 'PermPage',
  setup() {
    const toast = useToast()
    const sel = ref('车间主管')

    const permRows = () =>
      Object.entries(permMatrix).flatMap(([mod, perms]) =>
        perms.map((p, i) => {
          const has = !!roleGrants[sel.value]?.includes(mod)
          return h('tr', { key: mod + p }, [
            h('td', {}, [i === 0 ? h('b', {}, mod) : null, ' ', h('code', { class: 'mono muted' }, p)]),
            h('td', { class: has ? 'yes' : 'no' }, has ? '✓' : '—'),
            h('td', { class: 'muted' }, i === 0 ? `模块：${mod}` : ''),
          ])
        }),
      )

    return () =>
      [
        pageHead('角色权限', 'ADR-0008：两级动作词 + 自助权限点内建；数据范围四级（SELF / GROUP / WORKSHOP / FACTORY）', [
          h('button', { class: 'btn' }, '导出权限清单'),
          h('button', { class: 'btn primary' }, '+ 新建角色'),
        ]),
        h('div', { style: 'display:grid;grid-template-columns:320px minmax(0,1fr);gap:16px;align-items:start' }, [
          card('角色列表',
            h('table', { class: 'tbl' }, [
              h('tbody', {}, roles.map((r) =>
                h('tr', {
                  key: r.code,
                  style: sel.value === r.name ? 'background:var(--color-primary-bg);cursor:pointer' : 'cursor:pointer',
                  onClick: () => (sel.value = r.name),
                }, [
                  h('td', {}, [h('div', {}, r.name), h('div', { class: 'muted mono' }, r.code)]),
                  h('td', { class: 'right' }, [h('div', { class: 'nowrap' }, r.scope), h('div', { class: 'muted' }, `${r.users} 人`)]),
                ]),
              )),
            ]),
            undefined, true),
          h('div', {}, [
            card(`${sel.value} · 权限矩阵`,
              h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl perm-tbl' }, [
                h('thead', {}, h('tr', {}, [h('th', {}, '权限点'), h('th', { style: 'width:70px' }, '有/无'), h('th', {}, '说明')])),
                h('tbody', {}, permRows()),
              ])),
              undefined, true),
            card('数据范围与审计', descGrid([
              ['数据范围', h('span', {}, [`${sel.value} → `, h('b', {}, roles.find((r) => r.name === sel.value)?.scope ?? '—')])],
              ['强制位置', 'service 层第一行 apply_data_scope（Router 过滤不算安全）'],
              ['员工端', h('span', {}, ['员工数据范围恒为 ', h('code', {}, 'SELF'), '，只能访问 ', h('code', {}, '/api/v1/self/**')])],
              ['成本可见', 'stock:cost:view 独立权限；仓管看不到成本单价'],
              ['变更留痕', '角色/权限/数据范围变更写 document_logs（doc_type=Role）'],
              ['日志不可删', 'document_logs 无 UPDATE/DELETE 权限（DB 层 REVOKE）'],
            ], 2)),
          ]),
        ]),
        h(ToastHost, { items: toast.items }),
      ]
  },
})