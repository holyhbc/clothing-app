import { defineComponent, ref, computed, watch } from 'vue'
import { h } from './h'

export interface ComboOption {
  value: string
  label: string
  sub?: string
  disabled?: boolean
}

/**
 * 可搜索下拉（INV-9：05 §9.5 / 04 §5.1）
 * - 300ms 防抖过滤；↑↓ 移动、Enter 选中、Esc 关闭
 * - 支持「编码 + 名称」模糊匹配（不区分大小写）
 * - 空态可给「新建」入口；已选项回显「编码 + 名称」
 */
export const Combo = defineComponent({
  name: 'Combo',
  props: {
    options: { type: Array as unknown as () => ComboOption[], required: true },
    modelValue: { type: String, default: '' },
    placeholder: { type: String, default: '输入编码或名称搜索…' },
    emptyText: { type: String, default: '无匹配结果' },
    createText: { type: String, default: '' },
    width: { type: String, default: '' },
    disabled: Boolean,
  },
  emits: ['change', 'create'],
  setup(props, { emit, expose }) {
    const open = ref(false)
    const kw = ref('')
    const active = ref(0)

    const filtered = computed(() => {
      const q = kw.value.trim().toLowerCase()
      if (!q) return props.options.slice(0, 20)
      return props.options
        .filter((o) => (o.label + ' ' + (o.sub ?? '') + ' ' + o.value).toLowerCase().includes(q))
        .slice(0, 20)
    })

    const current = computed(() => props.options.find((o) => o.value === props.modelValue) ?? null)

    function pick(o: ComboOption) {
      if (o.disabled) return
      emit('change', o.value)
      open.value = false
      kw.value = ''
    }
    function onInput(e: Event) {
      kw.value = (e.target as HTMLInputElement).value
      active.value = 0
      open.value = true
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'ArrowDown') { e.preventDefault(); active.value = Math.min(active.value + 1, filtered.value.length - 1) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active.value = Math.max(active.value - 1, 0) }
      else if (e.key === 'Enter') { e.preventDefault(); const o = filtered.value[active.value]; if (o) pick(o) }
      else if (e.key === 'Escape') { open.value = false }
    }
    watch(() => props.modelValue, () => { kw.value = ''; active.value = 0 })

    expose({ open: () => (open.value = true) })

    const hl = (text: string) => {
      const q = kw.value.trim().toLowerCase()
      if (!q) return text
      const i = text.toLowerCase().indexOf(q)
      if (i < 0) return text
      return [text.slice(0, i), h('mark', {}, text.slice(i, i + q.length)), text.slice(i + q.length)]
    }

    return () =>
      h('div', { class: ['combo', open.value && 'open', props.disabled && 'disabled'], style: props.width ? { width: props.width } : {} }, [
        h('div', { class: 'combo-input' }, [
          h('input', {
            class: 'inp',
            value: open.value ? kw.value : current.value ? current.value.label + (current.value.sub ? '　' + current.value.sub : '') : '',
            placeholder: current.value && !open.value ? '' : props.placeholder,
            disabled: props.disabled,
            onInput,
            onFocus: () => { open.value = true; kw.value = '' },
            onBlur: () => window.setTimeout(() => (open.value = false), 150),
            onKeydown: onKey,
          }),
          h('span', { class: 'combo-icon' }, '⌕'),
        ]),
        open.value
          ? h('div', { class: 'combo-pop' }, [
              filtered.value.length
                ? filtered.value.map((o, i) =>
                    h('div', {
                      key: o.value,
                      class: ['combo-opt', i === active.value && 'on', o.disabled && 'off'],
                      onMousedown: (e: MouseEvent) => { e.preventDefault(); pick(o) },
                      onMouseenter: () => (active.value = i),
                    }, [
                      h('div', { class: 'co-main' }, hl(o.label)),
                      o.sub ? h('div', { class: 'co-sub' }, hl(o.sub)) : null,
                      o.disabled ? h('span', { class: 'co-off' }, '已停用') : null,
                    ]),
                  )
                : h('div', { class: 'combo-empty' }, [
                    props.emptyText,
                    props.createText
                      ? h('a', { class: 'combo-create', onMousedown: (e: MouseEvent) => { e.preventDefault(); emit('create', kw.value); open.value = false } }, props.createText)
                      : null,
                  ]),
              filtered.value.length >= 20 ? h('div', { class: 'combo-more' }, '结果过多，请继续输入关键字（05 §9.5：候选最多 20 条）') : null,
            ])
          : null,
      ])
  },
})

/** 生成选项：[{value,label,sub}]，按关键字过滤（前端 mock 版，对应后端 q 参数） */
export function comboOptions(rows: Array<Record<string, unknown>>, valueKey: string, labelKeys: string[]): ComboOption[] {
  return rows.map((r) => {
    const label = labelKeys.map((k) => String(r[k] ?? '')).filter(Boolean).join(' ')
    return { value: String(r[valueKey]), label: String(r[valueKey]), sub: label === String(r[valueKey]) ? '' : label }
  })
}