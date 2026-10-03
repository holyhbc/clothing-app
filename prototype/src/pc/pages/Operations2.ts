import { h } from '../../components/h'
import { defineComponent, ref, computed } from 'vue'

import { StatusTag, Pager, Modal, useToast, ToastHost } from '../../components/ui'
import { pageHead, card, descGrid } from '../shared'
import { purchaseOrders, purchaseLines, arrivals, money, qtyFmt } from '../../mock'

/* ===================== 采购单（ADR-0015：审核确认订单 + 分批到货入库） ===================== */
export const PurchasePage = defineComponent({
  name: 'PurchasePage',
  setup() {
    const toast = useToast()
    const detail = ref(false)
    const reverse = ref(false)
    const reason = ref('')
    const reasonErr = ref('')
    const arrivalOpen = ref(false)
    // 互斥：同一时刻只开一个弹窗（生产由 Modal 层管理，原型显式约束）
    const openArrival = () => { arrivalOpen.value = true }

    const FREIGHT = 2220.0
    const totalGoods = purchaseLines.reduce((s, l) => s + Number(l.amount.replace(/[,]/g, '')), 0)
    const alloc = purchaseLines.map((l, i) => {
      const a = Number(l.amount.replace(/[,]/g, ''))
      const v = i === purchaseLines.length - 1
        ? FREIGHT - purchaseLines.slice(0, -1).reduce((s, x) => s + FREIGHT * (Number(x.amount.replace(/[,]/g, '')) / totalGoods), 0)
        : FREIGHT * (a / totalGoods)
      return { ...l, allocFreight: v }
    })

    const list = () => [
      pageHead('采购单', '审核**只确认订单，不入库**；**分批到货登记才入库**（ADR-0015）。运费 / 染费 / 加工费在单据头录入，**三项费用只记录、不进成本**（ADR-0019 / ADR-0024）', [
        h('button', { class: 'btn' }, '导出'),
        h('button', { class: 'btn' }, '导入采购单'),
        h('button', { class: 'btn primary' }, '+ 新建采购单'),
      ]),
      card('列表', [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '单据号'), h('th', {}, '供应商'), h('th', {}, '单据日期'),
            h('th', { class: 'right' }, '货值'), h('th', { class: 'right' }, '运费'), h('th', { class: 'right' }, '染费'), h('th', { class: 'right' }, '加工费'),
            h('th', {}, '到货进度'), h('th', {}, '状态'), h('th', { style: 'width:230px' }, '操作'),
          ])),
          h('tbody', {}, purchaseOrders.map((o, i) => {
            const prog = [
              { done: 3, all: 3, txt: '3/3 次已到货' },
              { done: 1, all: 1, txt: '1/1 次已到货' },
              { done: 1, all: 1, txt: '1/1 次已到货' },
              { done: 1, all: 2, txt: '1/2 次已到货' },
              { done: 0, all: 2, txt: '未到货' },
            ][i]
            return h('tr', { key: o.docNo }, [
              h('td', { class: 'mono nowrap' }, o.docNo),
              h('td', { class: 'nowrap' }, o.supplier),
              h('td', { class: 'nowrap' }, o.date),
              h('td', { class: 'right amount' }, money(o.total)),
              h('td', { class: 'right amount muted' }, i === 0 ? money(FREIGHT) : '0.00'),
              h('td', { class: 'right amount muted' }, i === 0 ? '1,860.00' : '0.00'),
              h('td', { class: 'right amount muted' }, '0.00'),
              h('td', {}, [
                h('div', { class: 'nowrap', style: 'font-size:13px' }, prog.txt),
                h('div', { class: ['progress', prog.done === prog.all && prog.all > 0 ? 'ok' : ''], style: 'margin-top:4px;height:5px' },
                  [h('i', { style: `width:${prog.all ? (prog.done / prog.all) * 100 : 0}%` })]),
              ]),
              h('td', {}, h(StatusTag, { status: o.status })),
              h('td', { class: 'row-actions' }, [
                h('a', { onClick: () => (detail.value = true) }, '详情'),
                o.status === 'DRAFT' ? h('a', {}, '编辑') : null,
                o.status === 'SUBMITTED' ? h('a', { onClick: () => toast.ok('已审核（演示）：只生成应付承诺，不产生库存批次') }, '审核') : null,
                o.status === 'APPROVED' && prog.done < prog.all ? h('a', { onClick: openArrival }, '到货登记') : null,
                o.status === 'APPROVED' && prog.done < prog.all ? h('a', { class: 'danger', onClick: () => (reverse.value = true) }, '反审核') : null,
                o.status === 'APPROVED' && prog.done > 0 ? h('a', { onClick: () => toast.err('50012 还有未冲销的到货，请先逐次冲销') }, '反审核') : null,
              ]),
            ])
          })),
        ])),
      ], undefined, true),

      card('采购 vs 到货的职责拆分（ADR-0015）', h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
        h('thead', {}, h('tr', {}, [h('th', {}, '动作'), h('th', {}, '入库？'), h('th', {}, '生成应付？'), h('th', {}, '生成凭证？'), h('th', {}, '权限点')])),
        h('tbody', {}, [
          ['采购单 审核', h('span', { style: 'color:var(--color-danger)' }, '❌ 不入库'), '✅ 应付承诺', h('span', { style: 'color:var(--color-danger)' }, '❌'), 'purchase:approve'],
          ['到货登记', h('span', { style: 'color:var(--color-success)' }, '✅ 入库唯一入口'), '✅ 按到货金额冲减', '✅ 入库凭证', 'purchase:arrival'],
          ['到货反审核', '↩ 冲回该次批次', '↩ 冲减该次应付', '↩ 红冲该次凭证', 'purchase:arrival:reverse'],
          ['采购单反审核', '必须先冲完全部到货（否则 50012）', '—', '—', 'purchase:reverse'],
        ].map((r) => h('tr', { key: r[0] }, [
          h('td', {}, h('b', {}, r[0])), h('td', {}, r[1]), h('td', {}, r[2]), h('td', {}, r[3]),
          h('td', {}, h('code', { class: 'muted' }, r[4])),
        ]))),
      ])), true),

      card('无委外加工（业务方 2026-10-01 确认）', h('div', { class: 'alert info' }, [
        '业务方明确「没有外裁」，因此', h('b', {}, '不建委外发出/委外收回模块'),
        '。物料全部走"采购 → 库存 → 裁剪"，成衣全部自产。若未来出现委外，需新增独立模块（采购单不能代替委外单）。',
      ])),
    ]

    const detailView = () => [
      h('div', { style: 'margin-bottom:12px' }, h('button', { class: 'btn sm', onClick: () => (detail.value = false) }, '‹ 返回列表')),
      pageHead('采购单详情', undefined, [
        h('button', { class: 'btn primary', onClick: openArrival }, '+ 登记到货（第 4 次）'),
        h('button', { class: 'btn danger', onClick: () => (reverse.value = true) }, '反审核'),
      ]),
      h('div', { style: 'display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap' }, [
        h('span', { class: 'mono', style: 'font-size:18px;font-weight:600' }, 'PO-20261018-000012'),
        h('button', { class: 'btn sm' }, '复制'),
        h(StatusTag, { status: 'APPROVED' }),
        h('span', { class: 'muted' }, '已到货 3/3 次 · 累计应付 ¥260,372.00（不含三项费用）/ 订单货值 ¥267,602.00'),
      ]),
      card('单据头（含运费单号 / 发票号）', descGrid([
        ['供应商', '盛虹纺织有限公司 · 陈经理 · 月结 30 天'],
        ['单据日期', '2026-10-18'],
        ['预计到货日', '2026-10-25'],
        ['收货仓库', '原料一仓'],
        ['发票号', 'SH-260918-0032'],
        ['运单号', 'YD-20261018-7788'],
      ], 3)),
      card('采购侧费用（单据级录入，★ 三项都只记录，不进成本）', h('div', {}, [
        descGrid([
          ['运费 freight_amount', h('b', { class: 'amount' }, '¥' + money(FREIGHT))],
          ['运单号 freight_no', h('span', { class: 'mono' }, 'YD-20261018-7788')],
          ['染费 dyeing_amount', h('b', { class: 'amount' }, '¥1,860.00')],
          ['加工费 processing_amount', h('b', { class: 'amount' }, '¥0.00（无后整 / 缩水）')],
          ['进成本', h('span', { class: 'muted' }, '三项费用均不进批次成本、不进领料成本、不进成衣成本、不进损益、不生成凭证行（ADR-0019 / ADR-0024）')],
          ['批次成本', h('span', {}, ['恒为 ', h('b', {}, '到货金额 ÷ 数量'), '（= 重量 × 单价），与供应商对账单口径一致'])]
        ], 3),
        h('div', { class: 'alert warn', style: 'margin-top:10px' }, [
          '⚠ ', '应付与凭证口径：借 批次成本合计；贷 2202 应付账款 = ', h('b', {}, '货值 ¥' + money(totalGoods)),
          '（', '不含运费 / 染费 / 加工费', '）。毛利报表亦未扣这三项，页面已标注。',
        ]),
        h('div', { class: 'note-strip', style: 'margin-top:8px' }, [
          '~~运费分摊试算~~ 接口已下线（ADR-0019）；三项费用已到货后仍可修改（只改台账记录，不动已锁定批次成本），每次修改写 document_logs。',
        ]),
      ])),
      card('采购明细（一行一缸；门幅以采购单为准）', [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '#'), h('th', {}, '物料'), h('th', {}, '颜色'), h('th', {}, '缸号(默认)'),
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
            h('td', { class: 'right amount' }, h('span', {}, [l.width, h('span', { class: 'muted', style: 'font-size:11px' }, ' 实测')])),
            h('td', { class: 'right amount' }, l.price),
            h('td', { class: 'right amount', style: 'font-weight:600' }, money(l.amount)),
            h('td', { style: 'font-size:12px' }, [
              h('div', {}, l.remark),
              l.rework ? h('div', { class: 'tl-reason', style: 'margin-top:4px' }, '返修说明：' + l.rework) : null,
            ]),
          ]))),
        ])),
        h('div', { class: 'alert warn', style: 'margin:12px 16px 0' }, [
          '计量一致性：重量 88.6kg vs 匹数×米数 72.4m，差异 ',
          h('b', {}, '4.8%'), '，在 5% 阈值内，', h('b', {}, '仅提示不阻断'), '（ADR-0012 规则 3）。',
        ]),
      ], undefined, true),

      card('分批到货记录（入库唯一入口）', [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '到货次'), h('th', {}, '到货时间'), h('th', { class: 'right' }, '到货行'),
            h('th', { class: 'right' }, '匹数'), h('th', { class: 'right' }, '到货金额'),
            h('th', { class: 'right' }, '批次成本(元/米)'), h('th', {}, '生成批次'), h('th', {}, '登记人'), h('th', {}, '备注'),
            h('th', { style: 'width:110px' }, '操作'),
          ])),
          h('tbody', {}, arrivals.map((a) => h('tr', { key: a.no }, [
            h('td', {}, h('b', {}, `第 ${a.no} 次`)),
            h('td', { class: 'nowrap muted' }, a.date),
            h('td', { class: 'right tabular' }, String(a.lines)),
            h('td', { class: 'right amount' }, a.bolts),
            h('td', { class: 'right amount', style: 'font-weight:600' }, money(a.amount)),
            h('td', { class: 'right amount muted' }, '28.500000'),
            h('td', { class: 'mono muted', style: 'font-size:12px' }, a.stockIds),
            h('td', { class: 'nowrap muted' }, a.operator),
            h('td', { style: 'font-size:12px;max-width:180px' }, a.remark),
            h('td', { class: 'row-actions' }, [
              h('a', {}, '详情'),
              h('a', { class: 'danger', onClick: () => toast.err('若该批次已被裁剪消耗 → 40005 拒绝') }, '冲销'),
            ]),
          ]))),
        ])),
        h('div', { class: 'alert info', style: 'margin:12px 16px 16px' }, [
          '⚠ 缸号可覆盖：第 2 次到货的缸号是 ',
          h('code', {}, 'DY-2609-02'),
          '，与采购行默认缸号 ',
          h('code', {}, 'DY-2609-01'),
          ' 不同 —— 分批染色常见，到货行为准。',
        ]),
      ], undefined, true),
    ]

    return () => {
      const els: unknown[] = [detail.value ? detailView() : list()]

      els.push(arrivalOpen.value
        ? h(Modal, { title: '到货登记 · PO-20261018-000012（第 4 次）', wide: true }, {
            body: h('div', {}, [
              h('div', { class: 'alert info' }, ['到货登记 = ', h('b', {}, '入库唯一入口'), '。当场生成批次 + 台账 + 冲减应付 + 入库凭证，一个事务，任一失败整体回滚。']),
              h('div', { class: 'form-row' }, [h('label', { class: 'required' }, '到货日期'), h('div', { class: 'grow' }, h('input', { class: 'inp', type: 'date', style: 'max-width:180px', value: '2026-10-28' }))]),
              h('div', { class: 'form-row' }, [h('label', {}, '到货备注'), h('div', { class: 'grow' }, h('textarea', { class: 'inp', rows: 2, placeholder: '例如：其中 1 匹外包装破损，已拍照留档' }))]),
              h('div', { style: 'font-weight:600;margin:14px 0 8px;font-size:14px' }, '本次到货明细（可改匹数 / 重量 / 缸号 / 门幅）'),
              h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
                h('thead', {}, h('tr', {}, [
                  h('th', {}, '采购行'), h('th', { class: 'right' }, '应到匹数'), h('th', { class: 'right' }, '已到'),
                  h('th', { class: 'right' }, '本次到货'), h('th', { class: 'right' }, '本次重量kg'),
                  h('th', {}, '缸号（可覆盖）'), h('th', { class: 'right' }, '实测门幅cm'),
                ])),
                h('tbody', {}, purchaseLines.map((l, i) => h('tr', { key: l.lineNo }, [
                  h('td', { class: 'mono' }, `行 ${l.lineNo} · ${l.material}`),
                  h('td', { class: 'right tabular' }, qtyFmt(l.bolt)),
                  h('td', { class: 'right tabular muted' }, qtyFmt(i === 0 ? 12 : 0)),
                  h('td', {}, h('input', { class: 'inp', style: 'width:90px', value: i === 0 ? '0' : '2', type: 'number' })),
                  h('td', {}, h('input', { class: 'inp', style: 'width:110px', value: i === 0 ? '0' : '88.6' })),
                  h('td', {}, h('input', { class: 'inp', style: 'width:150px', value: i === 0 ? '' : 'DY-2609-05' })),
                  h('td', {}, h('input', { class: 'inp', style: 'width:90px', value: i === 0 ? '' : '151.0' })),
                ]))),
              ])),
              h('div', { class: 'alert warn', style: 'margin-top:12px' }, [
                '门幅按', h('b', {}, '采购过来的单子输入'),
                '（此处填实测值，可覆盖采购行与物料默认值）。累计到货 > 应到货会报 ',
                h('code', {}, '50011'), '。',
              ]),
              h('div', { class: 'form-row', style: 'margin-top:14px' }, [
                h('label', {}, '本次批次成本'),
                h('div', { class: 'grow' }, h('div', { class: 'chip' }, ['到货金额 ÷ 数量 = ', h('b', {}, '¥28.500000 / 米'), h('span', { class: 'muted' }, '（不含运费 / 染费 / 加工费，ADR-0019 / ADR-0024）')])),
              ]),
            ]),
            footer: [
              h('button', { class: 'btn' }, '取消'),
              h('button', { class: 'btn primary', onClick: () => { toast.ok('到货已登记并入库（演示）：生成 2 个批次 + 冲减应付 + 入库凭证'); arrivalOpen.value = false } }, '确认登记并入库'),
            ],
          })
        : null)

      els.push(reverse.value
        ? h(Modal, { title: '反审核采购单' }, {
            body: h('div', {}, [
              h('div', { class: 'alert danger' }, ['! ', '本单还有 ', h('b', {}, '3 次到货'), ' 未冲销 → ', h('code', {}, '50012'), ' 拒绝。必须先逐次冲销到货。']),
              h('div', { class: 'alert warn' }, ['冲销顺序：先 ', h('b', {}, '按到货反审核'), '（冲回该次批次、台账、凭证、应付），全部冲完才允许反审核单据本身。']),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '原因'),
                h('div', { class: 'grow' }, [
                  h('textarea', { class: 'inp', rows: 3, placeholder: '例如：与供方协商退 2 匹，门幅不足', value: reason.value, onInput: (e: Event) => { reason.value = (e.target as HTMLTextAreaElement).value; reasonErr.value = '' } }),
                  reasonErr.value ? h('div', { class: 'form-err' }, reasonErr.value) : null,
                ]),
              ]),
            ]),
            footer: [
              h('button', { class: 'btn' }, '取消'),
              h('button', { class: 'btn primary', onClick: () => { toast.err('50012 还有 3 次到货未冲销，请先逐次冲销到货'); reasonErr.value = '50012 尚有未冲销到货' } }, '先去冲销到货'),
            ],
          })
        : null)

      els.push(h(ToastHost, { items: toast.items }))
      return els.filter(Boolean) as any
    }
  },
})

/* ===================== 裁剪明细三种录入模式（ADR-0014） ===================== */
