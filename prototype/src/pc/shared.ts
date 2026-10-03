import { h } from '../components/h'
import { defineComponent, ref } from 'vue'


export function pageHead(title: string, sub?: string | null, ...actions: unknown[]) {
  return h('div', { class: 'page-head' }, [
    h('div', {}, [
      h('div', { class: 'page-title' }, title),
      sub ? h('div', { class: 'page-subtitle' }, sub) : null,
    ]),
    h('div', { class: 'page-head-actions' }, (actions.flat() ?? []) as any),
  ])
}

export function card(title: unknown, body: unknown, ...rest: unknown[]) {
  const actions = rest.find((x) => Array.isArray(x)) as unknown[] | undefined
  const tight = rest.includes(true)
  const head = typeof title === 'string' ? title : title
  return h('div', { class: 'card' }, [
    title
      ? h('div', { class: 'card-head' }, [title, actions?.length ? h('div', { class: 'card-head-actions' }, actions as any) : null])
      : null,
    h('div', { class: ['card-body', tight && 'tight'] }, body as any),
  ])
}

export function descGrid(items: Array<[string, unknown]>, ...rest: Array<number | undefined>) {
  const cols = (rest.find((x) => typeof x === 'number') as number | undefined) ?? 3
  return h('div', { class: ['desc-grid', cols === 2 && 'two'] },
    items.map(([k, v]) =>
      h('div', { class: 'desc-item' }, [h('div', { class: 'dl' }, k), h('div', { class: 'dv' }, v as any)]),
    ),
  )
}

export function field(label: string, value: unknown = '', cls = '') {
  return h('div', { class: 'field' }, [h('label', {}, label), h('input', { class: ['inp', cls], value: value as string })])
}

export function select(label: string, options: string[]) {
  return h('div', { class: 'field' }, [
    h('label', {}, label),
    h('select', { class: 'sel' }, options.map((o) => h('option', { value: o }, o))),
  ])
}