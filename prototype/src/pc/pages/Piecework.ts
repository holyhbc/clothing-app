import { h } from '../../components/h'
import { Combo } from '../../components/Combo'
import { defineComponent, ref, computed } from 'vue'

import { StatusTag, Pager, Modal, useToast, ToastHost } from '../../components/ui'
import { pageHead, card, descGrid } from '../shared'
import { pieceworkLogs, periods, payrollRows, money, qtyFmt, styles } from '../../mock'

export default defineComponent({
  name: 'PieceworkPage',
  emits: ['navigate'],
  setup(_, { emit }) {
    const toast = useToast()
    return () => [
      pageHead('计件流水', 'append-only（INV-2）：只能红冲 + 补录，禁止修改数量与金额；单价为落库时快照（INV-3）', [
        h('button', { class: 'btn', onClick: () => emit('navigate', 'station') }, '工位机计件 ›'),
        h('button', { class: 'btn' }, '导出 CSV'),
      ]),
      card('列表', h('div', {}, [
        h('div', { class: 'filters', style: 'margin-bottom:12px' }, [
          h('div', { class: 'field' }, [h('label', {}, '打菲号'), h('input', { class: 'inp w-lg', placeholder: 'BD-20261017-000026-0000431' })]),
          h('div', { class: 'field' }, [h('label', {}, '款号'), h(Combo, {
            options: styles.map((st) => ({ value: st.styleNo, label: st.styleNo, sub: st.name })),
            modelValue: 'HB-2026-0018',
            placeholder: '输入款号或款名搜索…（INV-9）',
            width: '200px',
          })]),
          h('div', { class: 'field' }, [h('label', {}, '工序'), h(Combo, {
            options: [
              { value: '全部', label: '全部工序' },
              { value: '打边', label: '打边', sub: '01　¥0.42/件' },
              { value: '拼前', label: '拼前', sub: '02　¥0.85/件' },
              { value: '查勘', label: '查勘', sub: '06　¥0.35/件' },
              { value: '整烫包装', label: '整烫包装', sub: '07　¥0.90/件　★ 最后工序' },
            ],
            modelValue: '全部',
            placeholder: '输入工序名搜索…（INV-9）',
            width: '200px',
          })]),
          h('div', { class: 'field' }, [h('label', {}, '日期'), h('input', { class: 'inp', value: '2026-10-16 ~ 2026-10-18' })]),
          h('button', { class: 'btn primary' }, '查询'),
        ]),
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '打菲号'), h('th', {}, '款号'), h('th', {}, '工序'), h('th', {}, '员工'),
            h('th', {}, '组别'), h('th', { class: 'right' }, '件数'), h('th', { class: 'right' }, '单价快照'),
            h('th', { class: 'right' }, '金额'), h('th', {}, '类型'), h('th', {}, '日期'), h('th', {}, '结算'), h('th', { style: 'width:150px' }, '操作'),
          ])),
          h('tbody', {}, pieceworkLogs.map((l) =>
            h('tr', {}, [
              h('td', { class: 'mono nowrap' }, l.bundleNo),
              h('td', { class: 'mono nowrap' }, l.styleNo),
              h('td', { class: 'nowrap' }, l.op),
              h('td', {}, [h('div', {}, l.employee), h('div', { class: 'muted mono' }, l.no)]),
              h('td', { class: 'nowrap' }, l.group),
              h('td', { class: 'right amount', style: l.qty < 0 ? 'color:var(--color-danger)' : '' }, String(l.qty)),
              h('td', { class: 'right amount' }, l.price),
              h('td', { class: 'right amount', style: `font-weight:600;${l.amount.startsWith('-') ? 'color:var(--color-danger)' : ''}` }, money(l.amount)),
              h('td', {}, h('span', {
                class: 'status-tag ' + (l.type === '红冲' ? 'st-rejected' : l.type === '补录' ? 'st-submitted' : 'st-draft'),
              }, [h('span', { class: 'dot' }), l.type])),
              h('td', { class: 'nowrap' }, l.date),
              h('td', {}, l.settled
                ? h('span', { class: 'status-tag st-approved' }, [h('span', { class: 'dot' }), '已结算'])
                : h('span', { class: 'status-tag st-pending' }, [h('span', { class: 'dot' }), '未结算'])),
              h('td', { class: 'row-actions' }, [
                h('a', { class: l.settled ? 'disabled' : '', onClick: () => toast.err('已结算流水不可红冲，请先工资反审核（32003）') }, '红冲'),
                h('a', {}, '追溯'),
              ]),
            ]),
          )),
        ])),
        h(Pager, { total: 6, page: 1 }),
      ]), true),
      card('幂等与并发保证', h('div', { class: 'desc-grid' }, [
        ['幂等键', h('span', { class: 'mono' }, 'UNIQUE(bundle_no, operation_no, employee_id, work_date, log_type)')],
        ['重复扫码', '命中唯一键 → 返回首次记录 + HTTP 200，不报错、不重复计数'],
        ['同码他人已计', h('span', {}, ['返回 ', h('code', {}, '32004'), '，提示已由他人完成'])] ,
        ['并发', '20 并发同码只落 1 条（事务内 SELECT ... FOR UPDATE 锁码行 + 唯一索引兜底）'],
        ['整件口径', h('span', {}, ['qty 只允许正整数（', h('code', {}, 'ck_piecework_logs_whole'), '），尾数不计件 ', h('code', {}, '32005')])],
        ['离线重传', '工位机与员工端本地队列，指数退避重试，带同一幂等键，服务端幂等返回'],
      ], 3)),
    ]
  },
})

/* ===================== 工资结算 ===================== */
export const PayrollPage = defineComponent({
  name: 'PayrollPage',
  setup() {
    const toast = useToast()
    const periodModal = ref(false)
    const genMode = ref<'month' | 'bimonth' | 'boundary'>('month')
    const startInput = ref('2026-10-01')
    const result = ref<{ count: number; amount: string; range: string } | null>(null)
    const closeTarget = ref('')

    function generate() {
      const r = genMode.value === 'month' ? '2026-11-01 ~ 2026-11-30'
        : genMode.value === 'bimonth' ? '2026-11-01 ~ 2026-12-31'
        : '2026-10-16 ~ 2026-10-31'
      result.value = { range: r, count: 3240, amount: '44,518.00' }
    }

    const totalQty = payrollRows.reduce((s, r) => s + r.qty, 0)
    const totalAmt = payrollRows.reduce((s, r) => s + Number(r.amount.replace(/[,]/g, '')), 0)

    return () => [
      pageHead('工资结算', 'ADR-0010：结算周期是用户自建的起止日期区间（整月 / 两月一次 / 按裁剪打菲完成日截取）', [
        h('button', { class: 'btn' }, '导出工资表'),
        h('button', { class: 'btn' }, '导出银行代发文件'),
        h('button', { class: 'btn primary', onClick: () => (periodModal.value = true) }, '+ 新建结算周期'),
      ]),

      card('结算周期（标签随便写，唯一性由区间保证）', h('div', {}, [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '周期标签'), h('th', {}, '起始日'), h('th', {}, '截止日'), h('th', {}, '来源'),
            h('th', { class: 'right' }, '区间内未结算流水'), h('th', { class: 'right' }, '金额'),
            h('th', {}, '状态'), h('th', { style: 'width:210px' }, '操作'),
          ])),
          h('tbody', {}, periods.map((p) =>
            h('tr', {}, [
              h('td', { class: 'mono nowrap' }, p.code),
              h('td', { class: 'nowrap' }, p.from),
              h('td', { class: 'nowrap' }, p.to),
              h('td', {}, h('span', { class: 'status-tag st-draft' }, [h('span', { class: 'dot' }), p.source])),
              h('td', { class: 'right tabular' }, qtyFmt(p.cnt)),
              h('td', { class: 'right amount' }, money(p.amount)),
              h('td', {}, h('span', { class: 'status-tag ' + (p.status === 'OPEN' ? 'st-submitted' : 'st-cancelled') }, [h('span', { class: 'dot' }), p.status === 'OPEN' ? '进行中' : '已关闭'])),
              h('td', { class: 'row-actions' }, [
                p.status === 'OPEN' ? h('a', { onClick: () => toast.ok(`已生成结算单草稿（演示）：区间 ${p.from} ~ ${p.to}`) }, '生成结算单') : h('a', { class: 'disabled' }, '生成结算单'),
                p.status === 'OPEN' ? h('a', { class: 'danger', onClick: () => (closeTarget.value = p.code) }, '关闭周期') : h('a', { onClick: () => toast.err('重开周期需 payroll:period:manage + 必填原因（演示）') }, '重开'),
                h('a', {}, '取数预演'),
              ]),
            ]),
          )),
        ])),
        h('div', { class: 'alert info', style: 'margin:12px 16px 16px' }, [
          '取数口径：', h('code', {}, 'work_date BETWEEN :start AND :end AND workshop_id = :ws AND payroll_id IS NULL'),
          ' → 周期内未结算流水为 0 才算结算干净。',
        ]),
      ]), true),

      card('结算单 PW-202610-001 · 缝制一组 · 2026-10-01 ~ 2026-10-15', h('div', {}, [
        h('div', { style: 'display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap' }, [
          h(StatusTag, { status: 'SUBMITTED' }),
          h('span', { class: 'muted' }, '制单人 计件员 张三 ｜ 提交 2026-10-16 ｜ 待车间主管审核'),
        ]),
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '工号'), h('th', {}, '姓名'), h('th', {}, '组别'),
            h('th', { class: 'right' }, '件数'), h('th', { class: 'right' }, '金额'), h('th', {}, '工序明细'),
          ])),
          h('tbody', {}, payrollRows.map((r) =>
            h('tr', {}, [
              h('td', { class: 'mono' }, r.no),
              h('td', {}, r.name),
              h('td', { class: 'nowrap' }, r.group),
              h('td', { class: 'right amount' }, qtyFmt(r.qty)),
              h('td', { class: 'right amount', style: 'font-weight:600' }, money(r.amount)),
              h('td', { class: 'muted' }, r.detail),
            ]),
          )),
          h('tfoot', {}, h('tr', {}, [
            h('td', { colspan: 3, class: 'right' }, '本页合计'),
            h('td', { class: 'right amount' }, qtyFmt(totalQty)),
            h('td', { class: 'right amount', style: 'font-weight:700' }, money(totalAmt)),
            h('td', {}),
          ])),
        ])),
        h('div', { class: 'sticky-actions' }, [
          h('span', { class: 'muted', style: 'margin-right:auto' }, '审批链：车间主管审核 → 财务审核 → 发放（生成付款凭证 + 代发文件）'),
          h('button', { class: 'btn', onClick: () => toast.err('不能审核自己创建的单据（10005）') }, '审核'),
          h('button', { class: 'btn danger', onClick: () => toast.err('已发放工资不可反审核，只能红冲（33001）') }, '反审核'),
        ]),
      ]), true),

      periodModal.value
        ? h(Modal, { title: '新建工资结算周期', wide: true }, {
            body: h('div', {}, [
              h('div', { class: 'alert info' }, ['周期是「起始日 ~ 截止日」区间；标签随便写（如"夏季款""7-8月"），唯一性由 ', h('code', {}, 'UNIQUE(start_date, end_date)'), ' 保证。']),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '创建方式'),
                h('div', { class: 'grow' }, h('div', { style: 'display:flex;gap:20px;flex-wrap:wrap' }, [
                  h('label', {}, h('input', { type: 'radio', checked: genMode.value === 'month', onChange: () => (genMode.value = 'month') }), ' 生成整月'),
                  h('label', {}, h('input', { type: 'radio', checked: genMode.value === 'bimonth', onChange: () => (genMode.value = 'bimonth') }), ' 两月一次'),
                  h('label', {}, h('input', { type: 'radio', checked: genMode.value === 'boundary', onChange: () => (genMode.value = 'boundary') }), ' 按裁剪打菲完成日截取'),
                ])),
              ]),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, genMode.value === 'boundary' ? '款号' : '起始月'),
                h('div', { class: 'grow' }, genMode.value === 'boundary'
                  ? h(Combo, {
                      options: styles.map((st) => ({ value: st.styleNo, label: st.styleNo, sub: st.name })),
                      modelValue: 'HB-2026-0018',
                      placeholder: '输入款号或款名搜索…（INV-9）',
                      width: '100%',
                    })
                  : h('input', { class: 'inp', style: 'max-width:200px', type: 'month', value: startInput.value, onInput: (e: Event) => (startInput.value = (e.target as HTMLInputElement).value) })),
              ]),
              h('div', { class: 'form-row' }, [h('label', {}, '周期标签'), h('div', { class: 'grow' }, h('input', { class: 'inp', style: 'width:100%', placeholder: '留空则自动填，如 2026-11 或 裁剪 20260901-20260930' }))]),
              h('div', { class: 'form-row' }, [
                h('label', {}, '取数预演'),
                h('div', { class: 'grow' },
                  h('button', { class: 'btn', onClick: generate }, '按此区间试算区间内未结算流水'),
                  result.value ? h('div', { class: 'alert ok', style: 'margin-top:10px' }, [
                    '区间 ', h('b', {}, result.value.range), ' 内未结算流水 ',
                    h('b', {}, qtyFmt(result.value.count)), ' 件，金额 ¥', h('b', {}, money(result.value.amount)),
                    '。请核对边界是否漏算（例如漏了月末最后 3 天）。',
                  ]) : null),
              ]),
            ]),
            footer: [
              h('button', { class: 'btn' }, '取消'),
              h('button', { class: 'btn primary', onClick: () => { toast.ok('周期已创建（演示）'); periodModal.value = false; result.value = null } }, '创建周期'),
            ],
          })
        : null,

      closeTarget.value
        ? h(Modal, { title: '关闭结算周期' }, {
            body: h('div', {}, [
              h('div', { class: 'alert warn' }, ['关闭后不允许再往该周期加结算单（', h('code', {}, '33003'), '）。重开需 ', h('code', {}, 'payroll:period:manage'), ' + 必填原因。']),
              h('div', { class: 'form-row' }, [h('label', {}, '周期'), h('div', { class: 'grow' }, h('input', { class: 'inp', style: 'width:100%', value: closeTarget.value, disabled: true }))]),
              h('div', { class: 'form-row' }, [h('label', { class: 'required' }, '关闭原因'), h('div', { class: 'grow' }, h('textarea', { class: 'inp', rows: 3, placeholder: '例如：本期已全部结算完毕' }))]),
            ]),
            footer: [h('button', { class: 'btn' }, '取消'), h('button', { class: 'btn danger', onClick: () => { toast.ok('周期已关闭（演示）'); closeTarget.value = '' } }, '确认关闭')],
          })
        : null,

      h(ToastHost, { items: toast.items }),
    ]
  },
})