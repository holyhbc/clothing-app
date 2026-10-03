import { h } from '../../components/h'
import { defineComponent, ref, computed } from 'vue'

import { Pager, useToast, ToastHost } from '../../components/ui'
import { Combo } from '../../components/Combo'
import { pageHead, card, field, select } from '../shared'
import { ledgerSummary, ledgerDetails, suppliers, qtyFmt, money } from '../../mock'

/* ===================== 采购台账（ADR-0022：供应商维度） ===================== */
export const LedgerPage = defineComponent({
  name: 'LedgerPage',
  setup() {
    const toast = useToast()
    const page = ref(1)
    const supplier = ref('全部')
    const from = ref('2026-09-01')
    const to = ref('2026-10-31')
    const kw = ref('')

    const rows = computed(() =>
      ledgerDetails.filter(
        (r) =>
          (supplier.value === '全部' || r.supplier === supplier.value) &&
          r.date >= from.value &&
          r.date <= to.value &&
          (!kw.value || (r.dyeLot + r.color + r.material).toLowerCase().includes(kw.value.toLowerCase())),
      ),
    )

    const totals = computed(() => {
      const t = { weight: 0, goods: 0 }
      rows.value.forEach((r) => {
        t.weight += Number(String(r.weight).replace(/,/g, ''))
        t.goods += Number(r.amount.replace(/,/g, ''))
      })
      return { weight: t.weight, goods: t.goods }
    })

    return () => [
      pageHead(
        '采购台账',
        'ADR-0022：按供应商看清账 —— 总重量 / 总货值 / 总运费 / 总染费 / 总加工费 / 应付 / 已付 / 未付。★ 三项费用只做记录，不摊进批次成本（ADR-0019 / ADR-0024）',
        [
          h('button', { class: 'btn', onClick: () => toast.ok('已导出到货明细 xlsx（演示）：' + rows.value.length + ' 行') }, '导出明细'),
          h('button', { class: 'btn primary' }, '打印对账单'),
        ],
      ),

      card(null, h('div', { class: 'filters' }, [
        h('div', { class: 'field' }, [
          h('label', {}, '供应商'),
          h(Combo, {
            options: [
              { value: '全部', label: '全部供应商' },
              ...suppliers.map((v) => ({ value: v.name, label: `${v.code} ${v.name}`, sub: `${v.contact}　${v.settle}` })),
            ],
            modelValue: supplier.value,
            placeholder: '输入供应商编码或名称搜索…（INV-9）',
            onChange: (v: string) => (supplier.value = v),
          }),
        ]),
        h('div', { class: 'field' }, [h('label', {}, '收货日期'), h('input', { class: 'inp', value: from.value, onInput: (e: Event) => (from.value = (e.target as HTMLInputElement).value) })]),
        h('div', { class: 'field' }, [h('label', {}, '至'), h('input', { class: 'inp', value: to.value, onInput: (e: Event) => (to.value = (e.target as HTMLInputElement).value) })]),
        field('缸号 / 颜色 / 面料', kw.value, 'w-lg'),
        h('button', { class: 'btn primary', onClick: () => (page.value = 1) }, '查询'),
        h('button', { class: 'btn' }, '重置'),
      ])),

      card(
        '供应商汇总（应付 = 到货金额，不含运费 / 染费 / 加工费）',
        h('div', { class: 'ledger-grid' }, ledgerSummary.map((s) =>
          h('div', { class: 'ledger-card' }, [
            h('h4', {}, [s.supplier, h('span', { class: 'muted' }, `${s.receipts} 次到货`)]),
            h('div', { class: 'ledger-rows' }, [
              h('span', { class: 'k' }, '总重量'), h('span', { class: 'v' }, s.weight + ' kg'),
              h('span', { class: 'k' }, '总匹数'), h('span', { class: 'v' }, s.bolts + ' 匹'),
              h('span', { class: 'k' }, '总货值'), h('span', { class: 'v' }, '¥' + s.goods),
              h('span', { class: 'k' }, ['总运费 ', h('span', { class: 'muted', title: 'ADR-0019：只记录，不进成本、不进损益' }, 'ⓘ')]), h('span', { class: 'v warn' }, '¥' + s.freight),
              h('span', { class: 'k' }, ['总染费 ', h('span', { class: 'muted', title: 'ADR-0024：染色费只记录，不进成本' }, 'ⓘ')]), h('span', { class: 'v warn' }, '¥' + s.dyeing),
              h('span', { class: 'k' }, ['总加工费 ', h('span', { class: 'muted', title: 'ADR-0024：后整 / 缩水等只记录，不进成本' }, 'ⓘ')]), h('span', { class: 'v warn' }, '¥' + s.processing),
              h('span', { class: 'k' }, '应付'), h('span', { class: 'v' }, '¥' + s.payable),
              h('span', { class: 'k' }, '已付'), h('span', { class: 'v' }, '¥' + s.paid),
              h('span', { class: 'k' }, '未付'), h('span', { class: ['v', s.unpaid === '0.00' ? '' : 'danger'] }, '¥' + s.unpaid),
              h('span', { class: 'k' }, '已付比例'), h('span', { class: 'v' }, s.rate),
            ]),
            h('div', { class: 'progress', style: 'margin-top:10px' }, [h('i', { style: `width:${s.rate}` })]),
          ]),
        )),
      ),

      h('div', { class: 'alert info' }, ['ⓘ ', '★ ', h('b', {}, '运费 / 染费 / 加工费三项全部只记录，不摊进批次成本'), '（ADR-0019 / ADR-0024），所以单件成本 = 重量 × 单价，与供应商对账单口径一致；代价是毛利报表未扣这三项费用，毛利页已标注。']),
      h('div', { class: 'alert warn' }, ['⚠ ', '应付 = 到货金额（不含三项费用）；已付按付款单核销；已付 > 应付返回 50013。']),

      card('到货明细（金额 = 重量 × 单价）', h('div', {}, [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '收货日期'), h('th', {}, '供应商'), h('th', {}, '面料名称'), h('th', {}, '缸号'), h('th', {}, '颜色'),
            h('th', { class: 'right' }, '重量 KG'), h('th', { class: 'right' }, '单价'), h('th', { class: 'right' }, '金额'),
            h('th', {}, '采购单 / 到货单'), h('th', {}, '操作人'),
          ])),
          h('tbody', {}, rows.value.map((r) =>
            h('tr', {}, [
              h('td', { class: 'nowrap' }, r.date),
              h('td', { class: 'nowrap' }, r.supplier),
              h('td', { class: 'nowrap' }, r.material),
              h('td', { class: 'mono nowrap' }, r.dyeLot),
              h('td', { class: 'nowrap' }, r.color),
              h('td', { class: 'right amount' }, qtyFmt(r.weight)),
              h('td', { class: 'right amount' }, r.price),
              h('td', { class: 'right amount', style: 'font-weight:600' }, '¥' + r.amount),
              h('td', {}, [h('div', { class: 'mono' }, r.po), h('div', { class: 'mono muted' }, r.arr)]),
              h('td', { class: 'nowrap' }, r.user),
            ]),
          )),
        ])),
        h('div', { class: 'sticky-actions' }, [
          h('span', { class: 'muted', style: 'margin-right:auto' }, [
            '筛选结果：', h('b', {}, String(rows.value.length)), ' 行 ｜ 合计重量 ',
            h('b', { class: 'amount' }, qtyFmt(totals.value.weight) + ' kg'),
            ' ｜ 合计货值 ', h('b', { class: 'amount' }, '¥' + money(totals.value.goods)),
            '（不含运费）',
          ]),
        ]),
        h(Pager, { total: rows.value.length, page: page.value, 'onUpdate:page': (p: number) => (page.value = p) }),
      ]), true),

      h(ToastHost, { items: toast.items }),
    ]
  },
})