import { h } from '../../components/h'
import { defineComponent, ref, computed } from 'vue'

import { Modal, useToast, ToastHost } from '../../components/ui'
import { Combo } from '../../components/Combo'
import { pageHead, card, descGrid, field, select } from '../shared'
import { cutMatrix, styles, qtyFmt } from '../../mock'

/* ===================== 裁剪单（ADR-0023：唯一生产主单，含布批行 + 颜色尺码矩阵） ===================== */
export const CuttingMatrixPage = defineComponent({
  name: 'CuttingMatrixPage',
  setup() {
    const toast = useToast()
    const d = cutMatrix
    const mode = ref(d.mode)
    const perHand = ref<Record<string, number>>({ WHT: 60, BLK: 50 })
    const cells = ref<Record<string, Record<string, Record<string, number>>>>(
      JSON.parse(JSON.stringify(Object.fromEntries(d.batches.map((b) => [b.id, b.cells])))),
    )
    const sel = ref(d.batches[0].id)
    const pick = ref({ supplier: '苏州恒信纺织', fabric: '弹力斜纹', color: '本白', dyeLot: 'DY-2609-01', bolt: 'B-2610-001', width: '152.0', avail: '286.400', used: '96.0' })
    const chainOpen = ref(false)
    const styleNo = ref(d.styleNo)

    const batch = computed(() => d.batches.find((b) => b.id === sel.value) ?? d.batches[0])
    const rowCells = computed(() => cells.value[batch.value.id])

    function setHand(color: string, size: string, v: string) {
      const n = Number(v)
      if (!Number.isInteger(n) || n < 0) {
        toast.err('30006 手数必须为正整数（ADR-0020）')
        return
      }
      cells.value[batch.value.id][color][size] = n
    }

    /** 全部布批汇总（矩阵合计口径） */
    const statAll = (color: string) => {
      const hands = d.batches.reduce((s, b) => s + d.sizes.reduce((x, sz) => x + (cells.value[b.id][color][sz] ?? 0), 0), 0)
      const pieces = d.batches.reduce((s, b) => s + d.sizes.reduce((x, sz) => x + (cells.value[b.id][color][sz] ?? 0) * perHand.value[color], 0), 0)
      return { hands, pieces }
    }
    const totalHands = computed(() => d.colors.reduce((s, c) => s + statAll(c).hands, 0))
    const totalPieces = computed(() => d.colors.reduce((s, c) => s + statAll(c).pieces, 0))

    return () => [
      pageHead('裁剪单 · 布批 × 颜色 × 尺码矩阵', 'ADR-0020 手数直接输入（整数）｜ADR-0022 级联选料扣原料库存｜ADR-0023 一页装下布批行 + 明细矩阵（原「明细录入」「按布批」已并入本页）', [
        h('button', { class: 'btn', onClick: () => (chainOpen.value = true) }, '溯源链'),
        h('button', { class: 'btn' }, '导出'),
        h('button', { class: 'btn primary' }, '审核（扣料 + 裁片入 WIP）'),
      ]),

      h('div', { class: 'filters' }, [
        h('div', { class: 'field' }, [
          h('label', { class: 'required' }, '货号'),
          h(Combo, {
            options: styles.map((st) => ({ value: st.styleNo, label: st.styleNo, sub: `${st.name}（${st.customer}）` })),
            modelValue: styleNo.value,
            placeholder: '输入货号或款名搜索… 也可直接输入新货号',
            createText: '＋ 用「{kw}」新建款号',
            width: '100%',
            onChange: (v: string) => (styleNo.value = v),
            onCreate: (kw: string) => toast.err(`货号「${kw}」${styles.some((st) => st.styleNo === kw) ? '已存在（10001）' : '需先在商品管理建档'}，货号唯一不可重复`),
          }),
        ]),
        h('div', { class: 'note-strip', style: 'flex:1;min-width:260px' }, [
          '货号可手工输入，但 ', h('b', {}, 'UNIQUE 不可与已有冲突'),
          '（冲突返回 10001 并提示已有款号）；颜色没有的当场新增字典（支持多色）。',
        ]),
        h('div', { class: 'field' }, [
          h('label', {}, '商品分类'),
          h('input', { class: 'inp', value: '单衣（取自款号档案，不可改）', disabled: true, style: 'background:var(--color-fill-1);color:var(--color-text-second)' }),
        ]),
        h('div', { class: 'field' }, [
          h('label', {}, '颜色（多选）'),
          h(Combo, {
            options: [
              { value: 'WHT+BLK', label: '本白 + 黑', sub: '一床裁多色（ADR-0017）' },
              { value: 'WHT', label: '本白', sub: 'WHT' },
              { value: 'BLK', label: '黑', sub: 'BLK' },
              { value: 'NEW', label: '＋ 新增颜色…', sub: '现场建字典（未被引用可删）' },
            ],
            modelValue: 'WHT+BLK',
            placeholder: '输入颜色搜索 / 新增…',
          }),
        ]),
        select('车间', ['裁剪一组', '裁剪二组', '裁床组']),
        field('裁剪日期', d.docDate),
        h('button', { class: 'btn primary' }, '保存'),
      ]),

      card('表头', descGrid([
        ['货号 / 款名', h('span', { class: 'mono' }, d.styleNo + '　' + d.styleName)],
        ['商品分类', h('span', {}, [h('b', {}, '单衣'), h('span', { class: 'muted' }, '（取自款号档案，单据不可改；用于按分类算裁剪工资）')])],
        ['客户 / 客户款号', `${d.customer}　${d.customerStyleNo}`],
        ['颜色', h('span', {}, [d.colors.map((c) => (c === 'WHT' ? '本白' : '黑')).join(' + '), h('span', { class: 'muted' }, '（一床可裁多色）')])],
        ['车间 / 交期', `${d.workshop}　${d.delivery}`],
        ['每手件数（按颜色）', h('span', {}, d.colors.map((c) => (c === 'WHT' ? '本白 60' : '黑 50')).join('　'))],
      ]), true),

      card('布批行（级联选料：供应商 → 面料 → 颜色 → 缸号/匹号，只列可用库存）', h('div', {}, [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '供应商'), h('th', {}, '面料'), h('th', {}, '颜色'), h('th', {}, '缸号'), h('th', {}, '匹号'),
            h('th', { class: 'right' }, '门幅'), h('th', { class: 'right' }, '可用重量'), h('th', { class: 'right' }, '本次领用'),
            h('th', {}, '状态'), h('th', { style: 'width:120px' }, '操作'),
          ])),
          h('tbody', {}, d.batches.map((b) =>
            h('tr', { class: sel.value === b.id ? 'row-active' : '' }, [
              h('td', { class: 'nowrap' }, b.supplier),
              h('td', { class: 'nowrap' }, b.material),
              h('td', { class: 'nowrap' }, b.color),
              h('td', { class: 'mono nowrap' }, b.dyeLot),
              h('td', { class: 'mono nowrap' }, b.bolt),
              h('td', { class: 'right amount' }, b.width),
              h('td', { class: 'right amount' }, b.avail + ' kg'),
              h('td', { class: 'right amount', style: 'font-weight:600' }, b.used + ' kg'),
              h('td', {}, b.counted
                ? h('div', { class: 'row-state' }, [h('span', { class: 'tag lock' }, '已计件 🔒'), h('span', { class: 'tag print' }, '已打印')])
                : h('div', { class: 'row-state' }, [h('span', { class: 'tag' }, '未动')])),
              h('td', { class: 'row-actions' }, [
                h('a', { onClick: () => (sel.value = b.id) }, '选此行'),
                b.counted ? h('a', { class: 'danger', onClick: () => toast.err('30004 该行已有计件，需先红冲反计件才能修改（ADR-0021）') }, '改手数') : null,
                b.counted ? null : h('a', {}, '删除行'),
              ]),
            ]),
          )),
        ])),
        h('div', { class: 'note-strip', style: 'margin-top:8px' }, [
          '审核时按「缸号 + 匹号」逐行扣减原料库存并写 stock_ledger_lines；领用 > 可用 → ',
          h('b', {}, '40006'), '；同缸布可跨单行占用，累计不得超批次可用量。',
        ]),
      ]), true),

      card(null, h('div', {}, [
        h('div', { style: 'display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap' }, [
          h('span', { class: 'muted' }, '手数带出方式：'),
          h('div', { class: 'mode-switch' }, [
            ...(['MASTER', 'UNIFORM', 'MANUAL'] as const).map((m) =>
              h('button', { class: mode.value === m ? 'on' : '', onClick: () => {
                if (Object.values(cells.value).some((row) => Object.values(row).some((c) => Object.values(c).some((n) => n > 0)))) {
                  toast.ok('已切换带出方式（演示）：已有手数保留，需人工确认')
                }
                mode.value = m
              } }, m === 'MASTER' ? 'A 按比例带出' : m === 'UNIFORM' ? 'B 统一件数' : 'C 自定义'),
            ),
          ]),
          h('span', { class: 'muted' }, '比例只是建议；手数由你直接输入（整数），件数自动算'),
          h('span', { style: 'margin-left:auto' }, [h('b', { class: 'amount' }, `合计 ${totalHands.value} 手 / ${qtyFmt(totalPieces.value)} 件`)]),
        ]),

        h('div', { class: 'tbl-wrap' }, h('table', { class: 'matrix' }, [
          h('thead', {}, h('tr', {}, [
            h('th', { class: 'batch-col', rowspan: 2, style: 'min-width:210px' }, '布批（行）'),
            ...d.colors.flatMap((c) => [
              h('th', { class: 'grp', colspan: d.sizes.length }, c === 'WHT' ? '本白（每手 ' + perHand.value[c] + ' 件）' : '黑（每手 ' + perHand.value[c] + ' 件）'),
            ]),
            h('th', { class: 'grp', rowspan: 2 }, '本行合计'),
          ])),
          h('thead', {}, h('tr', {}, d.colors.flatMap((c) => d.sizes.map((s) => h('th', {}, s.split('(')[0]))))),
          h('tbody', {}, d.batches.map((b) => {
            const row = cells.value[b.id]
            const locked = b.counted
            return h('tr', {}, [
              h('th', { class: 'batch-col' }, [
                h('div', {}, [h('b', {}, b.dyeLot), '　', h('span', { class: 'muted' }, b.bolt)]),
                h('div', { class: 'muted' }, `领用 ${b.used} kg ｜ ${b.width} cm ｜ ${b.supplier}`),
              ]),
              ...d.colors.flatMap((c) =>
                d.sizes.map((s) => {
                  const v = (row as Record<string, Record<string, number>>)[c][s] ?? 0
                  const sug = (d.suggest as Record<string, Record<string, number>>)[c][s]
                  const dev = sug > 0 && v > 0 && Math.abs(v - sug) / sug > 0.2
                  return h('td', { class: ['cell', locked ? 'locked' : '', dev ? 'dev' : ''].filter(Boolean).join(' ') }, [
                    h('div', { class: 'hands' }, locked ? String(v) : h('input', {
                      class: 'inp', style: 'width:52px;text-align:center;padding:2px 4px',
                      value: String(v), onInput: (e: Event) => setHand(c, s, (e.target as HTMLInputElement).value),
                    })),
                    h('div', { class: 'pieces' }, v * perHand.value[c] + ' 件' + (dev ? ' ⚠' : '')),
                  ])
                }),
              ),
              h('td', { class: 'cell row-sum' }, [
                h('div', { class: 'hands' }, d.colors.reduce((s, c) => s + d.sizes.reduce((x, sz) => x + (row[c][sz] ?? 0), 0), 0) + ' 手'),
                h('div', { class: 'pieces' }, qtyFmt(d.colors.reduce((s, c) => s + statAll(c).pieces, 0)) + ' 件'),
              ]),
            ])
          })),
          h('tfoot', {}, h('tr', {}, [
            h('th', { class: 'batch-col' }, '合计'),
            ...d.colors.flatMap((c) => d.sizes.map((s) =>
              h('td', { class: 'cell' }, [
                h('div', { class: 'hands' }, String(d.batches.reduce((sum, b) => sum + (cells.value[b.id][c][s] ?? 0), 0)) + ' 手'),
                h('div', { class: 'pieces' }, qtyFmt(d.batches.reduce((sum, b) => sum + (cells.value[b.id][c][s] ?? 0) * perHand.value[c], 0)) + ' 件'),
              ]),
            )),
            h('td', { class: 'cell' }, [h('div', {}, totalHands.value + ' 手'), h('div', { class: 'pieces' }, qtyFmt(totalPieces.value) + ' 件')]),
          ])),
        ])),

        h('div', { class: 'note-strip', style: 'margin-top:10px' }, [
          '口径：件数 = 手数 × 每手件数（整数乘法，ADR-0020）；比例偏离 >20% 黄色提示不拦截；尾数（人工改件数产生）计入裁剪损耗，不出码不计件不入库；审核后 → ',
          h('b', {}, '裁片入 WIP（ADR-0018）'),
        ]),
      ]), true),

      card('改单联动规则（ADR-0021）', h('div', { class: 'note-strip' }, [
        'DRAFT/待审核 随便改 ｜ 已审核 改后需重新审核（30005）｜ 已打菲未扫码：未打印码自动重建、已打印码需作废重打（31007）｜ 已计件 🔒：禁止修改（30004），必须先红冲反计件再走「反审核 → 改 → 重审 → 重打菲」。',
      ])),

      chainOpen.value
        ? h(Modal, { title: '溯源链（ADR-0023）' }, {
            body: h('div', { class: 'chain', style: 'font-size:14px' }, [
              h('span', { class: 'node mono cur' }, 'XL02-0001'),
              h('span', { class: 'arrow' }, '←'),
              h('span', { class: 'node mono' }, 'BD-20261018-000031'),
              h('span', { class: 'arrow' }, '←'),
              h('span', { class: 'node mono' }, d.docNo),
              h('span', { class: 'arrow' }, '←'),
              h('span', { class: 'node mono' }, d.styleNo + '（单衣）'),
              h('span', { class: 'arrow' }, '←'),
              h('span', { class: 'node mono' }, 'DY-2609-01 / B-2610-001'),
              h('span', { class: 'arrow' }, '←'),
              h('span', { class: 'node' }, '苏州恒信纺织'),
            ]),
            footer: [h('button', { class: 'btn', onClick: () => (chainOpen.value = false) }, '关闭')],
          })
        : null,

      h(ToastHost, { items: toast.items }),
    ]
  },
})

export default CuttingMatrixPage
