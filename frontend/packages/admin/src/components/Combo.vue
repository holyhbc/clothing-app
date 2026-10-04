<script setup lang="ts">
/**
 * `<Combo>` 可搜索下拉（docs/06 §10 强制）。
 *
 * `defineOptions` 不是为了好看：任务卡规定的文件名（`Login.vue` / `Error403.vue` /
 * `Combo.vue` …）是单词，而 eslint 的 `vue/multi-word-component-names` 要求组件名
 * 至少两个词。文件名由规范定死、组件名由 lint 定死，两者只能用 `defineOptions` 调和 ——
 * 改文件名会同时偏离任务卡和 docs/03 §2.2 的目录约定。
 */
defineOptions({ name: 'ComboSelect' })
/**
 * `<Combo>` 可搜索下拉（docs/06 §10 / docs/05 §9.5 强制）。
 *
 * ```vue
 * <Combo v-model="form.styleNo"
 *        :fetch-options="searchStyles"
 *        placeholder="输入货号或款名搜索…"
 *        create-text="新建款号" />
 * ```
 *
 * ## 为什么不能用 antd 的 `<a-select show-search>`
 *
 * `a-select` 的 `show-search` 是**对已加载的本地候选做过滤** —— 要用它就得先
 * 把全量拉下来，而款号/客户/颜色会累积到数千条（docs/06 §10 开篇就是这个理由）。
 * 真正要的是"输入即搜"：每次输入把关键字发给后端（走 §9.5.2 的候选接口 +
 * `pg_trgm` 索引），只回来 ≤20 条。所以自己实现。
 *
 * ## 关键口径（每条都有测试）
 *
 * | 要求 | 口径 |
 * | --- | --- |
 * | 防抖 | 输入后 **300ms** 才发请求 |
 * | 键盘 | `↑/↓` 移动高亮、`Enter` 选中、`Esc` 关闭 |
 * | 回显 | **编码 + 名称**（`A001 夏季连衣裙`），禁止只显示编码 |
 * | 停用项 | 标红 + **禁止选中**（docs/05 §9.5.2「不可直接选中」） |
 * | 空态 | 「无匹配结果」+ 「＋ 新建」入口（allowCreate 时） |
 * | 候选上限 | 最多 20 条，超出提示「请继续输入关键字」 |
 * | 请求失败 | 「搜索失败，点此重试」，**禁止静默清空** |
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useDebounce } from '@/composables/useDebounce'
import type { OptionOut } from '@garment/shared'

/** 防抖时长（docs/06 §10 / 05 §9.5.3 明确 300ms）。 */
const DEBOUNCE_MS = 300
/** 候选硬上限（docs/05 §9.5.1「候选下拉强制 size ≤ 20」）。 */
const MAX_OPTIONS = 20

const props = withDefaults(
  defineProps<{
    /** 提交给后端的业务编码（不是 UUID，docs/05 §9.5.2）。 */
    modelValue: string | null
    /** 输入即搜。抛异常会被渲染成「搜索失败，点此重试」。 */
    fetchOptions: (keyword: string) => Promise<OptionOut[]>
    placeholder?: string
    /** 允许显示「＋ 新建」入口（该主数据允许新建时）。 */
    allowCreate?: boolean
    /** 新建入口文案，如 `＋ 用「{kw}」新建款号`。 */
    createText?: string
    disabled?: boolean
    /**
     * 已选项回显用：只有编码时也能显示名称（刷新页面后 `modelValue` 只有值）。
     *
     * 默认实现返回 `null`（拿不到名称就退回显示编码），而不是在 `withDefaults` 里写
     * `resolveLabel: undefined` —— `exactOptionalPropertyTypes` 下那会让 props 变成
     * `T | undefined` 而 tsc 报错。
     */
    resolveLabel?: (value: string) => Promise<OptionOut | null>
  }>(),
  {
    placeholder: '输入关键字搜索',
    allowCreate: false,
    createText: '',
    disabled: false,
    resolveLabel: async () => null,
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
  /** 用户点了「＋ 新建」——由页面决定跳到哪个新建表单。 */
  create: [keyword: string]
}>()

const keyword = ref('')
const options = ref<OptionOut[]>([])
const loading = ref(false)
const errorText = ref<string | null>(null)
/** 该提示是否来自「请求失败」—— 只有这种情况才给「重试」按钮（停用项提示重试无意义）。 */
const retryable = ref(false)
/** 是否已经拉过一次「默认候选」（`q` 为空 = 最近使用 / 默认排序，docs/05 §9.5.2）。 */
let initialLoaded = false
/**
 * 已选项的显示文本。
 *
 * ⚠️ 不能在收起面板时从 `props.modelValue` 反推文本：`select()` 里 emit 之后、
 *    父组件还没重新渲染，`props.modelValue` 仍是**旧值**。于是还原逻辑会算出空串，
 *    症状是「选中候选后输入框立刻空了」—— 而用户以为自己没选中。
 */
let selectedLabel: string | null = null
const open = ref(false)
const highlight = ref(0)
const rootEl = ref<HTMLElement | null>(null)

const { debounced: debouncedFetch, cancel: cancelFetch } = useDebounce((value: string) => {
  void runSearch(value)
}, DEBOUNCE_MS)

/** 有候选被截断时提示"继续输入关键字"，而不是让用户以为只有这些。 */
const truncated = computed(() => options.value.length >= MAX_OPTIONS)

const displayText = computed(() => {
  if (options.value.length === 1 && options.value[0]?.value === props.modelValue) {
    return optionLabel(options.value[0])
  }
  return keyword.value
})

const selectableIndexes = computed(() =>
  options.value.reduce<number[]>((acc, option, index) => {
    if (option.disabled !== true) acc.push(index)
    return acc
  }, []),
)

const hasSelectable = computed(() => selectableIndexes.value.length > 0)

/**
 * 候选项的显示文本。
 *
 * ⚠️ **`label` 里已经含编码，不要在前端再拼一遍**。后端 `OptionOut` 的构造就是
 *    `label=f"{style_no} {name}"`（见 `base/service.py` 的款号候选）——
 *    这是 docs/05 §9.5.2「回显必须是编码 + 名称」的落地位置。前端再拼一次会得到
 *    `0021 0021 夏季连衣裙`，多出来的编码看起来像脏数据。
 *
 * `sub` 是后端给的副文本（分类、围度、`已停用`），接在括号里；为空就不加括号，
 * 免得出现 `款号（）` 这种噪声。
 */
function optionLabel(option: OptionOut): string {
  const sub = option.sub
  return sub === null || sub === '' ? option.label : `${option.label}（${sub}）`
}

async function runSearch(value: string): Promise<void> {
  const query = value.trim()
  errorText.value = null
  retryable.value = false
  loading.value = true
  try {
    const result = await props.fetchOptions(query)
    // ⚠️ 只接受前 20 条并**显式提示**：静默截断会让用户以为"库里就这几条"，
    // 然后把不对的款号写进单据。
    options.value = result.slice(0, MAX_OPTIONS)
    // ⚠️ 高亮落在**第一个可选项**上。用 `nextSelectable(0, 1)` 会跳到第二项 ——
    //   那个函数把 `from` 当成"光标当前位置"，+1 自然落到下一项。
    //   症状是打开候选框直接按 Enter 选到了第二条，用户根本没看清选了什么。
    highlight.value = selectableIndexes.value[0] ?? 0
  } catch {
    // 请求失败**保留原候选**并给重试，不静默清空（docs/06 §10「状态」）：
    // 清空的话用户以为"没数据了"，很可能转头去手工录一份 —— 那是数据污染的起点。
    errorText.value = '搜索失败，点此重试'
    retryable.value = true
  } finally {
    loading.value = false
  }
}

function nextSelectable(from: number, step: number): number {
  const pool = selectableIndexes.value
  if (pool.length === 0) return 0
  let cursor = pool.indexOf(from)
  if (cursor === -1) cursor = step > 0 ? -1 : 0
  const next = (cursor + step + pool.length) % pool.length
  return pool[next] ?? 0
}

function moveHighlight(step: number): void {
  if (!open.value) {
    open.value = true
    return
  }
  highlight.value = nextSelectable(highlight.value, step)
}

function select(index: number): void {
  const option = options.value[index]
  if (option === undefined) return
  if (option.disabled === true) {
    // docs/05 §9.5.2：停用项"不可直接选中（需提示已停用，是否启用）"。
    // 这里**不改值**，只说清原因 —— 擅自替换成别的项比报错更危险。
    errorText.value = `「${optionLabel(option)}」已停用，请先启用后再选`
    retryable.value = false
    return
  }
  keyword.value = optionLabel(option)
  selectedLabel = keyword.value
  emit('update:modelValue', option.value)
  open.value = false
}

function onInput(event: Event): void {
  const value = (event.target as HTMLInputElement).value
  keyword.value = value
  open.value = true
  highlight.value = 0
  debouncedFetch(value)
}

function onFocus(): void {
  open.value = true
  // 首次聚焦就把默认候选拉出来，否则用户要点两次才有东西看。
  // ⚠️ 用 `initialLoaded` 而不是「候选为空」判断：`onInput` 也会把面板打开，
  //   `watch(open)` 随即 focus 输入框 → 又触发一次 onFocus → 多发一轮 `q=''` 的请求。
  //   症状是"打一个字却先来一屏默认候选"，且请求数翻倍。
  if (initialLoaded || loading.value) return
  initialLoaded = true
  void runSearch('')
}

function onKeydown(event: KeyboardEvent): void {
  if (props.disabled) return
  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault()
      moveHighlight(1)
      break
    case 'ArrowUp':
      event.preventDefault()
      moveHighlight(-1)
      break
    case 'Enter':
      // 必须是 `preventDefault`：不拦的话父级表单会把这个 Enter 当成"提交"，
      // 于是用户只是选个款号就把整张单据提交了。
      event.preventDefault()
      if (open.value && hasSelectable.value) select(highlight.value)
      break
    case 'Escape':
      open.value = false
      break
    default:
      break
  }
}

function onRetry(): void {
  void runSearch(keyword.value)
}

function onCreate(): void {
  const text = keyword.value.trim()
  if (text === '') return
  emit('create', text)
}

function onDocumentClick(event: MouseEvent): void {
  const target = event.target
  if (target instanceof Node && rootEl.value?.contains(target)) return
  open.value = false
}

watch(
  () => props.modelValue,
  async (value) => {
    if (value === null) {
      keyword.value = ''
      selectedLabel = null
      return
    }
    // 刷新页面后 store / 表单里只有编码，没有名称 —— 用 `resolveLabel` 补回显，
    // 否则用户看到的是一串编���，认不出自己选了什么。
    if (options.value.some((option) => option.value === value)) return
    try {
      const resolved = await props.resolveLabel(value)
      keyword.value = resolved === null ? value : optionLabel(resolved)
    } catch {
      // 拿不到名称就退回显示编码，不白屏 —— 用户至少还能确认自己选的是哪一条
      keyword.value = value
    }
    selectedLabel = keyword.value
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  // ⚠️ 卸载时取消待执行的防抖搜索，否则 300ms 后会对已卸载的组件发请求
  cancelFetch()
})

watch(open, (isOpen) => {
  // ⚠️ 这里**不要**顺手 `focus()` 输入框。`open` 只会被 focus / input 两个已经
  //   聚焦的入口置真，所以这个 focus 是多余的；而它会再触发一次 `onFocus`，
  //   多发一轮 `q=''` 的请求 —— 症状是"打一个字却先来一屏默认候选"，请求数翻倍。
  if (isOpen) {
    document.addEventListener('click', onDocumentClick)
  } else {
    document.removeEventListener('click', onDocumentClick)
    // 收起时把输入框还原成"当前已选项"，否则用户临时打了几个字又取消，
    // 界面上留着没提交过的关键字，看着像已经改了数据。
    // ⚠️ 优先用 `selectedLabel`：见它的注释 —— 这一刻 `props.modelValue` 还是旧值。
    keyword.value = selectedLabel ?? props.modelValue ?? ''
  }
})
</script>

<template>
  <div ref="rootEl" class="combo">
    <input
      class="combo-input"
      type="text"
      role="combobox"
      :aria-expanded="open"
      aria-autocomplete="list"
      :placeholder="placeholder"
      :disabled="disabled"
      :value="displayText"
      @input="onInput"
      @focus="onFocus"
      @keydown="onKeydown"
    />

    <div v-if="open" class="combo-panel" role="listbox">
      <div v-if="loading" class="combo-hint">搜索中…</div>

      <template v-else>
        <!--
          错误提示**与候选并存**：请求失败时保留上一次的候选（docs/06 §10「不得静默清空」）。
          清空的话用户会以为"库里没有这条数据"，很可能转头手工录一份 —— 那才是数据污染。
        -->
        <div v-if="errorText !== null" class="combo-error">
          <span>{{ errorText }}</span>
          <button v-if="retryable" type="button" class="combo-retry" @mousedown.prevent="onRetry">
            重试
          </button>
        </div>

        <div
          v-for="(option, index) in options"
          :key="option.value"
          class="combo-option"
          :class="{
            'is-active': index === highlight,
            'is-disabled': option.disabled === true,
          }"
          role="option"
          :aria-selected="index === highlight"
          :aria-disabled="option.disabled === true"
          @mousedown.prevent="select(index)"
          @mouseenter="option.disabled !== true && (highlight = index)"
        >
          <span class="combo-option-label">{{ optionLabel(option) }}</span>
          <span v-if="option.disabled === true" class="combo-option-flag">已停用</span>
        </div>

        <div v-if="truncated" class="combo-hint">候选较多，请继续输入关键字缩小范围</div>

        <div v-else-if="options.length === 0" class="combo-empty">
          <span>无匹配结果</span>
          <button
            v-if="allowCreate && keyword.trim() !== ''"
            type="button"
            class="combo-create"
            @mousedown.prevent="onCreate"
          >
            {{ createText === '' ? `＋ 用「${keyword.trim()}」新建` : createText }}
          </button>
        </div>

        <button
          v-if="allowCreate && options.length > 0 && keyword.trim() !== ''"
          type="button"
          class="combo-create"
          @mousedown.prevent="onCreate"
        >
          {{ createText === '' ? `＋ 用「${keyword.trim()}」新建` : createText }}
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.combo {
  position: relative;
  width: 100%;
}

.combo-input {
  width: 100%;
  height: 24px;
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-bg-card);
  color: var(--color-text);
  font-size: var(--font-size-base);
}

.combo-input:focus {
  border-color: var(--color-primary);
  outline: none;
  box-shadow: 0 0 0 2px var(--color-primary-bg);
}

.combo-input:disabled {
  background: var(--color-bg);
  color: var(--color-text-third);
  cursor: not-allowed;
}

.combo-panel {
  position: absolute;
  top: calc(100% + 2px);
  left: 0;
  right: 0;
  z-index: var(--z-dropdown);
  max-height: 320px;
  overflow-y: auto;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-card);
}

.combo-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  font-size: var(--font-size-base);
  cursor: pointer;
}

.combo-option.is-active {
  background: var(--color-primary-bg);
}

/* 停用项标红（docs/05 §9.5.2）且**不显示手型光标** —— 它点不动 */
.combo-option.is-disabled {
  color: var(--color-danger);
  cursor: not-allowed;
}

.combo-option-flag {
  font-size: var(--font-size-sm);
  color: var(--color-danger);
}

.combo-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  font-size: var(--font-size-sm);
  color: var(--color-danger);
  border-bottom: 1px solid var(--color-border);
}

.combo-retry {
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-size: var(--font-size-sm);
  cursor: pointer;
  padding: 0;
}

.combo-hint,
.combo-empty {
  padding: var(--space-2) var(--space-3);
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.combo-empty {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.combo-create {
  display: block;
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: none;
  border-top: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-primary);
  font-size: var(--font-size-base);
  text-align: left;
  cursor: pointer;
}

.combo-create:hover {
  background: var(--color-primary-bg);
}
</style>
