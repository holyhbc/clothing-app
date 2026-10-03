import { h } from '../components/h'
import { defineComponent, ref, computed } from 'vue'

import { useToast, ToastHost } from '../components/ui'
import { money, qtyFmt, myRange, myDaily } from '../mock'
import { MOBILE_PAGES } from '../pages/meta'

/* 模拟 iPhone 15 外框，PC 上预览 H5 */
export default defineComponent({
  name: 'MobileApp',
  setup() {
    const tab = ref('m-home' as (typeof MOBILE_PAGES)[number]['key'])
    const toast = useToast()
    const bindStyle = ref('HB-2026-0018')
    const selectedOp = ref('')
    const bound = ref<{ op: string; name: string; date: string }[]>([
      { op: '02', name: '拼前', date: '2026-10-18' },
      { op: '06', name: '查勘', date: '2026-10-18' },
    ])

    const ops = [
      { op: '01', name: '打边', bundle: 1, price: '0.42' },
      { op: '02', name: '拼前', bundle: 1, price: '0.85' },
      { op: '03', name: '装袖', bundle: 1, price: '1.20' },
      { op: '04', name: '合缝', bundle: 1, price: '0.68' },
      { op: '05', name: '锁眼钉扣', bundle: 1, price: '0.45' },
      { op: '06', name: '查勘', bundle: 12, price: '0.35' },
      { op: '07', name: '整烫包装', bundle: 12, price: '0.90' },
    ]

    const tabbar = [
      { key: 'm-home' as const, label: '今日', icon: '⌂' },
      { key: 'm-range' as const, label: '查区间', icon: '⩚' },
      { key: 'm-bind' as const, label: '绑工序', icon: '⊞' },
      { key: 'm-payroll' as const, label: '工资', icon: '¥' },
    ]
    const rangeFrom = ref('2026-10-01')
    const rangeTo = ref('2026-10-18')
    const groupBy = ref<'day' | 'week' | 'month'>('day')

    function bindOp() {
      if (!selectedOp.value) { toast.err('请先选择工序'); return }
      const o = ops.find((x) => x.op === selectedOp.value)!
      if (bound.value.some((b) => b.op === o.op && b.date === '2026-10-18')) { toast.err('该工序今天已绑定（唯一约束）'); return }
      bound.value.push({ op: o.op, name: o.name, date: '2026-10-18' })
      toast.ok(`已绑定 ${o.name}（${bindStyle.value}）`)
      selectedOp.value = ''
    }

    return () => [
      h('div', { style: 'min-height:100vh;background:#e9ebef;padding:24px 0;display:flex;gap:24px;align-items:flex-start;justify-content:center;flex-wrap:wrap' }, [
        /* 说明 */
        h('div', { style: 'width:330px;background:#fff;border-radius:8px;padding:20px;border:1px solid var(--color-border)' }, [
          h('div', { style: 'font-weight:600;font-size:16px;margin-bottom:8px' }, '员工端 H5（微信内打开）'),
          h('div', { class: 'muted', style: 'line-height:1.9;font-size:13px' }, [
            '符合 docs/06 §3：',
            h('br'), '· 触控目标 ≥ 48×48px，主要按钮高度 ≥ 50px',
            h('br'), '· 关键数字 ≥ 24px，等宽',
            h('br'), '· 3 秒内看到"今天做了多少、多少钱"',
            h('br'), '· 未审核工资显示预估并标注',
            h('br'), '· 员工数据范围恒为 SELF（后端强制）',
            h('br'), '· 扫码优先摄像头，手工输入保留降级',
          ]),
          h('div', { style: 'margin-top:14px' }, [
            h('a', { class: 'btn', href: '#/pc' }, '‹ 返回 PC 端'),
            h('button', { class: 'btn', style: 'margin-left:8px', onClick: () => toast.ok('已清缓存演示') }, '清缓存演示'),
          ]),
        ]),

        /* 手机 */
        h('div', { style: 'width:390px;background:#fff;border-radius:34px;border:9px solid #22272e;overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,.18);position:relative' }, [
          h('div', { style: 'height:38px;background:#fff;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600' }, '9:41'),
          h('div', { style: 'background:var(--color-primary);color:#fff;padding:14px 18px' }, [
            h('div', { style: 'font-size:15px;font-weight:600' }, ['服装厂 ERP', h('span', { style: 'float:right;font-weight:400;font-size:13px' }, '切换 ›')]),
            h('div', { style: 'font-size:12px;opacity:.85;margin-top:2px' }, '王海涛 · E1058 · 缝制一组'),
          ]),

          h('div', { style: 'height:600px;overflow-y:auto;background:var(--color-bg);padding:12px;box-sizing:border-box' },
            tab.value === 'm-home' ? mHome()
            : tab.value === 'm-range' ? mRange()
            : tab.value === 'm-bind' ? mBind()
            : tab.value === 'm-payroll' ? mPayroll()
            : mMe(),
          ),

          h('div', { style: 'display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--color-border);background:#fff;padding-bottom:8px' },
            tabbar.map((t) =>
              h('div', {
                style: `text-align:center;padding:8px 0;cursor:pointer;color:${tab.value === t.key ? 'var(--color-primary)' : 'var(--color-text-third)'}`,
                onClick: () => (tab.value = t.key),
              }, [
                h('div', { style: 'font-size:18px;line-height:1.2' }, t.icon),
                h('div', { style: 'font-size:11px;margin-top:2px' }, t.label),
              ]),
            ),
          ),
        ]),
      ]),
      h(ToastHost, { items: toast.items }),
    ]

    function mHome() {
      return [
        h('div', { style: 'background:#fff;border-radius:8px;padding:16px;text-align:center;margin-bottom:12px' }, [
          h('div', { style: 'color:var(--color-text-third);font-size:13px' }, '今天（2026-10-18）'),
          h('div', { style: 'font-size:52px;font-weight:700;line-height:1.1;margin:6px 0;font-variant-numeric:tabular-nums' }, '128'),
          h('div', { style: 'color:var(--color-text-third);font-size:13px' }, '件'),
          h('div', { style: 'font-size:30px;font-weight:700;color:var(--color-primary);margin-top:12px;font-variant-numeric:tabular-nums' }, ['¥', money(53.76)]),
          h('div', { style: 'color:var(--color-text-third);font-size:13px;margin-top:2px' }, '今日工钱（未审核，含预估）'),
          h('div', { style: 'display:inline-block;margin-top:10px;padding:4px 12px;border-radius:12px;background:var(--color-warning-bg);color:#8a6200;font-size:12px' }, '预估 · 以最终结算为准'),
        ]),
        h('div', { style: 'background:#fff;border-radius:8px;padding:14px;margin-bottom:12px' }, [
          h('div', { style: 'font-weight:600;margin-bottom:10px;font-size:14px' }, '我今天绑定的工序'),
          ...bound.value.map((b) =>
            h('div', { style: 'display:flex;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid var(--color-border);font-size:14px' }, [
              h('span', { class: 'chip', style: 'font-size:12px' }, b.op),
              h('span', { style: 'flex:1' }, `${b.name} · ${bindStyle.value}`),
              h('span', { class: 'muted', style: 'font-size:12px' }, `${b.date} 当天有效`),
            ]),
          ),
          h('div', { style: 'display:flex;gap:8px;margin-top:12px' }, [
            h('button', { class: 'btn lg', style: 'flex:1;height:50px', onClick: () => (tab.value = 'm-range') }, '⩚ 查区间累计'),
            h('button', { class: 'btn lg primary', style: 'flex:1;height:50px', onClick: () => (tab.value = 'm-bind') }, '⊞ 绑工序'),
          ]),
        ]),
        h('div', { style: 'background:#fff;border-radius:8px;padding:14px' }, [
          h('div', { style: 'font-weight:600;margin-bottom:8px;font-size:14px' }, '近 7 天'),
          h('div', { style: 'display:flex;align-items:flex-end;gap:8px;height:80px' },
            [[62, 0], [88, 1], [104, 2], [128, 3], [0, 4], [0, 5], [0, 6]].map(([v, i]) =>
              h('div', { style: 'flex:1;text-align:center' }, [
                h('div', { style: `height:${(v / 128) * 56}px;background:${i === 3 ? 'var(--color-primary)' : 'var(--color-primary-bg)'};border-radius:3px 3px 0 0` }),
                h('div', { style: 'font-size:10px;color:var(--color-text-third);margin-top:3px' }, ['12', '13', '14', '15', '16', '17', '18'][i]),
              ]),
            ),
          ),
        ]),
      ]
    }

    function mRange() {
      const max = Math.max(...myDaily.map((d) => d.qty))
      return [
        h('div', { style: 'background:#fff;border-radius:8px;padding:16px;margin-bottom:12px' }, [
          h('div', { style: 'font-size:15px;font-weight:600;margin-bottom:12px' }, '按时间区间查累计'),
          h('div', { style: 'display:flex;gap:8px;align-items:center' }, [
            h('input', {
              type: 'date', class: 'inp', style: 'flex:1;height:44px;font-size:14px', value: rangeFrom.value,
              onInput: (e: Event) => (rangeFrom.value = (e.target as HTMLInputElement).value),
            }),
            h('span', { class: 'muted' }, '至'),
            h('input', {
              type: 'date', class: 'inp', style: 'flex:1;height:44px;font-size:14px', value: rangeTo.value,
              onInput: (e: Event) => (rangeTo.value = (e.target as HTMLInputElement).value),
            }),
          ]),
          h('div', { style: 'display:flex;gap:8px;margin-top:10px' }, [
            h('button', {
              class: ['btn', 'lg', groupBy.value === 'day' ? 'primary' : ''],
              style: 'flex:1;height:44px;font-size:14px', onClick: () => (groupBy.value = 'day'),
            }, '按日'),
            h('button', {
              class: ['btn', 'lg', groupBy.value === 'week' ? 'primary' : ''],
              style: 'flex:1;height:44px;font-size:14px', onClick: () => (groupBy.value = 'week'),
            }, '按周'),
            h('button', {
              class: ['btn', 'lg', groupBy.value === 'month' ? 'primary' : ''],
              style: 'flex:1;height:44px;font-size:14px', onClick: () => (groupBy.value = 'month'),
            }, '按月'),
          ]),
          h('button', {
            class: 'btn primary lg', style: 'width:100%;height:50px;margin-top:12px;font-size:15px',
            onClick: () => toast.ok(`已按 ${rangeFrom.value} ~ ${rangeTo.value} 统计（演示）`),
          }, '查询'),
        ]),

        h('div', { style: 'background:#fff;border-radius:8px;padding:16px;margin-bottom:12px;text-align:center' }, [
          h('div', { style: 'color:var(--color-text-third);font-size:13px' }, `${rangeFrom.value} ~ ${rangeTo.value} 累计`),
          h('div', { style: 'font-size:40px;font-weight:700;line-height:1.15;margin:6px 0;font-variant-numeric:tabular-nums' }, qtyFmt(myRange[2].qty)),
          h('div', { style: 'color:var(--color-text-third);font-size:13px' }, '件'),
          h('div', { style: 'font-size:28px;font-weight:700;color:var(--color-primary);margin-top:10px;font-variant-numeric:tabular-nums' }, ['¥', money(myRange[2].amount)]),
          h('div', { style: 'color:var(--color-text-third);font-size:12px;margin-top:2px' }, '预估 · 含未审核'),
          h('button', {
            class: 'btn', style: 'width:100%;height:44px;margin-top:12px',
            onClick: () => toast.ok('已导出 CSV（演示）：工号,姓名,款号,工序,件数,单价,金额'),
          }, '导出明细 CSV'),
        ]),

        h('div', { style: 'background:#fff;border-radius:8px;padding:16px;margin-bottom:12px' }, [
          h('div', { style: 'font-size:14px;font-weight:600;margin-bottom:10px' }, '快捷区间'),
          ...myRange.map((r) => h('div', {
            style: 'display:flex;align-items:center;padding:13px 0;border-bottom:1px solid var(--color-border);cursor:pointer;min-height:48px',
            onClick: () => { rangeFrom.value = '2026-' + r.from; rangeTo.value = '2026-' + r.to; toast.ok('已选中：' + r.label) },
          }, [
            h('span', { style: 'flex:1;font-size:14px' }, r.label),
            h('span', { class: 'muted', style: 'font-size:13px;margin-right:10px' }, r.days + ' 天'),
            h('span', { style: 'font-size:16px;font-weight:700;font-variant-numeric:tabular-nums;margin-right:8px' }, qtyFmt(r.qty)),
            h('span', { class: 'amount', style: 'font-weight:600;color:var(--color-primary)' }, '¥' + money(r.amount)),
            h('span', { style: 'margin-left:8px;color:var(--color-text-third)' }, '›'),
          ])),
        ]),

        h('div', { style: 'background:#fff;border-radius:8px;padding:16px' }, [
          h('div', { style: 'font-size:14px;font-weight:600;margin-bottom:10px' }, '近 7 天明细（' + groupBy.value + '）'),
          ...myDaily.map((d) => h('div', { style: 'display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--color-border);font-size:14px' }, [
            h('span', { style: 'width:56px;flex:0 0 56px' }, d.d),
            h('span', { style: 'flex:1;height:8px;background:var(--color-border);border-radius:4px;overflow:hidden' },
              [h('span', { style: `display:block;height:100%;width:${(d.qty / max) * 100}%;background:var(--color-primary);border-radius:4px` })]),
            h('span', { style: 'width:52px;text-align:right;font-weight:600;font-variant-numeric:tabular-nums' }, qtyFmt(d.qty)),
            h('span', { class: 'amount', style: 'width:76px;text-align:right' }, '¥' + money(d.amount)),
          ])),
        ]),
      ]
    }

    function mBind() {
      return [
        h('div', { style: 'background:#fff;border-radius:8px;padding:16px;margin-bottom:12px;text-align:center' }, [
          h('div', { style: 'font-size:15px;font-weight:600;margin-bottom:10px' }, '① 扫描打菲二维码'),
          h('div', {
            style: 'height:150px;border:2px dashed var(--color-primary);border-radius:8px;display:flex;flex-direction:column;align-items:center;justify-content:center;background:var(--color-primary-bg);margin-bottom:12px',
          }, [
            h('div', { style: 'font-size:34px' }, '⊞'),
            h('div', { style: 'font-size:13px;color:var(--color-primary);margin-top:6px' }, '点击调起摄像头'),
          ]),
          h('div', { style: 'font-size:12px;color:var(--color-text-third);margin-bottom:10px' }, '摄像头不可用时用下方手工输入（降级通道必须保留）'),
          h('div', { style: 'display:flex;gap:8px' }, [
            h('input', { class: 'inp', style: 'flex:1;height:46px;font-size:14px', value: bindStyle.value, placeholder: 'BD-2026... 手工输入打菲号', onInput: (e: Event) => (bindStyle.value = (e.target as HTMLInputElement).value) }),
          ]),
          h('button', {
            class: 'btn primary', style: 'width:100%;height:50px;margin-top:10px;font-size:15px',
            onClick: () => (bindStyle.value = bindStyle.value || 'HB-2026-0018'),
          }, '解析二维码'),
        ]),
        h('div', { style: 'background:#fff;border-radius:8px;padding:16px;margin-bottom:12px' }, [
          h('div', { style: 'font-size:13px;color:var(--color-text-second);margin-bottom:4px' }, '② 款号（扫码带出）'),
          h('div', { style: 'font-size:19px;font-weight:600;margin-bottom:12px' }, bindStyle.value),
          h('div', { style: 'font-size:13px;color:var(--color-text-second);margin-bottom:10px' }, '③ 选择你做的工序（该款工序来自模板配置）'),
          ...ops.map((o) =>
            h('div', {
              style: `display:flex;align-items:center;gap:10px;padding:14px 12px;border:1px solid ${selectedOp.value === o.op ? 'var(--color-primary)' : 'var(--color-border)'};border-radius:8px;margin-bottom:8px;cursor:pointer;background:${selectedOp.value === o.op ? 'var(--color-primary-bg)' : '#fff'};min-height:48px`,
              onClick: () => (selectedOp.value = o.op),
            }, [
              h('input', { type: 'radio', checked: selectedOp.value === o.op, style: 'width:18px;height:18px', onChange: () => (selectedOp.value = o.op) }),
              h('span', { style: 'flex:1;font-size:15px' }, `${o.op} ${o.name}`),
              h('span', { class: 'muted', style: 'font-size:12px' }, `1扎 ${o.bundle} 件`),
              h('span', { class: 'amount', style: 'font-weight:600;color:var(--color-primary)' }, `¥${o.price}`),
            ]),
          ),
          h('button', { class: 'btn primary lg', style: 'width:100%;height:50px;margin-top:6px;font-size:15px', onClick: bindOp }, '确认绑定'),
          h('div', { style: 'font-size:12px;color:var(--color-text-third);margin-top:8px;line-height:1.6' }, '绑定当天有效，次日需重扫。同一款号同一工序当天只能绑一次（唯一约束）。'),
        ]),
      ]
    }

    function mMe() {
      return [
        h('div', { style: 'background:#fff;border-radius:8px;padding:16px;margin-bottom:12px' }, [
          h('div', { style: 'display:flex;align-items:center;gap:12px' }, [
            h('div', { style: 'width:48px;height:48px;border-radius:50%;background:var(--color-primary-bg);color:var(--color-primary);display:grid;place-items:center;font-size:18px;font-weight:600' }, '王'),
            h('div', {}, [
              h('div', { style: 'font-size:16px;font-weight:600' }, '王海涛'),
              h('div', { class: 'muted', style: 'font-size:13px' }, 'E1058 · 缝制一组 · 组长 王'),
            ]),
          ]),
          h('div', { style: 'display:grid;grid-template-columns:repeat(3,1fr);text-align:center;margin-top:14px;padding-top:14px;border-top:1px solid var(--color-border)' }, [
            ['本月件数', '3,214'], ['本月金额', '¥1,286.40'], ['累计天数', '14'],
          ].map(([k, v]) => h('div', {}, [
            h('div', { style: 'font-size:18px;font-weight:700;font-variant-numeric:tabular-nums' }, v),
            h('div', { style: 'font-size:12px;color:var(--color-text-third);margin-top:2px' }, k),
          ]))),
        ]),
        h('div', { style: 'background:#fff;border-radius:8px;margin-bottom:12px;overflow:hidden' }, [
          ([['我的绑定工序', () => (tab.value = 'm-bind')], ['我的计件明细', () => (tab.value = 'm-home')], ['我的工资单', () => (tab.value = 'm-payroll')]] as Array<[string, () => void]>).map(([t, f], i) =>
            h('div', {
              style: `padding:16px;font-size:15px;border-bottom:${i < 2 ? '1px solid var(--color-border)' : 'none'};min-height:48px;display:flex;align-items:center;cursor:pointer`,
              onClick: f as any,
            }, [t, h('span', { style: 'margin-left:auto;color:var(--color-text-third)' }, '›')]),
          ),
        ]),
        h('div', { style: 'background:#fff;border-radius:8px;padding:16px' }, [
          h('div', { style: 'font-size:14px;font-weight:600;margin-bottom:8px' }, '本月计件明细（按工序）'),
          ...[['打边 01', 320, '0.42', 134.4], ['拼前 02', 480, '0.85', 408], ['查勘 06', 2320, '0.35', 812]].map(([n, q, p, a]) =>
            h('div', { style: 'display:flex;align-items:center;padding:10px 0;border-bottom:1px solid var(--color-border);font-size:14px' }, [
              h('span', { style: 'flex:1' }, n),
              h('span', { class: 'muted', style: 'margin-right:14px;font-size:12px' }, `${qtyFmt(q as number)} 件 × ¥${p}`),
              h('span', { class: 'amount', style: 'font-weight:600' }, '¥' + money(a as number)),
            ]),
          ),
        ]),
      ]
    }

    function mPayroll() {
      const rows = [
        { period: '2026-10-01 ~ 2026-10-15', qty: 7102, amount: '1,286.40', st: '预估', cls: 'st-pending' },
        { period: '2026-08-01 ~ 2026-09-30', qty: 21460, amount: '4,182.60', st: '已审核', cls: 'st-approved' },
        { period: '2026-07-01 ~ 2026-07-31', qty: 18640, amount: '3,624.20', st: '已发放', cls: 'st-paid' },
        { period: '2026-06-01 ~ 2026-06-30', qty: 17220, amount: '3,318.80', st: '已发放', cls: 'st-paid' },
      ]
      return [
        h('div', { style: 'background:#fff;border-radius:8px;padding:16px;margin-bottom:12px' }, [
          h('div', { style: 'font-size:13px;color:var(--color-text-second)' }, '当前期（2026-10-01 ~ 2026-10-15）'),
          h('div', { style: 'font-size:38px;font-weight:700;color:var(--color-primary);margin:6px 0;font-variant-numeric:tabular-nums' }, ['¥', money(1286.4)]),
          h('div', { style: 'display:inline-block;padding:4px 12px;border-radius:12px;background:var(--color-warning-bg);color:#8a6200;font-size:12px' }, '预估 · 含未审核，以最终结算为准'),
          h('div', { style: 'font-size:13px;color:var(--color-text-third);margin-top:8px' }, '按工序 7,102 件 · 3 个工序'),
        ]),
        h('div', { style: 'background:#fff;border-radius:8px;padding:16px' }, [
          h('div', { style: 'font-size:14px;font-weight:600;margin-bottom:10px' }, '历史工资单'),
          ...rows.map((r) =>
            h('div', { style: 'padding:12px 0;border-bottom:1px solid var(--color-border)' }, [
              h('div', { style: 'display:flex;align-items:center' }, [
                h('span', { style: 'flex:1;font-size:14px' }, r.period),
                h('span', { class: 'status-tag ' + r.cls, style: 'font-size:11px' }, [h('span', { class: 'dot' }), r.st]),
              ]),
              h('div', { style: 'display:flex;align-items:baseline;margin-top:4px' }, [
                h('span', { style: 'font-size:20px;font-weight:700;font-variant-numeric:tabular-nums' }, ['¥', money(r.amount)]),
                h('span', { class: 'muted', style: 'margin-left:auto;font-size:12px' }, `${qtyFmt(r.qty)} 件`),
              ]),
            ]),
          ),
        ]),
      ]
    }
  },
})