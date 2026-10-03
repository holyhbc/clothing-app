import { h } from '../../components/h'
import { defineComponent, ref, computed } from 'vue'

import { StatusTag, Pager, Modal, useToast, ToastHost } from '../../components/ui'
import { pageHead, card, descGrid } from '../shared'
import {
  styles, colors, sizeClasses, materials, customers, purchaseOrders, purchaseLines,
  sizeRatios, cuttingCalc, importDemo, money, qtyFmt, productCategories,
} from '../../mock'
import type { DocStatus } from '../../mock'

/* ===================== 商品管理（款号档案） ===================== */
export const ProductPage = defineComponent({
  name: 'ProductPage',
  setup() {
    const toast = useToast()
    const detail = ref(false)
    return () => [
      pageHead('商品管理（款号档案）', '一个款号 = 客户 + 季节 + 颜色组 + 尺码组 + BOM + 工序 + 单价 + 尺码比例 + 成本毛利', [
        h('button', { class: 'btn' }, '导出商品档案'),
        h('button', { class: 'btn' }, '导入款号'),
        h('button', { class: 'btn primary' }, '+ 新建款号'),
      ]),
      card('列表', [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '款号 / 名称'), h('th', {}, '分类'), h('th', {}, '客户'), h('th', {}, '季节'), h('th', {}, '交期'),
            h('th', { class: 'right' }, '订单'), h('th', { class: 'right' }, '已完工'),
            h('th', { class: 'right' }, '面料成本'), h('th', { class: 'right' }, '工价成本'),
            h('th', { class: 'right' }, '销售金额'), h('th', { class: 'right' }, '毛利率'),
            h('th', {}, '状态'), h('th', { style: 'width:200px' }, '操作'),
          ])),
          h('tbody', {}, styles.map((s) =>
            h('tr', { key: s.styleNo }, [
              h('td', {}, [
                h('div', { class: 'mono', style: 'font-weight:600' }, s.styleNo),
                h('div', { class: 'muted' }, s.name),
              ]),
              h('td', { class: 'nowrap' }, h('span', { class: 'chip' }, [
                h('span', { class: 'k' }, '分类'),
                h('span', { class: 'v' }, productCategories.find((c) => c.code === s.cat)?.name ?? '—'),
              ])),
              h('td', { class: 'nowrap' }, s.customer),
              h('td', { class: 'nowrap muted' }, s.season),
              h('td', { class: 'nowrap' }, s.delivery),
              h('td', { class: 'right amount' }, qtyFmt(s.orderQty)),
              h('td', { class: 'right amount' }, qtyFmt(s.finished)),
              h('td', { class: 'right amount' }, money(s.fabricCost)),
              h('td', { class: 'right amount' }, money(s.laborCost)),
              h('td', { class: 'right amount', style: 'font-weight:600' }, s.saleAmount === '0.00' ? '—' : money(s.saleAmount)),
              h('td', { class: 'right amount', style: s.margin !== '—' ? 'color:var(--color-success);font-weight:600' : 'color:var(--color-text-third)' }, s.margin === '—' ? '—' : s.margin + '%'),
              h('td', {}, h('span', {
                class: 'status-tag ' + (s.state === '已完工' ? 'st-approved' : s.state === '待投料' || s.state === '待打菲' ? 'st-draft' : 'st-submitted'),
              }, [h('span', { class: 'dot' }), s.state])),
              h('td', { class: 'row-actions' }, [
                h('a', { onClick: () => (detail.value = true) }, '档案'),
                h('a', {}, '颜色尺码'),
                h('a', {}, '工序单价'),
                h('a', {}, '尺码比例'),
                h('a', {}, '成本毛利'),
              ]),
            ]),
          )),
        ])),
        h(Pager, { total: styles.length, page: 1 }),
      ], undefined, true),

      detail.value
        ? h(Modal, { title: '款号档案 · HB-2026-0018 男式商务长袖衬衫', wide: true }, {
            body: h('div', {}, [
              h('div', { class: 'form-row' }, [h('label', {}, '客户'), h('div', { class: 'grow' }, [
                h('div', {}, '恒博服饰有限公司 · 客户款号 HB-SH-2601W · 账期 月结30天 · 信用额度 ¥500,000'),
              ])]),
              h('div', { class: 'form-row' }, [h('label', {}, '颜色组'), h('div', { class: 'grow' }, [
                h('span', { class: 'chip', style: 'margin-right:6px' }, 'WHT 本白 × 1,200 件'),
                h('span', { class: 'chip', style: 'margin-right:6px' }, 'BLK 黑 × 800 件'),
                h('span', { class: 'chip', style: 'margin-right:6px' }, 'NVY 藏青 × 300 件'),
                h('span', { class: 'chip' }, 'GRN 军绿 × 100 件'),
              ])]),
              h('div', { class: 'form-row' }, [h('label', {}, '尺码组'), h('div', { class: 'grow' }, [
                h('div', {}, '男装码表：S(155/76A) M(160/80A) L(165/84A) XL(170/88A) 2XL(175/92A) 3XL(180/96A)'),
              ])]),
              h('div', { class: 'form-row' }, [h('label', {}, '尺码比例'), h('div', { class: 'grow' }, [
                h('div', { class: 'muted', style: 'margin-bottom:6px' }, '按款号 × 颜色 预置手数（ADR-0013）；不同颜色比例不同'),
                h('table', { class: 'tbl' }, [
                  h('thead', {}, h('tr', {}, [h('th', {}, '颜色'), h('th', {}, '尺码'), h('th', { class: 'right' }, '手数')])),
                  h('tbody', {}, sizeRatios.map((r, i) => h('tr', { key: r.color + r.size }, [
                    h('td', {}, i === 0 ? r.color : null),
                    h('td', { class: 'mono' }, r.size),
                    h('td', { class: 'right amount' }, r.code),
                  ]))),
                ]),
              ])]),
              h('div', { class: 'form-row' }, [h('label', {}, '工序'), h('div', { class: 'grow' }, [
                h('div', {}, '01 打边 0.42 ｜ 02 拼前 0.85 ｜ 03 装袖 1.20 ｜ 04 合缝 0.68 ｜ 05 锁眼钉扣 0.45 ｜ 06 查勘 0.35（1打12件） ｜ 07 整烫包装 0.90（1打12件）'),
              ])]),
              h('div', { class: 'form-row' }, [h('label', {}, '成本毛利'), h('div', { class: 'grow' }, [
                h('div', {}, [
                  '面料（缸号批次实际成本） ', h('b', { class: 'amount' }, money(styles[0].fabricCost)),
                  ' ｜ 工价（已结算计件） ', h('b', { class: 'amount' }, money(styles[0].laborCost)),
                  ' ｜ 销售 ', h('b', { class: 'amount' }, money(styles[0].saleAmount)),
                  ' ｜ 毛利率 ', h('b', { style: 'color:var(--color-success)' }, styles[0].margin + '%'),
                ]),
              ])]),
            ]),
            footer: [
              h('button', { class: 'btn' }, '复制为新款号'),
              h('button', { class: 'btn primary', onClick: () => { toast.ok('已保存（演示）'); detail.value = false } }, '编辑档案'),
            ],
          })
        : null,
      h(ToastHost, { items: toast.items }),
    ]
  },
})

/* ===================== 基础资料 ===================== */
export const BaseDataPage = defineComponent({
  name: 'BaseDataPage',
  setup() {
    const toast = useToast()
    const tab = ref('客户')
    const importOpen = ref(false)
    const tabs = ['客户', '供应商', '颜色', '尺码 / 码表', '物料', '仓库', '工序', '款号']

    const importable = ['客户', '供应商', '颜色', '尺码 / 码表', '物料', '工序', '款号']

    return () => [
      pageHead('基础资料', '内置标准库（尺码 / 色卡 / 码表）+ 可自由新增；所有资料支持 Excel 批量导入（模板 → 校验 → 确认）', [
        h('button', { class: 'btn' }, '导出全部'),
        h('button', { class: 'btn' }, '导入'),
        h('button', { class: 'btn primary' }, '+ 新建'),
      ]),

      card(null, h('div', { style: 'display:flex;gap:4px;border-bottom:1px solid var(--color-border);margin:-4px -16px 0;padding:0 16px' },
        tabs.map((t) =>
          h('button', {
            class: ['btn', 'sm', tab.value === t ? 'primary' : ''],
            style: 'border-radius:6px 6px 0 0;border-bottom:none;margin-bottom:-1px',
            onClick: () => (tab.value = t),
          }, t),
        ),
      ), false, true),

      tab.value === '客户' ? card('客户', [
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '客户编码'), h('th', {}, '名称'), h('th', {}, '联系人'), h('th', {}, '电话'),
            h('th', {}, '结算方式'), h('th', { class: 'right' }, '信用额度'), h('th', { class: 'right' }, '应收余额'),
            h('th', {}, '状态'), h('th', { style: 'width:140px' }, '操作'),
          ])),
          h('tbody', {}, customers.map((c) => h('tr', { key: c.code }, [
            h('td', { class: 'mono nowrap' }, c.code),
            h('td', {}, c.name),
            h('td', { class: 'nowrap' }, c.contact),
            h('td', { class: 'mono' }, c.phone),
            h('td', { class: 'nowrap' }, c.settle),
            h('td', { class: 'right amount' }, c.credit),
            h('td', { class: 'right amount', style: c.balance !== '¥0.00' ? 'color:var(--color-danger);font-weight:600' : '' }, c.balance),
            h('td', {}, c.active
              ? h('span', { class: 'status-tag st-approved' }, [h('span', { class: 'dot' }), '启用'])
              : h('span', { class: 'status-tag st-cancelled' }, [h('span', { class: 'dot' }), '停用'])),
            h('td', { class: 'row-actions' }, [
              h('a', {}, '编辑'),
              h('a', { class: c.active ? '' : '', onClick: () => toast.ok(c.active ? '已停用（演示）' : '已启用（演示）') }, c.active ? '停用' : '启用'),
              h('a', { class: 'disabled' }, '删除'),
            ]),
          ]))),
        ])),
      ], undefined, true) : null,

      tab.value === '颜色' ? card('颜色字典（内置标准库 + 可自由新增）', [
        h('div', { class: 'alert info' }, ['内置色卡由 ', h('code', {}, 'python -m app.cli.seed_baseline'), ' 写入，标 ', h('code', {}, 'is_builtin=true'), '，可停用不可删除；seed 幂等且不覆盖用户改动。']),
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '色码'), h('th', {}, '颜色名'), h('th', {}, '标准色卡'), h('th', {}, '来源'),
            h('th', { class: 'right' }, '被引用'), h('th', {}, '状态'), h('th', { style: 'width:140px' }, '操作'),
          ])),
          h('tbody', {}, colors.map((c) => h('tr', { key: c.code }, [
            h('td', { class: 'mono', style: 'font-weight:600' }, c.code),
            h('td', {}, h('span', {}, [
              h('span', {
                style: `display:inline-block;width:14px;height:14px;border-radius:3px;border:1px solid var(--color-border);margin-right:7px;vertical-align:-2px;background:${c.code === 'WHT' ? '#f7f5f0' : c.code === 'BLK' ? '#1d2129' : c.code === 'NVY' ? '#1b2436' : c.code === 'KHK' ? '#b59a6a' : c.code === 'GRN' ? '#4a5334' : c.code === 'BEG' ? '#e0d5c0' : c.code === 'RED' ? '#c0272d' : '#4d4d4d'}`,
              }),
              c.name,
            ])),
            h('td', { class: 'mono muted' }, c.family),
            h('td', {}, c.builtin ? h('span', { class: 'status-tag st-draft' }, [h('span', { class: 'dot' }), '内置']) : h('span', { class: 'status-tag st-submitted' }, [h('span', { class: 'dot' }), '自建'])),
            h('td', { class: 'right tabular' }, String(c.refs)),
            h('td', {}, h('span', { class: 'status-tag st-approved' }, [h('span', { class: 'dot' }), '启用'])),
            h('td', { class: 'row-actions' }, [
              h('a', {}, '编辑'),
              c.refs === 0
                ? h('a', { class: 'danger' }, '删除')
                : h('a', { class: 'disabled', title: '已被引用，只能停用（20003）' }, '删除'),
              h('a', {}, '引用明细'),
            ]),
          ]))),
        ])),
      ], undefined, true) : null,

      tab.value === '尺码 / 码表' ? card('尺码字典与码表', [
        h('div', { class: 'alert info' }, [
          '内置', h('b', {}, ' 2 个'), ' 尺码模板（业务方 2026-10-01 确认）：',
          h('code', {}, '女款模板 S-M / L-XL'), ' 与 ', h('code', {}, '男款模板 L XL XXL 3XL'),
          '。童装等可自建。未被任何单据引用的颜色/尺码/模板可', h('b', {}, '物理删除'),
          '；已引用的只能停用（20003）。',
        ]),
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '尺码模板'), h('th', {}, '类型'), h('th', {}, '尺码列表'),
            h('th', { class: 'right' }, '数量'), h('th', { class: 'right' }, '被引用'),
            h('th', { style: 'width:180px' }, '操作'),
          ])),
          h('tbody', {}, sizeClasses.map((s) => h('tr', { key: s.cls }, [
            h('td', {}, h('b', {}, s.cls)),
            h('td', {}, h('span', { class: 'status-tag ' + (s.builtin ? 'st-draft' : 'st-submitted') }, [h('span', { class: 'dot' }), s.kind])),
            h('td', { class: 'mono muted', style: 'font-size:12px' }, s.sizes),
            h('td', { class: 'right tabular' }, String(s.count)),
            h('td', { class: 'right tabular' }, String(s.refs)),
            h('td', { class: 'row-actions' }, [
              h('a', { onClick: () => toast.ok(`已用「${s.cls}」模板一键带出新建款号（演示）`) }, '一键带出'),
              h('a', {}, '编辑'),
              s.refs === 0
                ? h('a', { class: 'danger', onClick: () => toast.err('演示：未被引用 → 可物理删除') }, '删除')
                : h('a', { class: 'disabled', title: '已被引用，只能停用（20003）' }, '删除'),
            ]),
          ]))),

      card('商品分类（ADR-0020：套装/单衣/单裤/棉毛/背心/打底裤，被款号引用只能停用）', h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
        h('thead', {}, h('tr', {}, [
          h('th', {}, '分类'), h('th', {}, '编码'), h('th', { class: 'right' }, '被引用款号数'),
          h('th', {}, '状态'), h('th', {}, '用途'), h('th', { style: 'width:190px' }, '操作'),
        ])),
        h('tbody', {}, productCategories.map((c) => h('tr', { key: c.code }, [
          h('td', { style: 'font-weight:600' }, c.name),
          h('td', { class: 'mono' }, c.code),
          h('td', { class: 'right amount' }, String(c.styles)),
          h('td', {}, h('span', { class: 'status-tag ' + (c.active ? 'st-approved' : 'st-draft') }, [h('span', { class: 'dot' }), c.active ? '启用' : '停用'])),
          h('td', { class: 'muted' }, '按分类算裁剪工资 · 裁剪单默认取款号分类'),
          h('td', { class: 'row-actions' }, [
            h('a', { onClick: () => toast.ok('已新增分类（演示）：自定义分类同样可用') }, '新增'),
            h('a', { onClick: () => (c.styles ? toast.err('已被款号引用，只能停用不能删除') : toast.ok('已删除（演示）')) }, '删除'),
            h('a', { onClick: () => toast.ok('已停用（演示）') }, '停用'),
          ]),
        ]))),
      ])), true),
        ])),
      ], undefined, true) : null,

      tab.value === '物料' ? card('物料（面料 / 辅料，含标准门幅）', [
        h('div', { class: 'alert warn' }, ['门幅：物料主数据存「标准门幅」，采购单行可填「实际门幅」覆盖 → 批次按实际门幅记，裁剪时校验有效门幅。']),
        h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
          h('thead', {}, h('tr', {}, [
            h('th', {}, '物料编码'), h('th', {}, '名称'), h('th', {}, '类别'), h('th', {}, '成分'),
            h('th', { class: 'right' }, '标准门幅(cm)'), h('th', {}, '单位'), h('th', { class: 'right' }, '参考单价'),
            h('th', {}, '规格'), h('th', { class: 'right' }, '被引用'), h('th', { style: 'width:120px' }, '操作'),
          ])),
          h('tbody', {}, materials.map((m) => h('tr', { key: m.code }, [
            h('td', { class: 'mono nowrap' }, m.code),
            h('td', {}, m.name),
            h('td', {}, h('span', { class: 'status-tag ' + (m.cat === '面料' ? 'st-submitted' : 'st-draft') }, [h('span', { class: 'dot' }), m.cat])),
            h('td', { class: 'muted', style: 'font-size:12px' }, m.comp),
            h('td', { class: 'right amount' }, m.width),
            h('td', { class: 'nowrap' }, m.unit),
            h('td', { class: 'right amount' }, m.price),
            h('td', { class: 'muted', style: 'font-size:12px' }, m.spec),
            h('td', { class: 'right tabular' }, String(m.refs)),
            h('td', { class: 'row-actions' }, [h('a', {}, '编辑'), h('a', { class: 'disabled' }, '删除')]),
          ]))),
        ])),
      ], undefined, true) : null,

      !['客户', '颜色', '尺码 / 码表', '物料'].includes(tab.value)
        ? card(tab.value, h('div', { class: 'alert info' }, [
            h('b', {}, tab.value),
            ' 页面示意：本原型聚焦核心页面，',
            tab.value, '详情请见 ',
            h('code', {}, 'docs/modules/01-基础资料.md'),
            '。',
          ]))
        : null,

      importOpen.value
        ? h(Modal, { title: `Excel 导入 · ${tab.value}`, wide: true }, {
            body: h('div', {}, [
              h('div', { class: 'alert info' }, ['三步式：**下载模板 → 上传校验（不写库）→ 确认导入（单事务）**。有错误行时禁止提交，不允许静默跳过。']),
              h('div', { class: 'steps-mini', style: 'margin-bottom:16px' }, [
                h('div', { class: 'st done' }, '① 下载模板'),
                h('div', { class: 'st current' }, '② 上传校验'),
                h('div', { class: 'st' }, '③ 确认导入'),
              ]),
              h('div', { class: 'form-row' }, [
                h('label', {}, '文件'),
                h('div', { class: 'grow' }, [
                  h('input', { type: 'file', accept: '.xlsx', class: 'inp', style: 'width:100%' }),
                  h('div', { class: 'form-hint' }, '仅 .xlsx；单文件 ≤ 5000 行；表头必须与模板一致'),
                ]),
              ]),
              h('div', { class: 'form-row' }, [
                h('label', {}, '已存在数据'),
                h('div', { class: 'grow' }, h('select', { class: 'sel', style: 'width:100%' }, [
                  '跳过（只新增）', '更新（按编码匹配覆盖非空字段）',
                ].map((x) => h('option', {}, x)))),
              ]),
              h('div', { class: 'alert warn' }, ['以下为演示校验结果：']),
              h('div', { style: 'display:flex;gap:16px;margin-bottom:12px' }, [
                h('div', { class: 'kpi', style: 'flex:1;padding:12px' }, [h('div', { class: 'kpi-label' }, '总行数'), h('div', { class: 'kpi-value', style: 'font-size:20px' }, String(importDemo.total))]),
                h('div', { class: 'kpi', style: 'flex:1;padding:12px' }, [h('div', { class: 'kpi-label' }, '可导入'), h('div', { class: 'kpi-value', style: 'font-size:20px;color:var(--color-success)' }, String(importDemo.valid))]),
                h('div', { class: 'kpi', style: 'flex:1;padding:12px' }, [h('div', { class: 'kpi-label' }, '有问题'), h('div', { class: 'kpi-value', style: 'font-size:20px;color:var(--color-danger)' }, String(importDemo.errors.length))]),
              ]),
              h('div', { class: 'tbl-wrap' }, h('table', { class: 'tbl' }, [
                h('thead', {}, h('tr', {}, [h('th', { class: 'right' }, '行号'), h('th', {}, '字段'), h('th', {}, '原值'), h('th', {}, '错误说明'), h('th', {}, '建议改法')])),
                h('tbody', {}, importDemo.errors.map((e) => h('tr', { key: e.row }, [
                  h('td', { class: 'right tabular' }, String(e.row)),
                  h('td', { class: 'mono' }, e.field),
                  h('td', { class: 'muted mono', style: 'font-size:12px' }, e.value),
                  h('td', {}, h('span', { style: 'color:var(--color-danger)' }, e.msg)),
                  h('td', { class: 'muted', style: 'font-size:12px' }, '查基础资料对照'),
                ]))),
              ])),
            ]),
            footer: [
              h('button', { class: 'btn' }, '下载模板'),
              h('button', { class: 'btn' }, '下载错误明细'),
              h('button', { class: 'btn', disabled: true, title: '请先修正 8 行问题' }, '确认导入（0/128）'),
            ],
          })
        : null,
      h(ToastHost, { items: toast.items }),
    ].filter(Boolean) as any
  },
})