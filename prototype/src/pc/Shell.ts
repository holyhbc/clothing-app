import { h } from '../components/h'
import { defineComponent, ref, computed } from 'vue'

import { ToastHost, useToast } from '../components/ui'
import { PAGES } from '../pages/meta'

export default defineComponent({
  name: 'PcShell',
  props: { active: { type: String, required: true } },
  emits: ['navigate'],
  setup(props, { emit, slots }) {
    const toast = useToast()
    const groups = computed(() => {
      const m = new Map<string, typeof PAGES>()
      for (const p of PAGES) {
        if (!m.has(p.group)) m.set(p.group, [])
        m.get(p.group)!.push(p)
      }
      return [...m.entries()]
    })
    const roles = ['车间主管 · 缝制一组', '厂长 · 全厂', '仓管 · 全厂', '财务 · 全厂', '计件员 · 全厂']
    const role = ref(roles[0])

    return () => [
      h('div', { class: 'pc-layout' }, [
        h('header', { class: 'pc-header' }, [
          h('div', { class: 'pc-logo' }, [h('span', { class: 'pc-logo-mark' }, '衣'), '服装厂 ERP']),
          h('span', { class: 'muted' }, '｜'),
          h('span', { class: 'second' }, '原型演示 · 无后端交互'),
          h('div', { class: 'pc-header-spacer' }),
          h('a', { class: 'btn sm', href: '#/mobile' }, '员工端 H5 ›'),
          h('select', {
            class: 'sel', value: role.value, style: 'min-width:190px',
            onChange: (e: Event) => (role.value = (e.target as HTMLSelectElement).value),
          }, roles.map((r) => h('option', { value: r }, r))),
          h('div', { class: 'pc-user' }, [h('div', { class: 'pc-avatar' }, '王')]),
        ]),
        h('div', { class: 'pc-body' }, [
          h('aside', { class: 'pc-sider' },
            groups.value.map(([g, items]) =>
              h('div', {}, [
                h('div', { class: 'pc-menu-group-title' }, g),
                ...items.map((p) =>
                  h('div', {
                    class: ['pc-menu-item', p.key === props.active ? 'active' : ''],
                    onClick: () => emit('navigate', p.key),
                  }, [
                    h('span', { class: 'pc-menu-icon' }, p.icon),
                    h('span', {}, p.title),
                    p.sub ? h('span', { class: 'muted', style: 'font-size:11px;margin-left:auto' }, p.sub) : null,
                  ]),
                ),
              ]),
            ),
          ),
          h('main', { class: 'pc-content' }, slots.body?.()),
        ]),
      ]),
      h(ToastHost, { items: toast.items }),
    ]
  },
})