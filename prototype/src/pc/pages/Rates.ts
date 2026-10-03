import { h } from '../../components/h'
import { Combo } from '../../components/Combo'
import { defineComponent, ref } from 'vue'

import { Modal, useToast, ToastHost } from '../../components/ui'
import { pageHead, card, descGrid } from '../shared'
import { operations, rateHistory, styleRateRows, styles, money } from '../../mock'

export default defineComponent({
  name: 'RatesPage',
  setup() {
    const toast = useToast()
    const adjustOp = ref('')
    const adjustPrice = ref('')
    const adjustFrom = ref('2026-10-25')
    const adjustReason = ref('')
    const errs = ref<Record<string, string>>({})
    const opModal = ref(false)
    const newOp = ref({ no: '', name: '', bundleQty: '1', piecework: true })

    function submitAdjust() {
      const e: Record<string, string> = {}
      if (!adjustOp.value) e.op = '请选择工序'
      if (!adjustPrice.value || Number(adjustPrice.value) < 0) e.price = '单价必须 ≥ 0'
      if (!adjustFrom.value) e.from = '请选择生效日期'
      if (!adjustReason.value.trim()) e.reason = '调价原因必填（10006）'
      errs.value = e
      if (Object.keys(e).length) return
      toast.ok(`已调价：${adjustOp.value} 自 ${adjustFrom.value} 起执行；历史流水单价快照不受影响（演示）`)
      adjustOp.value = ''; adjustPrice.value = ''; adjustReason.value = ''
    }

    return () => [
      pageHead('工序与计件单价', 'ADR-0009：单价按「款号 × 工序 × 生效区间」定价；调价 = 关旧区间 + 插新区间，禁止 UPDATE 价格', [
        h('button', { class: 'btn', onClick: () => (opModal.value = true) }, '+ 新增工序'),
        h('button', { class: 'btn', onClick: () => toast.ok('已导出工序与单价表（演示）') }, '导出'),
      ]),

      card('款号工序单价 · HB-2026-0018（男式商务长袖衬衫）', h('div', {}, [
        h('div', { class: 'filters', style: 'margin-bottom:12px' }, [
          h('div', { class: 'field' }, [h('label', {}, '款号'), h(Combo, {
            options: styles.map((st) => ({ value: st.styleNo, label: st.styleNo, sub: `${st.name}（${st.customer}）` })),
            modelValue: 'HB-2026-0018',
            placeholder: '输入款号或款名搜索…（INV-9）',
            width: '260px',
            onChange: () => toast.ok('已切换款号（演示）：单价表按款号过滤'),
          })]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '当前合计工价'), h('span', { class: 'v amount', style: 'color:var(--color-primary)' }, '¥4.80 / 件')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '工序数'), h('span', { class: 'v' }, '7')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '最后一道工序'), h('span', { class: 'v' }, '整烫（烫衣服）')]),
        ]),
        h('div', { class: 'alert info', style: 'margin-bottom:10px' }, ['ⓘ ', '★ 最后一道工序 = ', h('b', {}, '整烫（烫衣服）'), '（ADR-0018）：该工序扫码完成即自动成衣入库。默认按工序名含「整烫 / 烫衣」自动识别；若某款识别不到，保存时标红要求人工指定，不允许留空。']),
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '工序号'), h('th', {}, '工序名'), h('th', { class: 'right' }, '一扎几件'),
            h('th', { class: 'right' }, '当前单价(元/件)'), h('th', {}, '生效起'), h('th', {}, '生效止'),
            h('th', {}, '区间'), h('th', {}, '最后工序'), h('th', { style: 'width:140px' }, '操作'),
          ])),
          h('tbody', {}, styleRateRows.map((r) =>
            h('tr', {}, [
              h('td', { class: 'mono' }, r.op),
              h('td', {}, r.name),
              h('td', { class: 'right amount' }, `${r.bundleQty} 件`),
              h('td', { class: 'right amount', style: 'font-weight:600' }, r.price),
              h('td', { class: 'nowrap' }, r.from),
              h('td', { class: 'nowrap muted' }, r.to),
              h('td', {}, h('span', { class: 'status-tag st-approved' }, [h('span', { class: 'dot' }), '当前生效'])),
              h('td', {}, r.op === '07'
                ? h('span', { class: 'status-tag st-approved', style: 'font-weight:700' }, [h('span', { class: 'dot' }), '★ 整烫'])
                : h('span', { class: 'muted' }, '—')),
              h('td', { class: 'row-actions' }, [
                h('a', { onClick: () => { adjustOp.value = r.name; adjustPrice.value = r.price; adjustFrom.value = '2026-10-25' } }, '调价'),
                h('a', {}, '价格历史'),
              ]),
            ]),
          )),
        ])),
      ]), true),

      card('价格历史 · 打边（01）· 演示"同一工序随时间调价"', h('div', { class: 'rate-table' }, [
        h('div', { class: 'rate-row head' }, [
          h('div', {}, '生效区间'), h('div', {}, '状态'), h('div', {}, '说明'), h('div', { class: 'right' }, '单价(元/件)'), h('div', {}, ''),
        ]),
        ...rateHistory.map((r) =>
          h('div', { class: ['rate-row', r.cls] }, [
            h('div', {}, h('span', { class: 'rate-tagline mono' }, r.range)),
            h('div', {}, h('span', {
              class: 'status-tag ' + (r.cls === 'current' ? 'st-approved' : r.cls === 'future' ? 'st-submitted' : 'st-draft'),
            }, [h('span', { class: 'dot' }), r.tag])),
            h('div', { class: 'muted' },
              r.cls === 'past' ? '已结束，历史流水用此价（快照）'
              : r.cls === 'current' ? '今天及之后计件用此价'
              : '提前公告，2026-11-01 起生效'),
            h('div', { class: 'right amount', style: 'font-weight:600' }, r.price),
            h('div', { class: 'muted' }, r.cls === 'future' ? h('a', { style: 'color:var(--color-primary);cursor:pointer' }, '撤销') : ''),
          ]),
        ),
        h('div', { class: 'alert info', style: 'margin-top:14px' }, [
          '调价规则：',
          h('b', {}, 'effective_from ≤ 计件日期 < effective_to'),
          '，命中不到 → 错误码 ',
          h('code', {}, '20004'),
          '。生效区间不允许重叠（',
          h('code', {}, '20005'),
          '）。已落库的计件流水单价快照永不变（INV-3）。',
        ]),
      ])),

      card('工序主数据（用户自定义增删改，不内置枚举）', h('div', {}, [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [h('th', {}, '工序号'), h('th', {}, '工序名'), h('th', { class: 'right' }, '默认一扎'), h('th', {}, '是否计件'), h('th', {}, '最后工序'), h('th', {}, '状态'), h('th', {}, '被引用'), h('th', { style: 'width:150px' }, '操作')])),
          h('tbody', {}, operations.map((o) =>
            h('tr', {}, [
              h('td', { class: 'mono' }, o.op),
              h('td', {}, o.name),
              h('td', { class: 'right amount' }, `${o.bundleQty} 件`),
              h('td', {}, o.piecework ? h('span', { class: 'status-tag st-approved' }, [h('span', { class: 'dot' }), '计件']) : h('span', { class: 'status-tag st-draft' }, [h('span', { class: 'dot' }), '不计件'])),
              h('td', {}, (o as { isFinal?: boolean }).isFinal
                ? h('span', { class: 'status-tag st-approved', style: 'font-weight:700' }, [h('span', { class: 'dot' }), '★ 最后工序'])
                : h('span', { class: 'muted' }, '—')),
              h('td', {}, o.active ? h('span', { class: 'status-tag st-approved' }, [h('span', { class: 'dot' }), '启用']) : h('span', { class: 'status-tag st-cancelled' }, [h('span', { class: 'dot' }), '停用'])),
              h('td', { class: 'muted nowrap' }, o.active ? `${o.op === '06' ? 3 : 12} 款` : '—'),
              h('td', { class: 'row-actions' }, [
                h('a', {}, '编辑'),
                h('a', { onClick: () => toast.ok(o.active ? '已停用（演示）：停用后不参与新建单据，历史可查' : '已启用（演示）') }, o.active ? '停用' : '启用'),
                h('a', { class: o.active ? 'disabled' : '', onClick: () => toast.err('20003 已被引用，只能停用不能删除') }, '删除'),
              ]),
            ]),
          )),
        ])),
      ]), true),

      card('款号工序配置（模板载体）· HB-2026-0018', descGrid([
        ['工序顺序', '01 打边 → 02 拼前 → 03 装袖 → 04 合缝 → 05 锁眼钉扣 → 06 查勘 → 07 整烫包装'],
        ['一扎几件', '打边/拼前/装袖/合缝/锁眼 = 1 件；查勘/整烫 = 12 件（一打）'],
        ['员工绑定', h('span', {}, ['扫打菲二维码 → 带出款号 → 从本页工序列表选择 → 绑定（当天有效）'])],
        ['模板复制', h('a', { style: 'color:var(--color-primary);cursor:pointer' }, '复制到另一个款号 ›')],
      ], 2)),

      adjustOp.value
        ? h(Modal, { title: '调整工序单价' }, {
            body: h('div', {}, [
              h('div', { class: 'alert warn' }, ['调价不修改历史价：旧行只改 effective_to，新行写新价；同事务写 document_logs（changed_fields 记新旧价）。']),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '工序'),
                h('div', { class: 'grow' }, [
                  h('select', { class: 'sel', style: 'width:100%', onChange: (e: Event) => { adjustOp.value = (e.target as HTMLSelectElement).value; delete errs.value.op } },
                    ['', ...styleRateRows.map((r) => r.name)].map((x) => h('option', { value: x }, x || '请选择'))),
                  errs.value.op ? h('div', { class: 'form-err' }, errs.value.op) : null,
                ]),
              ]),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '新单价'),
                h('div', { class: 'grow' }, [
                  h('input', { class: 'inp', style: 'max-width:200px', placeholder: '元/件，精度 6', value: adjustPrice.value, onInput: (e: Event) => { adjustPrice.value = (e.target as HTMLInputElement).value; delete errs.value.price } }),
                  h('div', { class: 'form-hint' }, '例：0.450000。允许 0（免费工序），但需填原因。'),
                  errs.value.price ? h('div', { class: 'form-err' }, errs.value.price) : null,
                ]),
              ]),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '生效起'),
                h('div', { class: 'grow' }, [
                  h('input', { class: 'inp', type: 'date', style: 'max-width:200px', value: adjustFrom.value, onInput: (e: Event) => { adjustFrom.value = (e.target as HTMLInputElement).value; delete errs.value.from } }),
                  h('div', { class: 'form-hint' }, '可以是未来日期（提前公告）。今天及之前的已计件流水不受影响。'),
                  errs.value.from ? h('div', { class: 'form-err' }, errs.value.from) : null,
                ]),
              ]),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '调价原因'),
                h('div', { class: 'grow' }, [
                  h('textarea', { class: 'inp', rows: 3, placeholder: '例如：厂里统一调工价，涨幅 9.5%', value: adjustReason.value, onInput: (e: Event) => { adjustReason.value = (e.target as HTMLTextAreaElement).value; delete errs.value.reason } }),
                  errs.value.reason ? h('div', { class: 'form-err' }, errs.value.reason) : null,
                ]),
              ]),
            ]),
            footer: [
              h('button', { class: 'btn' }, '取消'),
              h('button', { class: 'btn primary', onClick: submitAdjust }, '确认调价'),
            ],
          })
        : null,

      opModal.value
        ? h(Modal, { title: '新增工序' }, {
            body: h('div', {}, [
              h('div', { class: 'form-row' }, [h('label', { class: 'required' }, '工序号'), h('div', { class: 'grow' }, h('input', { class: 'inp', style: 'max-width:200px', placeholder: '如 04 / 或自定义 DB08', onInput: (e: Event) => (newOp.value.no = (e.target as HTMLInputElement).value) }))]),
              h('div', { class: 'form-row' }, [h('label', { class: 'required' }, '工序名'), h('div', { class: 'grow' }, h('input', { class: 'inp', style: 'max-width:240px', placeholder: '如 合缝 / 后 / 锁眼', onInput: (e: Event) => (newOp.value.name = (e.target as HTMLInputElement).value) }))]),
              h('div', { class: 'form-row' }, [h('label', {}, '默认一扎'), h('div', { class: 'grow' }, h('input', { class: 'inp', style: 'max-width:120px', type: 'number', value: newOp.value.bundleQty, onInput: (e: Event) => (newOp.value.bundleQty = (e.target as HTMLInputElement).value) }))]),
              h('div', { class: 'form-row' }, [h('label', {}, '是否计件'), h('div', { class: 'grow' }, h('label', {}, h('input', { type: 'checkbox', checked: newOp.value.piecework, onChange: (e: Event) => (newOp.value.piecework = (e.target as HTMLInputElement).checked) }), ' 计件（不勾选 = 辅助工序不计工钱）'))]),
              h('div', { class: 'alert info' }, ['一扎几件最终以款号工序配置为准（', h('code', {}, 'style_operations.bundle_qty'), '）；此处只是默认值。']),
            ]),
            footer: [h('button', { class: 'btn' }, '取消'), h('button', { class: 'btn primary', onClick: () => { toast.ok('工序已新增（演示）'); opModal.value = false } }, '保存')],
          })
        : null,

      h(ToastHost, { items: toast.items }),
    ]
  },
})