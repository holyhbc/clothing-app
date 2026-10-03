import { h } from '../../components/h'
import { defineComponent, ref, computed } from 'vue'

import { StatusTag, Pager, Modal, useToast, ToastHost } from '../../components/ui'
import { pageHead, card, descGrid, field, select } from '../shared'
import { cuttingOrders, cuttingDetail, money, qtyFmt } from '../../mock'
import type { DocStatus } from '../../mock'

export default defineComponent({
  name: 'CuttingPage',
  setup() {
    const toast = useToast()
    const view = ref<'list' | 'detail'>('list')
    const statusFilter = ref('全部')
    const page = ref(1)
    const reject = ref(false)
    const reverse = ref(false)
    const reason = ref('')
    const reasonErr = ref('')

    const list = computed(() =>
      statusFilter.value === '全部' ? cuttingOrders : cuttingOrders.filter((o) => o.status === statusFilter.value),
    )

    function openDetail() { view.value = 'detail' }
    function back() { view.value = 'list'; reject.value = false; reverse.value = false; reason.value = ''; reasonErr.value = '' }

    function submitReject() {
      if (!reason.value.trim()) { reasonErr.value = '驳回必须填写原因（10006）'; return }
      toast.ok('已驳回（演示）'); back()
    }
    function submitReverse() {
      if (!reason.value.trim()) { reasonErr.value = '反审核必须填写原因（10006）'; return }
      toast.ok('已反审核，耗料与台账已反向冲回（演示）'); back()
    }

    const d = cuttingDetail
    const listView = () => [
            pageHead('裁剪单', '唯一生产主单（ADR-0023）：表头款号/分类/多色，表体布批行 × 颜色 × 尺码矩阵（手数直接输入）。审核生效 = 扣料 + 裁片入 WIP｜ 反审核必填原因', [
              h('button', { class: 'btn' }, '导出'),
              h('button', { class: 'btn primary', onClick: () => (window.location.hash = '/pc/cuttingmatrix') }, '+ 新建裁剪单'),
            ]),
            card(null, h('div', { class: 'filters' }, [
              field('款号', 'HB-2026-0018', 'w-lg'),
              select('状态', ['全部', 'DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'CANCELLED']),
              select('车间', ['全部', '裁床组', '裁剪一组', '裁剪二组']),
              h('div', { class: 'field' }, [h('label', {}, '单据日期'), h('input', { class: 'inp', value: '2026-10-15 ~ 2026-10-18' })]),
              h('button', { class: 'btn primary', onClick: () => (statusFilter.value = '全部') }, '查询'),
              h('button', { class: 'btn' }, '重置'),
            ])),
            card('列表', h('div', {}, [
              h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
                h('thead', {}, h('tr', {}, [
                  h('th', {}, '单据号'), h('th', {}, '款号 / 分类 / 色组'), h('th', {}, '车间'), h('th', {}, '布批（缸号/匹号）'),
                  h('th', { class: 'right' }, '手数'), h('th', {}, '来源 / 打菲'),
                  h('th', { class: 'right' }, '耗料(米)'), h('th', { class: 'right' }, '出数(件)'),
                  h('th', { class: 'right' }, '尾数'), h('th', { class: 'right' }, '损耗率'),
                  h('th', {}, '状态'), h('th', {}, '制单人'), h('th', {}, '提交时间'), h('th', { style: 'width:190px' }, '操作'),
                ])),
                h('tbody', {}, list.value.map((o) =>
                  h('tr', {}, [
                    h('td', { class: 'mono nowrap' }, o.docNo),
                    h('td', {}, [
                      h('div', { class: 'mono' }, o.styleNo),
                      h('div', { class: 'muted' }, [h('span', {}, '单衣'), '　', o.colorGroup]),
                    ]),
                    h('td', { class: 'nowrap' }, o.workshop),
                    h('td', { class: 'mono nowrap muted' }, o.fabric),
                    h('td', { class: 'right amount' }, o.hands + ' 手'),
                    h('td', { class: 'nowrap' }, [
                      h('a', { style: 'cursor:pointer;color:var(--color-primary)', onClick: () => (window.location.hash = '/pc/bundling') }, 'BD-…000031'),
                      h('div', { class: 'muted' }, '1,200 码 / 已印 1,000'),
                    ]),
                    h('td', { class: 'right amount' }, qtyFmt(o.fabricQty)),
                    h('td', { class: 'right amount' }, qtyFmt(o.output)),
                    h('td', { class: 'right amount muted' }, o.balance ? qtyFmt(o.balance) : '—'),
                    h('td', { class: 'right amount', style: o.wasteRate > 5 ? 'color:var(--color-warning)' : '' }, o.wasteRate + '%'),
                    h('td', {}, h(StatusTag, { status: o.status })),
                    h('td', { class: 'nowrap' }, o.user),
                    h('td', { class: 'muted nowrap' }, o.time),
                    h('td', { class: 'row-actions' }, [
                      h('a', { onClick: openDetail }, '详情'),
                      o.status === 'SUBMITTED' ? h('a', { onClick: () => toast.ok('已审核（演示）：已扣面料 1,280.5 米，出数 2,400 件') }, '审核') : null,
                      o.status === 'SUBMITTED' ? h('a', { class: 'danger', onClick: () => { view.value = 'detail'; reject.value = true } }, '驳回') : null,
                      o.status === 'APPROVED' ? h('a', { class: 'danger', onClick: () => { view.value = 'detail'; reverse.value = true } }, '反审核') : null,
                      h('a', { onClick: () => (window.location.hash = '/pc/cuttingmatrix') }, '矩阵录入'),
                      o.status === 'DRAFT' || o.status === 'REJECTED' ? h('a', {}, '编辑') : null,
                      o.status === 'APPROVED' ? h('a', { class: 'disabled' }, '打印') : null,
                    ]),
                  ]),
                )),
              ])),
              h(Pager, { total: list.value.length, page: page.value, 'onUpdate:page': (p: number) => (page.value = p) }),
            ]), true),
          ]
    const detailView = () => [
            h('div', { style: 'margin-bottom:12px' }, h('button', { class: 'btn sm' }, '‹ 返回列表')),
            pageHead('裁剪单详情', undefined, [
              d.status === 'SUBMITTED' ? h('button', { class: 'btn', onClick: () => { reject.value = true } }, '驳回') : null,
              d.status === 'SUBMITTED' ? h('button', { class: 'btn primary', onClick: () => toast.ok('已审核（演示）') }, '审核') : null,
              d.status === 'APPROVED' ? h('button', { class: 'btn danger', onClick: () => { reverse.value = true } }, '反审核') : null,
              h('button', { class: 'btn', disabled: true }, '打印（已审核后可打印）'),
            ]),

            h('div', { style: 'display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap' }, [
              h('span', { class: 'mono', style: 'font-size:18px;font-weight:600' }, d.docNo),
              h('button', { class: 'btn sm', onClick: () => toast.ok('单据号已复制') }, '复制'),
              h(StatusTag, { status: d.status }),
              h('span', { class: 'muted' }, '单号规则 CT-YYYYMMDD-6位序号（09 §2.1）'),
            ]),

            card('基本信息', descGrid([
              ['款号', h('span', { class: 'mono' }, d.styleNo)],
              ['款名', d.styleName],
              ['客户', `${d.customer}　${d.customerStyleNo}`],
              ['色组', d.colorGroup],
              ['车间', d.workshop],
              ['裁剪日期', d.docDate],
              ['面料', h('span', { class: 'mono' }, d.fabric)],
              ['铺布层数', String(d.plys)],
              ['交期', d.delivery],
            ])),

            card('数量与损耗（整件口径，09 §4.2）', h('div', {}, [
              h('div', { class: 'alert ok' }, ['✓ ', h('span', {}, [
                '本单裁剪可出 ', h('b', {}, '2,400.5 件'), ' → 向下取整 ',
                h('b', {}, 'output_qty = 2,400 件'), '；余量 ',
                h('b', {}, 'balance_qty = 0.5 件'), ' 已计入 cut_waste_qty，',
                h('b', {}, '不生成打菲码、不计件、不入库'),
              ])]),
              descGrid([
                ['耗料量', h('span', { class: 'amount' }, qtyFmt(d.fabricQty) + ' 米')],
                ['出数 output_qty', h('span', { class: 'amount', style: 'color:var(--color-primary);font-weight:600' }, qtyFmt(d.output) + ' 件')],
                ['尾数 balance_qty', h('span', { class: 'amount' }, qtyFmt(d.balance) + ' 件（不入库）')],
                ['损耗 cut_waste_qty', h('span', { class: 'amount' }, qtyFmt(d.waste) + ' 米（' + d.wasteRate + '%）')],
                ['布头入库', h('span', { class: 'amount' }, '86.2 米 → 面料批次（同缸号匹号，批次实际成本 28.50）')],
                ['已打菲 / 已入库', '1,200 扎 / 0 件'],
              ], 3),
            ])),

            card('裁剪明细（按色 / 尺码 / 匹号 / 缸号）', h('div', {}, [
              h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
                h('thead', {}, h('tr', {}, [
                  h('th', {}, '颜色'), h('th', {}, '尺码'), h('th', {}, '匹号'), h('th', {}, '缸号'),
                  h('th', { class: 'right' }, '耗料(米)'), h('th', { class: 'right' }, '单件用量'), h('th', { class: 'right' }, '出数(件)'), h('th', { class: 'right' }, '尾数'),
                ])),
                h('tbody', {}, d.lines.map((l) => h('tr', {}, [
                  h('td', {}, l.color), h('td', { class: 'mono nowrap' }, l.size),
                  h('td', { class: 'mono nowrap' }, l.boltNo), h('td', { class: 'mono nowrap' }, l.dyeLot),
                  h('td', { class: 'right amount' }, qtyFmt(l.fabricQty)),
                  h('td', { class: 'right amount' }, qtyFmt(l.usage)),
                  h('td', { class: 'right amount', style: 'font-weight:600' }, qtyFmt(l.output)),
                  h('td', { class: 'right amount muted' }, l.balance ? qtyFmt(l.balance) : '—'),
                ]))),
              ])),
              h('div', { class: 'sticky-actions' }, [
                h('span', { class: 'muted', style: 'margin-right:auto' }, '合计 耗料 1,230.5 米 ｜ 出数 2,400 件 ｜ 尾数 0.5 件'),
                h('b', { class: 'amount' }, '出数合计 2,400 件'),
              ]),
            ]), true),

            card('操作日志（document_logs，只增不删）', h('div', { class: 'timeline' }, d.logs.map((l) =>
              h('div', { class: 'tl-item' }, [
                h('div', { class: 'tl-dot' }),
                h('div', { class: 'tl-body' }, [
                  h('div', { class: 'tl-title' }, [
                    l.action,
                    l.from ? h('span', { class: 'muted' }, `　${l.from} → ${l.to}`) : null,
                  ]),
                  h('div', { class: 'tl-meta' }, `${l.who} · ${l.time}`),
                  l.reason ? h('div', { class: 'tl-reason' }, '原因：' + l.reason) : null,
                ]),
              ]),
            )),
            )
    ]

    return () => [
      view.value === 'list' ? listView() : detailView(),
      reject.value
        ? h(Modal, { title: '驳回裁剪单' }, {
            body: h('div', {}, [
              h('div', { class: 'alert warn' }, ['! ', '驳回后单据回到可编辑状态，必须填写原因（错误码 10006）']),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '原因'),
                h('div', { class: 'grow' }, [
                  h('textarea', { class: 'inp', rows: 3, placeholder: '例如：M 码用量与 BOM 不符，请按 0.825 米重新核对', value: reason.value, onInput: (e: Event) => { reason.value = (e.target as HTMLTextAreaElement).value; reasonErr.value = '' } }),
                  reasonErr.value ? h('div', { class: 'form-err' }, reasonErr.value) : null,
                ]),
              ]),
            ]),
            footer: [h('button', { class: 'btn', onClick: back }, '取消'), h('button', { class: 'btn danger', onClick: submitReject }, '确认驳回')],
          })
        : null,

      reverse.value
        ? h(Modal, { title: '反审核裁剪单', wide: true }, {
            body: h('div', {}, [
              h('div', { class: 'alert danger' }, ['! ', '反审核会同事务反向冲销：① 加回面料库存 ② 反向台账（按同一批批次明细逐条）③ 冲销结转记录。历史单据与流水保留，不物理删除。']),
              h('div', { class: 'alert warn' }, ['! ', '校验：已打菲数量 ≤ 已出数 − 本次冲销量（1,200 ≤ 2,400 ✓）；若不满足返回 30002。']),
              h('div', { class: 'form-row' }, [
                h('label', { class: 'required' }, '原因'),
                h('div', { class: 'grow' }, [
                  h('textarea', { class: 'inp', rows: 3, placeholder: '例如：客户临时改色，裁剪单作废重做', value: reason.value, onInput: (e: Event) => { reason.value = (e.target as HTMLTextAreaElement).value; reasonErr.value = '' } }),
                  reasonErr.value ? h('div', { class: 'form-err' }, reasonErr.value) : null,
                  h('div', { class: 'form-hint' }, '必填。写入 document_logs 的 reason 字段，可审计。'),
                ]),
              ]),
            ]),
            footer: [
              h('button', { class: 'btn', onClick: back }, '取消'),
              h('button', { class: 'btn danger', onClick: submitReverse }, '确认反审核并冲销库存'),
            ],
          })
        : null,

      h(ToastHost, { items: toast.items }),
    ]
  },
})