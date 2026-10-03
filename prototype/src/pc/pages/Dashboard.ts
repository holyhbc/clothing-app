import { h } from '../../components/h'
import { defineComponent, ref, computed } from 'vue'

import { Stat, StatusTag } from '../../components/ui'
import { dashboard, money } from '../../mock'
import { pageHead } from '../shared'

export default defineComponent({
  name: 'Dashboard',
  emits: ['navigate'],
  setup(_, { emit }) {
    const todoFilter = ref('全部')
    const tabs = ['全部', '待审核', '库存预警']
    const todos = computed(() =>
      todoFilter.value === '全部' ? dashboard.todos : dashboard.todos.filter((t) => t.type === todoFilter.value),
    )

    const pct = (a: number, b: number) => (b ? Math.min(100, Math.round((a / b) * 100)) : 0)

    return () => [
      pageHead('生产看板', '数据截至 2026-10-18 09:45 · 数据范围：缝制一组（本车间）', [
        h('button', { class: 'btn' }, '导出日报'),
        h('button', { class: 'btn primary' }, '去处理待办'),
      ]),

      h('div', { class: 'kpi-grid' },
        dashboard.kpis.map((k) =>
          h(Stat, {
            label: k.label, value: k.value, unit: k.unit,
            sub: k.sub, trend: k.trend, tip: k.tip, money: k.label.includes('工资'),
          }),
        ),
      ),

      h('div', { style: 'display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.25fr);gap:16px;align-items:start' }, [
        /* 待办 */
        h('div', { class: 'card', style: 'margin:0' }, [
          h('div', { class: 'card-head' }, [
            '我的待办',
            h('div', { class: 'card-head-actions' },
              tabs.map((t) =>
                h('button', {
                  class: ['btn', 'sm', todoFilter.value === t ? 'primary' : ''],
                  onClick: () => (todoFilter.value = t),
                }, t),
              ),
            ),
          ]),
          h('div', { class: 'card-body' },
            todos.value.map((t) =>
              h('div', { style: 'display:flex;gap:12px;align-items:center;padding:11px 0;border-bottom:1px solid var(--color-border)' }, [
                h(StatusTag, { status: t.status }),
                h('div', { style: 'flex:1;min-width:0' }, [
                  h('div', {}, t.title),
                  h('div', { class: 'muted' }, t.sub),
                ]),
                h('span', { class: 'muted nowrap' }, t.time),
                h('button', { class: 'btn sm', onClick: () => emit('navigate', t.type === '库存预警' ? 'stock' : t.status === 'SUBMITTED' ? 'payroll' : 'cutting') }, '处理'),
              ]),
            ),
            todos.value.length === 0 ? h('div', { class: 'muted', style: 'padding:24px;text-align:center' }, '没有待办') : null,
          ),
        ]),

        /* 款式进度 */
        h('div', { class: 'card', style: 'margin:0' }, [
          h('div', { class: 'card-head' }, ['款式生产进度', h('div', { class: 'card-head-actions' }, h('button', { class: 'btn sm' }, '查看全部'))]),
          h('div', { class: 'card-body tight' }, [
            h('table', { class: 'tbl' }, [
              h('thead', {}, h('tr', {}, [
                h('th', {}, '款号 / 名称'), h('th', {}, '订单'), h('th', {}, '已完工 / 订单'),
                h('th', { style: 'width:150px' }, '进度'), h('th', {}, '交期'), h('th', {}, '状态'),
              ])),
              h('tbody', {}, dashboard.styleProgress.map((s) => {
                const p = pct(s.finished, s.orderQty)
                return h('tr', {}, [
                  h('td', {}, [
                    h('div', { class: 'mono' }, s.style),
                    h('div', { class: 'muted' }, s.name),
                  ]),
                  h('td', { class: 'right tabular' }, qtyFmt(s.orderQty)),
                  h('td', { class: 'right tabular' }, `${qtyFmt(s.finished)} / ${qtyFmt(s.orderQty)}`),
                  h('td', {}, h('div', { class: ['progress', p >= 90 ? 'ok' : p < 20 ? 'warn' : ''] }, [h('i', { style: `width:${p}%` })])),
                  h('td', { class: 'nowrap' }, s.delivery),
                  h('td', {}, h('span', { class: 'status-tag ' + (s.state === '接近完成' ? 'st-approved' : s.state === '待裁' || s.state === '待投料' ? 'st-draft' : 'st-submitted') }, [h('span', { class: 'dot' }), s.state])),
                ])
              })),
            ]),
          ]),
        ]),
      ]),
    ]
  },
})

function qtyFmt(n: number | string) { return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 3 }) }