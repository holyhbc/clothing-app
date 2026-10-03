import { h } from '../../components/h'
import { defineComponent, ref, computed } from 'vue'

import { Pager, useToast, ToastHost } from '../../components/ui'
import { Combo } from '../../components/Combo'
import { pageHead, card, field, select } from '../shared'
import { traceRows, styles, wipStocks, finishedStocks, productCategories, qtyFmt, money } from '../../mock'

const catName = (code: string) => productCategories.find((c) => c.code === code)?.name ?? code

/* ===================== 生产追踪（ADR-0023：把链路串成一条线） ===================== */
export const TracePage = defineComponent({
  name: 'TracePage',
  setup() {
    const toast = useToast()
    const tab = ref<'code' | 'wip' | 'fg'>('code')
    const style = ref('HB-2026-0018')
    const kw = ref('')
    const page = ref(1)

    const rows = computed(() =>
      traceRows.filter(
        (r) => (r.styleNo === style.value || style.value === '全部') && (!kw.value || r.bundleNo.toLowerCase().includes(kw.value.toLowerCase())),
      ),
    )
    const wip = computed(() => wipStocks.filter((r) => style.value === '全部' || r.styleNo === style.value))
    const fg = computed(() => finishedStocks.filter((r) => style.value === '全部' || r.styleNo === style.value))

    const chain = (r?: (typeof traceRows)[number]) =>
      h('div', { class: 'chain' }, [
        h('span', { class: 'node mono' }, r ? r.styleNo : 'HB-2026-0018'),
        h('span', { class: 'arrow' }, '→'),
        h('span', { class: 'node mono' }, r ? r.cutNo : 'CT-20261018-000012'),
        h('span', { class: 'arrow' }, '→'),
        h('span', { class: 'node mono' }, r ? 'BD-20261018-000031' : 'BD-20261018-000031'),
        h('span', { class: 'arrow' }, '→'),
        h('span', { class: 'node mono' }, r ? r.op : '打边'),
        h('span', { class: 'arrow' }, '→'),
        h('span', { class: ['node', 'cur', 'mono'] }, r ? r.bundleNo : 'XL01-0001'),
      ])

    const stageClass = (s: string) =>
      s === '已完工入库' ? 'done' : s === '待计件' ? 'wait' : s === '部分生产' ? 'part' : ''

    return () => [
      pageHead(
        '生产追踪',
        'ADR-0023：一件衣服现在在哪、谁做的、值多少钱 —— 款号 → 裁剪单 → 打菲单 → 工序 → 码 → 计件 → 工资，一条线看完',
        [h('button', { class: 'btn', onClick: () => toast.ok('已导出追踪台账 xlsx（演示）') }, '导出台账')],
      ),

      card(null, h('div', { class: 'filters' }, [
        h('div', { class: 'field' }, [
          h('label', {}, '款号'),
          h(Combo, {
            options: [
              { value: '全部', label: '全部款号' },
              ...styles.map((st) => ({ value: st.styleNo, label: st.styleNo, sub: st.name + '（' + (productCategories.find((c) => c.code === st.cat)?.name ?? '') + '）' })),
            ],
            modelValue: style.value,
            placeholder: '输入款号或款名搜索…（INV-9）',
            onChange: (v: string) => (style.value = v),
          }),
        ]),
        h('div', { class: 'field' }, [h('label', {}, '条码 / 码编号'), h('input', { class: 'inp w-lg', value: kw.value, onInput: (e: Event) => (kw.value = (e.target as HTMLInputElement).value) })]),
        h('button', { class: 'btn primary', onClick: () => (page.value = 1) }, '查询'),
        h('button', { class: 'btn' }, '重置'),
      ])),

      h('div', { class: 'alert info' }, ['溯源链：', chain(), '　—　任意一端都能反查全部上下游（ADR-0023 B21 / C41）']),

      h('div', { class: 'mode-switch', style: 'margin:0 0 12px' }, [
        h('button', { class: tab.value === 'code' ? 'on' : '', onClick: () => (tab.value = 'code') }, '码级追踪'),
        h('button', { class: tab.value === 'wip' ? 'on' : '', onClick: () => (tab.value = 'wip') }, '在制品 WIP'),
        h('button', { class: tab.value === 'fg' ? 'on' : '', onClick: () => (tab.value = 'fg') }, '成衣入库'),
      ]),

      tab.value === 'code'
        ? card('码级追踪（一码 = 一手 = 一个员工）', h('div', {}, [
            h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
              h('thead', {}, h('tr', {}, [
                h('th', {}, '码 bundle_no'), h('th', {}, '款号 / 分类'), h('th', {}, '来源裁剪单'), h('th', {}, '布批（缸号/匹号）'),
                h('th', {}, '工序'), h('th', {}, '手号'), h('th', { class: 'right' }, '应计件数'), h('th', { class: 'right' }, '实计件数'),
                h('th', {}, '计件人'), h('th', { class: 'right' }, '工价金额'), h('th', {}, '当前状态'),
              ])),
              h('tbody', {}, rows.value.map((r) =>
                h('tr', {}, [
                  h('td', { class: 'mono nowrap' }, r.bundleNo),
                  h('td', {}, [h('div', { class: 'mono' }, r.styleNo), h('div', { class: 'muted' }, catName(r.cat))]),
                  h('td', { class: 'mono nowrap' }, [
                    h('a', { style: 'cursor:pointer;color:var(--color-primary)' }, r.cutNo),
                  ]),
                  h('td', { class: 'mono nowrap muted' }, `${r.dyeLot} / ${r.bolt}`),
                  h('td', { class: 'nowrap' }, r.op),
                  h('td', { class: 'nowrap' }, r.hand),
                  h('td', { class: 'right amount' }, String(r.qty)),
                  h('td', { class: ['right', 'amount'], style: r.counted < r.qty ? 'color:var(--color-warning);font-weight:600' : 'font-weight:600' }, String(r.counted)),
                  h('td', { class: 'nowrap' }, r.emp),
                  h('td', { class: 'right amount' }, r.amount === '—' ? '—' : '¥' + r.amount),
                  h('td', {}, h('span', { class: ['stage-pill', stageClass(r.stage)] }, r.stage)),
                ]),
              )),
            ])),
            h(Pager, { total: rows.value.length, page: page.value, 'onUpdate:page': (p: number) => (page.value = p) }),
          ]), true)
        : null,

      tab.value === 'wip'
        ? h('div', {}, [
            h('div', { class: 'alert info' }, ['ⓘ ', 'ADR-0018：裁剪审核即把裁片入在制品台账（不计成本）；尾数不入 WIP。WIP 结存 = 累计转入 − 累计转出。']),
            card('在制品 WIP（裁片）', h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
              h('thead', {}, h('tr', {}, [
                h('th', {}, '款号'), h('th', {}, '分类'), h('th', {}, '颜色'), h('th', {}, '尺码'),
                h('th', { class: 'right' }, '累计转入'), h('th', { class: 'right' }, '累计转出'), h('th', { class: 'right' }, '在制结存'),
                h('th', {}, '最近流转'), h('th', {}, '状态'),
              ])),
              h('tbody', {}, wip.value.map((r) =>
                h('tr', {}, [
                  h('td', { class: 'mono nowrap' }, r.styleNo),
                  h('td', { class: 'muted' }, catName(r.cat)),
                  h('td', { class: 'nowrap' }, r.color),
                  h('td', { class: 'nowrap' }, r.size),
                  h('td', { class: 'right amount' }, String(r.inQty)),
                  h('td', { class: 'right amount' }, String(r.outQty)),
                  h('td', { class: 'right amount', style: 'font-weight:600' }, String(r.qty)),
                  h('td', { class: 'nowrap muted' }, r.last),
                  h('td', {}, h('span', { class: ['stage-pill', r.state === '已转成衣' ? 'done' : 'part'] }, r.state)),
                ]),
              )),
            ])), true),
          ])
        : null,

      tab.value === 'fg'
        ? h('div', {}, [
            h('div', { class: 'alert info' }, ['ⓘ ', 'ADR-0018：成衣入库时机 = 该款「最后一道工序」扫码完成时自动转库；漏扫可用「手动完单入库」（stock:in:manual，必填原因）。成本在此时一次性结转 = 面料实际成本 + 已计工价。']),
            h('div', { class: 'alert warn' }, ['⚠ ', '成本不含采购运费（ADR-0019），因此本页金额与毛利口径均未扣运费。']),
            card('成衣库存（可销售）', h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
              h('thead', {}, h('tr', {}, [
                h('th', {}, '款号'), h('th', {}, '分类'), h('th', {}, '颜色'), h('th', {}, '尺码'),
                h('th', { class: 'right' }, '数量'), h('th', { class: 'right' }, '单位成本'), h('th', { class: 'right' }, '金额'), h('th', {}, '入库方式'),
              ])),
              h('tbody', {}, fg.value.map((r) =>
                h('tr', {}, [
                  h('td', { class: 'mono nowrap' }, r.styleNo),
                  h('td', { class: 'muted' }, catName(r.cat)),
                  h('td', { class: 'nowrap' }, r.color),
                  h('td', { class: 'nowrap' }, r.size),
                  h('td', { class: 'right amount', style: 'font-weight:600' }, String(r.qty)),
                  h('td', { class: 'right amount' }, r.unit),
                  h('td', { class: 'right amount' }, '¥' + money(r.amount)),
                  h('td', { class: 'muted nowrap' }, r.in),
                ]),
              )),
            ])), true),
            card('手动完单入库（漏扫 / 返修补入库）', h('div', { class: 'note-strip' }, [
              '权限 stock:in:manual · 必填原因（10006）· 写 document_logs · 超过 WIP 结存返回 40007 · ',
              h('b', {}, '不允许把裁片直接当作成衣入库'),
            ])),
          ])
        : null,

      h(ToastHost, { items: toast.items }),
    ]
  },
})