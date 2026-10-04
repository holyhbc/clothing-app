<script setup lang="ts">
/**
 * 码表的**有序尺码成员**编辑器（`size_group_items`，docs/modules/01 §3.6.3）。
 *
 * ## 为什么不能用逗号分隔的文本框
 *
 * `items` 是有序关联（`sort_order` 参与「一键带出款号尺码」的顺序），而逗号分隔的
 * 字符串既不能表达顺序调整，也无法显示每个成员的中文名（`XL(170/92A)`）—— 用户
 * 只能对着 `M,L,XL,2XL` 猜哪个是哪个。04 §8 也明确禁止把可查询字段塞进字符串。
 *
 * ## 为什么用 Combo 而不是下拉
 *
 * 尺码字典会累积到几百条（docs/06 §10 的理由），而且**候选的 value 必须是
 * `size_id`（UUID）而不是尺码码** —— `SizeGroupItemIn.size_id` 是外键，
 * 提交尺码码会被后端 `10001` 拒。所以走 `optionsById`（列表接口取行，自己映射 id）。
 */
import { computed, ref, watch } from 'vue'
import { Button, Space, Tag, message } from 'ant-design-vue'
import Combo from '@/components/Combo.vue'
import { baseApi } from '@/api/base'
import type { OptionOut } from '@garment/shared'

export interface SizeGroupItem {
  /** `size_groups.size_group_items.size_id`（UUID，不是尺码码）。 */
  size_id: string
  /** 组内顺序，**从 1 起**（与内置 seed 的 1..4 一致，便于人工核对）。 */
  sort_order: number
}

const props = defineProps<{
  modelValue: readonly SizeGroupItem[]
  /** 限定候选的尺码类（码表本身就是一个尺码类 + 一套有序尺码）。 */
  sizeClass?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [items: SizeGroupItem[]] }>()

defineOptions({ name: 'OrderedSizeItems' })

const pending = ref<string | null>(null)

/** 候选搜索。⚠️ `size` 传 `size_class` 才不会选到另一个类目的同名码（`L` 两个类目都有）。 */
function searchSizes(keyword: string): Promise<OptionOut[]> {
  const extra = props.sizeClass === undefined ? {} : { size_class: props.sizeClass }
  return baseApi.sizes.optionsById(keyword, extra)
}

/** 反查已选项的显示文本（刷新页面后只剩 id）。 */
async function resolveSize(sizeId: string): Promise<OptionOut | null> {
  const found = (await baseApi.sizes.optionsById()).find((item) => item.value === sizeId)
  return found ?? { value: sizeId, label: sizeId, sub: null, disabled: false }
}

const selectedIds = computed(() => props.modelValue.map((item) => item.size_id))

const labelCache = ref<Record<string, string>>({})

/** 每个成员的中文名。取不到就显示 id 前 8 位 —— 空白与「重复」都更难排查。 */
async function loadLabels(): Promise<void> {
  const missing = props.modelValue.filter((item) => labelCache.value[item.size_id] === undefined)
  if (missing.length === 0) return
  const entries = await Promise.all(
    missing.map(async (item) => {
      const option = await resolveSize(item.size_id)
      return [item.size_id, option?.label ?? item.size_id.slice(0, 8)] as const
    }),
  )
  labelCache.value = { ...labelCache.value, ...Object.fromEntries(entries) }
}

function labelOf(sizeId: string): string {
  return labelCache.value[sizeId] ?? sizeId.slice(0, 8)
}

function commit(next: readonly SizeGroupItem[]): void {
  emit('update:modelValue', next.map((item, index) => ({ size_id: item.size_id, sort_order: index + 1 })))
}

function onPick(value: string | null): void {
  if (value === null || value === '') return
  if (selectedIds.value.includes(value)) {
    void message.warning('这个尺码已经在列表里了')
    pending.value = null
    return
  }
  // 尺码上限：modules/01 §5.3 的码表最多 100 个成员（对齐比例接口的口径）
  if (props.modelValue.length >= 100) {
    void message.warning('一个码表最多 100 个尺码')
    pending.value = null
    return
  }
  commit([...props.modelValue, { size_id: value, sort_order: props.modelValue.length + 1 }])
  labelCache.value = { ...labelCache.value, [value]: labelOf(value) }
  pending.value = null
}

function move(index: number, step: number): void {
  const target = index + step
  if (target < 0 || target >= props.modelValue.length) return
  const next = [...props.modelValue]
  const [item] = next.splice(index, 1)
  if (item === undefined) return
  next.splice(target, 0, item)
  commit(next)
}

function remove(index: number): void {
  commit(props.modelValue.filter((_, position) => position !== index))
}

// ⚠️ 用 `watch` 而不是 setup 里直接调一次：编辑态的成员是**加载之后**才有的，
//    setup 时那会儿 modelValue 还是空的，标签就永远补不回来。
watch(() => props.modelValue, () => void loadLabels(), { immediate: true, deep: true })
</script>

<template>
  <div class="ordered-items">
    <Space direction="vertical" size="small" style="width: 100%">
      <Combo
        :model-value="pending"
        :fetch-options="searchSizes"
        :resolve-label="resolveSize"
        placeholder="输入尺码码或实际尺码搜索后添加…"
        @update:model-value="onPick"
      />

      <p v-if="modelValue.length === 0" class="ordered-hint">
        还没有成员。码表至少要有 1 个尺码（后端 `10001` 会拒空码表）。
      </p>

      <ul v-else class="ordered-list">
        <li v-for="(item, index) in modelValue" :key="item.size_id" class="ordered-row">
          <Tag>{{ index + 1 }}</Tag>
          <span class="ordered-label">{{ labelOf(item.size_id) }}</span>
          <Space :size="4">
            <!--
              顺序调整用「上移 / 下移」而不是拖拽：拖拽在 1366×768 的表格密度下
              命中区太小（工厂用户戴手套点不准），而上下移动一个按钮一次到位，
              也便于单测断言。
            -->
            <Button size="small" :disabled="index === 0" @click="move(index, -1)">上移</Button>
            <Button
              size="small"
              :disabled="index === modelValue.length - 1"
              @click="move(index, 1)"
            >
              下移
            </Button>
            <Button size="small" danger @click="remove(index)">移除</Button>
          </Space>
        </li>
      </ul>
    </Space>
  </div>
</template>

<style scoped>
.ordered-hint {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.ordered-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.ordered-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) 0;
  border-bottom: 1px solid var(--color-border);
}

.ordered-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
}
</style>