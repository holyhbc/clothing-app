import { h } from '../../components/h'
import { Combo } from '../../components/Combo'
import { defineComponent, ref } from 'vue'

import { StatusTag, Pager, QrBox, Modal, useToast, ToastHost } from '../../components/ui'
import { pageHead, card } from '../shared'
import { bundlingOrders, bundleSamples, bundleHands, styles } from '../../mock'

export default defineComponent({
  name: 'BundlingPage',
  setup() {
    const toast = useToast()
    const printOpen = ref(false)
    const copyOpen = ref(false)
    const printQty = ref(1)
    const copySrc = ref('HB-2026-0018')
    const copyMode = ref('keep')

    return () => [
      pageHead('打菲', '一码 = 一手 = 一个员工（ADR-0016/0023：原「打菲按手」页已并入本页）；「每码件数」= 这一扎装几件，「码数」= 全单打多少个码。二维码内容 = bundle_no 纯文本，Code128 供扫码枪', [
        h('button', { class: 'btn' }, '导出'),
        h('button', { class: 'btn', onClick: () => (copyOpen.value = true) }, '模板复制'),
        h('button', { class: 'btn primary' }, '+ 新建打菲单'),
      ]),

      card('列表', h('div', {}, [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '单据号'), h('th', {}, '款号 / 工序'), h('th', {}, '车间 / 组别'),
            h('th', { class: 'right' }, '每码件数'), h('th', { class: 'right' }, '码数（手）'),
            h('th', { class: 'right' }, '已打印'), h('th', { class: 'right' }, '已计件'),
            h('th', {}, '状态'), h('th', {}, '制单'), h('th', {}, '时间'), h('th', { style: 'width:200px' }, '操作'),
          ])),
          h('tbody', {}, bundlingOrders.map((o) => {
            const countedRate = o.bundleCount ? Math.round((o.counted / o.bundleCount) * 100) : 0
            return h('tr', {}, [
              h('td', { class: 'mono nowrap' }, o.docNo),
              h('td', {}, [h('div', { class: 'mono' }, o.styleNo), h('div', { class: 'muted' }, o.operation)]),
              h('td', { class: 'nowrap' }, o.workshop),
              h('td', { class: 'right amount' }, `${o.bundleQty} 件/码`),
              h('td', { class: 'right amount' }, [
                h('div', { style: 'font-weight:600' }, String(o.bundleCount)),
                h('div', { class: 'muted', style: 'font-weight:400' }, `手 / 共 ${(o.bundleQty * o.bundleCount).toLocaleString('zh-CN')} 件`),
              ]),
              h('td', { class: 'right tabular' }, o.printed),
              h('td', {}, [
                h('div', { class: 'right tabular' }, `${o.counted}`),
                h('div', { class: ['progress', countedRate >= 90 ? 'ok' : ''], style: 'margin-top:4px;height:5px' }, [h('i', { style: `width:${countedRate}%` })]),
              ]),
              h('td', {}, h(StatusTag, { status: o.status })),
              h('td', { class: 'nowrap' }, o.user),
              h('td', { class: 'muted nowrap' }, o.time),
              h('td', { class: 'row-actions' }, [
                h('a', { onClick: () => { printOpen.value = true; printQty.value = 1 } }, '打印标签'),
                o.status === 'SUBMITTED' ? h('a', { onClick: () => toast.ok('已审核（演示）：生成 ' + o.bundleCount + ' 个唯一码') }, '审核') : null,
                h('a', { class: o.status === 'APPROVED' ? '' : 'disabled', onClick: () => toast.ok('已作废所选码（演示），已计件的码不允许作废（32003）') }, '作废码'),
                h('a', { class: 'disabled' }, '变更历史'),
              ]),
            ])
          })),
        ])),
        h(Pager, { total: bundlingOrders.length, page: 1 }),
      ]), true),

      card('按手列表（一码 = 一手 = 一个员工；原「打菲按手」页已并入本页）', h('div', {}, [
        h('div', { class: 'chain', style: 'margin-bottom:10px' }, [
          h('span', { class: 'node mono' }, 'HB-2026-0018（单衣）'), h('span', { class: 'arrow' }, '→'),
          h('a', { class: 'node mono', onClick: () => (window.location.hash = '/pc/cutting') }, 'CT-20261018-000012'), h('span', { class: 'arrow' }, '→'),
          h('span', { class: 'node mono cur' }, 'BD-20261018-000031'), h('span', { class: 'arrow' }, '→'),
          h('span', { class: 'node' }, '打边'),
        ]),
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '尺码'), h('th', { class: 'right' }, '手数'), h('th', { class: 'right' }, '件数'),
            h('th', {}, '手号 / bundle_no'), h('th', { class: 'right' }, '每码件数'), h('th', {}, '标签'),
            h('th', {}, '打印'), h('th', {}, '计件'), h('th', { style: 'width:150px' }, '操作'),
          ])),
          h('tbody', {}, bundleHands.flatMap((g) =>
            g.codes.map((c, i) =>
              h('tr', {}, [
                i === 0 ? h('td', { class: 'nowrap', rowspan: g.codes.length }, [
                  h('div', { style: 'font-weight:600' }, g.size.split('(')[0]),
                  h('div', { class: 'muted' }, g.size.split('(')[1] ? g.size.split('(')[1].replace(')', '') : ''),
                ]) : null,
                i === 0 ? h('td', { class: 'right amount', rowspan: g.codes.length }, String(g.hands)) : null,
                i === 0 ? h('td', { class: 'right amount', rowspan: g.codes.length }, String(g.cuttingQty)) : null,
                h('td', { class: 'nowrap' }, [
                  h('div', {}, '第 ' + c.no + ' 手 / 共 ' + g.hands + ' 手'),
                  h('div', { class: 'mono muted' }, c.bundleNo),
                ]),
                h('td', { class: 'right amount' }, c.bundleQty + ' 件'),
                h('td', {}, h('span', { class: 'muted' }, '40×40 标签')),
                h('td', {}, h('span', { class: 'stage-pill ' + (c.time === '—' ? '' : 'done') }, c.time === '—' ? '未打印' : '已打印')),
                h('td', {}, c.state === '已计'
                  ? h('div', {}, [h('div', {}, c.employee), h('div', { class: 'muted' }, c.time)])
                  : h('span', { class: 'stage-pill wait' }, '未计件')),
                h('td', { class: 'row-actions' }, [
                  h('a', { onClick: () => (printOpen.value = true) }, '打印'),
                  c.state === '已计'
                    ? h('a', { class: 'danger', onClick: () => toast.err('32003 计件已结算的码不可作废，需先红冲反计件') }, '作废')
                    : h('a', { class: 'danger', onClick: () => toast.ok('已作废该码（演示）') }, '作废'),
                ]),
              ]),
            ),
          )),
        ])),
        h('div', { class: 'note-strip', style: 'margin-top:8px' }, [
          '未计件清单：共 3 个码未扫码（超期提醒，支持导出）。整件口径：尾数不出码（31003）；码数必须等于裁剪手数（31004）。',
        ]),
      ]), true),

      card('一衣一码规则（09 §2.4）', h('div', { class: 'desc-grid' }, [
        ['二维码内容', h('span', { class: 'mono' }, 'bundle_no 纯文本，纠错等级 M，尺寸 ≥20mm')],
        ['条码码制', h('span', { class: 'mono' }, 'Code 128，内容与二维码一致，供扫码枪')],
        ['bundle_no 格式', h('span', { class: 'mono' }, 'BD-20261018-000031-XL02-0001（含尺码码 + 手序号，ADR-0016）')],
        ['一码 = 一手', '一码一手一员工；每码件数 = 裁剪明细 qty_per_hand；扫码默认整手，可手写更小件数，超产拒绝（32006）'],
        ['改单联动', '上游裁剪改手数：未打印码重建、已打印码作废重打（31007）、已计件禁止（30004 需先红冲）'],
        ['生命周期', '生成即固化；返工/换色/拼件必须新建码，旧码作废写 voided_at'],
        ['批量生成', '2000 件一次生成：批量 INSERT（禁逐条），同事务写 cutting_outputs'],
        ['整件口径', 'bundle_count 必须为整数；尾数不生成码（31003）'],
      ], 3)),

      printOpen.value
        ? h(Modal, { title: '打印打菲标签 · BD-20261018-000031', wide: true }, {
            body: h('div', {}, [
              h('div', { class: 'alert info' }, ['标签规格 40mm × 40mm 不干胶，预留打印偏移 2mm。@page size 40mm 40mm，打印时隐藏所有交互元素。']),
              h('div', { class: 'form-row' }, [
                h('label', {}, '打印张数'),
                h('div', { class: 'grow' }, [
                  h('input', { class: 'inp', style: 'max-width:140px', type: 'number', value: String(printQty.value), onInput: (e: Event) => (printQty.value = Number((e.target as HTMLInputElement).value)) }),
                  h('div', { class: 'form-hint' }, '重打必须记录「重打第几件」，便于与实物核对，写入操作日志。'),
                ]),
              ]),
              h('div', { class: 'qr-grid' }, bundleSamples.map((b) =>
                h('div', { class: 'qr-label' }, [
                  h(QrBox, { text: b.bundleNo, size: 118 }),
                  h('div', { class: 't1' }, b.bundleNo),
                  h('div', { class: 't2' }, `${b.styleNo} · ${b.color} · ${b.size}`),
                  h('div', { class: 't3' }, b.op),
                ]),
              )),
              h('div', { class: 'form-hint', style: 'margin-top:10px' }, '实际打印会包含 Code128 条码图形，此处用二维码占位示意。'),
            ]),
            footer: [
              h('button', { class: 'btn', onClick: () => (printOpen.value = false) }, '取消'),
              h('button', { class: 'btn' }, '导出 CSV 给标签软件'),
              h('button', { class: 'btn primary', onClick: () => { toast.ok(`已发送打印 ${bundleSamples.length * printQty.value} 张（演示）`); printOpen.value = false } }, '开始打印'),
            ],
          })
        : null,

      copyOpen.value
        ? h(Modal, { title: '款号工序模板复制（ADR-0009）', wide: true }, {
            body: h('div', {}, [
              h('div', { class: 'alert info' }, ['把源款号的工序配置（顺序 / 一扎几件 / 是否计件）与单价历史整套复制到目标款号。目标款号已存在同工序时按下方策略处理。']),
              h('div', { class: 'form-row' }, [h('label', { class: 'required' }, '源款号'), h('div', { class: 'grow' },
                h(Combo, {
                  options: styles.map((st) => ({ value: st.styleNo, label: st.styleNo, sub: `${st.name}（${st.customer}）` })),
                  modelValue: copySrc.value,
                  placeholder: '输入源款号或款名搜索…（INV-9）',
                  width: '100%',
                  onChange: (v: string) => (copySrc.value = v),
                }))]),
              h('div', { class: 'form-row' }, [h('label', { class: 'required' }, '目标款号'), h('div', { class: 'grow' },
                h('input', { class: 'inp', style: 'width:100%', value: 'HB-2026-0033' }))]),
              h('div', { class: 'form-row' }, [h('label', { class: 'required' }, '复制模式'), h('div', { class: 'grow' }, [
                h('div', { style: 'display:flex;gap:20px;flex-wrap:wrap' }, [
                  h('label', {}, h('input', { type: 'radio', checked: copyMode.value === 'keep', onChange: () => (copyMode.value = 'keep') }), ' 沿用源价'),
                  h('label', {}, h('input', { type: 'radio', checked: copyMode.value === 'ratio', onChange: () => (copyMode.value = 'ratio') }), ' 沿用源价并按比例调整'),
                  h('label', {}, h('input', { type: 'radio', checked: copyMode.value === 'struct', onChange: () => (copyMode.value = 'struct') }), ' 只复制工序结构（价格到目标款号另设）'),
                ]),
                copyMode.value === 'ratio' ? h('input', { class: 'inp', style: 'margin-top:8px;max-width:160px', value: '1.0800' }) : null,
              ])]),
              h('div', { class: 'form-row' }, [h('label', {}, '冲突策略'), h('div', { class: 'grow' },
                h('select', { class: 'sel', style: 'width:100%' }, ['跳过已存在', '覆盖目标配置', '整单失败并报错'].map((x) => h('option', {}, x))))]),
              h('div', { class: 'alert warn' }, ['权限点：', h('code', {}, 'base:rate_template:manage'), '。复制过程与目标款号工序保存同事务，失败整体回滚。']),
            ]),
            footer: [
              h('button', { class: 'btn', onClick: () => (copyOpen.value = false) }, '取消'),
              h('button', { class: 'btn primary', onClick: () => { toast.ok(`已从 ${copySrc.value} 复制 7 个工序 + 7 条单价到 HB-2026-0033（演示）`); copyOpen.value = false } }, '确认复制'),
            ],
          })
        : null,

      h(ToastHost, { items: toast.items }),
    ]
  },
})