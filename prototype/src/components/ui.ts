import { h } from '../components/h'
import { defineComponent, ref, reactive, type PropType } from 'vue'

import { STATUS_TEXT, STATUS_CLASS, type DocStatus } from '../mock'

/* ===================== StatusTag（docs/06 §1 状态色映射） ===================== */
export const StatusTag = defineComponent({
  name: 'StatusTag',
  props: { status: { type: String as PropType<DocStatus | 'PENDING'>, required: true } },
  setup(props) {
    const cls = () => STATUS_CLASS[props.status as DocStatus] ?? 'st-pending'
    const txt = () => STATUS_TEXT[props.status as DocStatus] ?? '待处理'
    return () => h('span', { class: ['status-tag', cls()] }, [h('span', { class: 'dot' }), txt()])
  },
})

/* ===================== Modal（原生实现，概念版） ===================== */
export const Modal = defineComponent({
  name: 'Modal',
  props: { title: String, wide: Boolean },
  emits: ['close'],
  setup(props, { emit, slots }) {
    return () =>
      h('div', { class: 'modal-mask', onClick: (e: MouseEvent) => { if (e.target === e.currentTarget) emit('close') } }, [
        h('div', { class: ['modal', props.wide && 'wide'] }, [
          h('div', { class: 'modal-head' }, [props.title, h('span', { class: 'x', onClick: () => emit('close') }, '✕')]),
          h('div', { class: 'modal-body' }, slots.body?.()),
          h('div', { class: 'modal-foot' }, slots.footer?.() ?? [
            h('button', { class: 'btn', onClick: () => emit('close') }, '取消'),
          ]),
        ]),
      ])
  },
})

/* ===================== Toast ===================== */
interface ToastItem { id: number; kind: 'ok' | 'err'; text: string }
let toastSeq = 0
export function useToast() {
  const items = reactive<ToastItem[]>([])
  function push(kind: 'ok' | 'err', text: string) {
    const id = ++toastSeq
    items.push({ id, kind, text })
    setTimeout(() => {
      const i = items.findIndex((x) => x.id === id)
      if (i >= 0) items.splice(i, 1)
    }, 2400)
  }
  return { items, ok: (t: string) => push('ok', t), err: (t: string) => push('err', t) }
}

export const ToastHost = defineComponent({
  name: 'ToastHost',
  props: { items: { type: Array as PropType<ToastItem[]>, required: true } },
  setup(props) {
    return () =>
      h('div', { class: 'toast-host' },
        props.items.map((t) =>
          h('div', { class: ['toast', t.kind] }, [
            h('span', { class: 'i' }, t.kind === 'ok' ? '✓' : '!'),
            h('span', {}, t.text),
          ]),
        ),
      )
  },
})

/* ===================== 简易分页 ===================== */
export const Pager = defineComponent({
  name: 'Pager',
  props: { total: { type: Number, default: 0 }, page: { type: Number, default: 1 } },
  emits: ['update:page'],
  setup(props, { emit }) {
    return () => {
      const pages = Math.max(1, Math.ceil(props.total / 20))
      const list = Array.from({ length: Math.min(pages, 5) }, (_, i) => i + 1)
      return h('div', { class: 'pager' }, [
        h('span', {}, `共 ${props.total} 条`),
        h('span', { class: 'pg', onClick: () => emit('update:page', Math.max(1, props.page - 1)) }, '‹'),
        list.map((p) =>
          h('span', { class: ['pg', p === props.page && 'active'], onClick: () => emit('update:page', p) }, String(p)),
        ),
        h('span', { class: 'pg', onClick: () => emit('update:page', Math.min(pages, props.page + 1)) }, '›'),
      ])
    }
  },
})

/* ===================== 二维码占位（SVG 伪随机点阵） ===================== */
export const QrBox = defineComponent({
  name: 'QrBox',
  props: { text: { type: String, required: true }, size: { type: Number, default: 96 } },
  setup(props) {
    const cells = 25
    let seed = 0
    for (const ch of props.text) seed = (seed * 31 + ch.charCodeAt(0)) >>> 0
    const rnd = () => {
      seed = (seed * 1103515245 + 12345) >>> 0
      return seed / 0xffffffff
    }
    const rects: { x: number; y: number }[] = []
    for (let y = 0; y < cells; y++) {
      for (let x = 0; x < cells; x++) {
        const corner = (x < 7 && y < 7) || (x >= cells - 7 && y < 7) || (x < 7 && y >= cells - 7)
        if (corner) continue
        if (rnd() > 0.52) rects.push({ x, y })
      }
    }
    return () =>
      h('svg', { width: props.size, height: props.size, viewBox: `0 0 ${cells} ${cells}`, shapeRendering: 'crispEdges' }, [
        ...rects.map((r) => h('rect', { x: r.x, y: r.y, width: 1, height: 1, fill: '#1d2129' })),
        ...([[0, 0], [cells - 7, 0], [0, cells - 7]] as const).flatMap(([ox, oy]) => [
          h('rect', { x: ox, y: oy, width: 7, height: 7, fill: 'none', stroke: '#1d2129', 'stroke-width': 1 }),
          h('rect', { x: ox + 2, y: oy + 2, width: 3, height: 3, fill: '#1d2129' }),
        ]),
      ])
  },
})

/* ===================== 简易筛选条 ===================== */
export const FilterBar = defineComponent({
  name: 'FilterBar',
  setup(_, { slots }) {
    return () => h('div', { class: 'card' }, [h('div', { class: 'card-body' }, [h('div', { class: 'filters' }, slots.default?.())])])
  },
})

/* ===================== 大数字统计 ===================== */
export const Stat = defineComponent({
  name: 'Stat',
  props: {
    label: String,
    value: String,
    unit: String,
    sub: String,
    trend: String,
    tip: String,
    money: Boolean,
  },
  setup(props) {
    return () =>
      h('div', { class: 'kpi' }, [
        h('div', { class: 'kpi-label' }, [props.label, props.tip ? h('span', { class: 'muted', title: props.tip }, 'ⓘ') : null]),
        h('div', { class: 'kpi-value' }, [
          props.value,
          props.unit ? h('span', { class: 'kpi-unit' }, props.unit) : null,
        ]),
        props.sub ? h('div', { class: ['kpi-sub', props.trend] }, props.sub) : null,
      ])
  },
})

export function useModal() {
  const open = ref(false)
  return { open, show: () => (open.value = true), hide: () => (open.value = false) }
}