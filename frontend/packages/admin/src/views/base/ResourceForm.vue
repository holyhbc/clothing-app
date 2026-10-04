<script setup lang="ts">
/**
 * 基础资料新建 / 编辑表单（九个资源共用；docs/06 §2.4）。
 *
 * ## 必填、类型、默认值全部来自后端契约
 *
 * 表单长什么样由注册表决定（中文名 / 控件），而**哪些字段必填、是什么类型、
 * 默认值是多少**来自 `BASE_DICT_CONTRACT`（T-WEB-005 缺口③）。前端不写第二份：
 * 后端给某个模型加一个必填字段，重跑生成器后表单立刻多一个红星。
 *
 * ## 编辑态的两条硬规则
 *
 * 1. **编码列只读**（§4.4：编码被历史单据按字符串引用，改号即改历史指向）。
 * 2. **只提交 `patch` 模型里有的字段**。`WorkshopGroupPatch` 没有 `workshop_id`
 *    —— 组别不能换车间。若按"用户改了什么就发什么"提交，后端 `extra=forbid`
 *    会回 `10001`，而界面上看不出任何异常。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert,
  Button,
  Card,
  Form,
  FormItem,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Typography,
  message,
} from 'ant-design-vue'
import type { FormInstance } from 'ant-design-vue'
import type { Rule } from 'ant-design-vue/es/form'
import { BASE_DICT_CONTRACT } from '@garment/shared'
import type { BaseDictField, BaseDictRow } from '@garment/shared'
import { baseApi, editableFieldNames, requiredFields, resourceDecl } from '@/api/base'
import type { BaseFormField, BaseResourceApi, BaseResourceDecl, RegistryKey } from '@/api/base'
import Combo from '@/components/Combo.vue'
import OrderedSizeItems from '@/views/base/OrderedSizeItems.vue'
import type { SizeGroupItem } from '@/views/base/OrderedSizeItems.vue'

const props = defineProps<{ resourceKey: RegistryKey }>()

defineOptions({ name: 'BaseResourceForm' })

const resourceKey = props.resourceKey
const decl: BaseResourceDecl = resourceDecl(resourceKey)
const contract = BASE_DICT_CONTRACT[resourceKey]
const api: BaseResourceApi = baseApi[resourceKey]

const route = useRoute()
const router = useRouter()

/** 路径参数是**业务编码**（`/colors/{color_code}`），不是 UUID（§4.4）。 */
const code = computed(() => {
  const raw = route.params['code']
  return typeof raw === 'string' && raw !== '' ? raw : null
})
const isEdit = computed(() => code.value !== null)

const loading = ref(false)
const saving = ref(false)
const loadError = ref<string | null>(null)
const submitError = ref<string | null>(null)
/** 乐观锁冲突（`10003`）：要提示刷新，而不是让用户再点一次保存。 */
const conflicted = ref(false)
const current = ref<BaseDictRow | null>(null)

/** 表单值。键来自契约的 create 字段，值类型按契约（`unknown` 是刻意的）。 */
const form = reactive<Record<string, unknown>>({})
const formRef = ref<FormInstance | null>(null)

const required = requiredFields(resourceKey)
const editable = editableFieldNames(resourceKey)

/**
 * 契约里的字段定义（类型 / 默认值 / 枚举取值）。
 *
 * ⚠️ 显式标注返回类型 `BaseDictField | undefined`：契约是 `as const`，不标注的话
 *    返回值是**该资源字段的字面量联合**，于是 `spec.default` / `spec.values`
 *    在没写这两个可选属性的字段上报 TS2339 —— 而它们明明是可选的。
 */
function fieldSpec(name: string): BaseDictField | undefined {
  return contract.create.fields.find((item) => item.name === name)
}

/** 编辑态某个字段是否可改。编码列与「建后不可改」的字段一律只读。 */
function isEditable(name: string): boolean {
  if (!isEdit.value) return true
  if (name === contract.codeColumn) return false
  return editable.has(name)
}

/** 只读原因写进 tooltip —— 灰一个输入框而不说为什么，用户会以为是坏了。 */
function disabledReason(name: string): string {
  if (name === contract.codeColumn) return '编码建后不可改（历史单据按它引用）'
  return '该字段建后不可改'
}

/** 表单初值：契约默认值优先，其次按类型给零值。 */
function initialValue(name: string): unknown {
  const spec = fieldSpec(name)
  if (spec === undefined) return ''
  if (spec.default !== undefined) return spec.default
  switch (spec.type) {
    case 'bool':
      return false
    case 'int':
    case 'decimal':
      return null
    case 'list':
      return []
    case 'uuid':
      return null
    default:
      return ''
  }
}

function resetForm(): void {
  for (const key of Object.keys(form)) delete form[key]
  for (const item of decl.formFields) {
    form[item.name] = item.name === 'items' ? [] : initialValue(item.name)
  }
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    if (code.value === null) {
      resetForm()
      return
    }
    const row = await api.get(code.value)
    current.value = row
    resetForm()
    for (const item of decl.formFields) {
      const value = (row as unknown as Record<string, unknown>)[item.name]
      // ⚠️ `null` 归一成"空"而不是原样塞进去：InputNumber 收到 null 会显示成 NaN，
      //    而后端语义就是"没填"。
      if (value === null || value === undefined) continue
      form[item.name] = item.name === 'items' ? [] : value
    }
  } catch (caught) {
    loadError.value = caught instanceof Error ? caught.message : '加载失败，请返回列表重试'
  } finally {
    loading.value = false
  }
}

/**
 * 校验规则**由契约生成**。
 *
 * ⚠️ `validateTrigger: 'blur'` 而不是 change（docs/06 §2.4）：change 会在用户刚敲下
 *    第一个字符就标红，而此时他很可能只是还没打完。
 */
const rules = computed<Record<string, Rule | Rule[]>>(() => {
  const result: Record<string, Rule | Rule[]> = {}
  for (const item of decl.formFields) {
    result[item.name] = required.includes(item.name)
      ? [
          {
            required: true,
            // 列表字段（码表成员）单独一句：空数组不是"没填"而是"没成员"
            message:
              item.name === 'items' ? '至少添加 1 个尺码成员' : `请填写${item.label}`,
            trigger: 'blur',
          },
        ]
      : []
  }
  return result
})

/** 提交体：只保留契约里有的字段，并剔掉空值（PATCH 的部分更新语义）。 */
function buildPayload(): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const item of decl.formFields) {
    if (isEdit.value && !editable.has(item.name)) continue
    const value = form[item.name]
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value) && value.length === 0) continue
    // 码表成员：存成后端要的 `{size_id, sort_order}` 数组
    payload[item.name] = item.name === 'items' ? (value as SizeGroupItem[]) : value
  }
  if (isEdit.value) {
    // ⚠️ `version` 必传（§4.4）：不传后端回 `10001`，传错回 `10003`
    payload['version'] = current.value?.version ?? 1
  }
  return payload
}

async function onSubmit(): Promise<void> {
  if (saving.value) return
  saving.value = true
  submitError.value = null
  conflicted.value = false
  try {
    await formRef.value?.validate()
  } catch {
    // 校验没过：antd 已把红字标在字段下方并滚动到第一个错误（docs/06 §2.4）
    saving.value = false
    return
  }
  try {
    if (code.value === null) {
      await api.create(buildPayload())
      void message.success(`已新建${decl.itemLabel}`)
    } else {
      await api.patch(code.value, buildPayload())
      void message.success('已保存')
    }
    await router.replace({ name: `base-${resourceKey}` })
  } catch (caught) {
    // ⚠️ 失败**保留用户输入**（docs/06 §2.4）：清空的话他刚填的两分钟就没了
    if (caught instanceof Error && 'code' in caught && (caught as { code: number }).code === 10003) {
      conflicted.value = true
      submitError.value = '这条记录已被他人修改，直接保存会覆盖对方的结果。请先刷新看最新内容。'
    } else {
      submitError.value = caught instanceof Error ? caught.message : '保存失败，请重试'
    }
  } finally {
    saving.value = false
  }
}

async function onReload(): Promise<void> {
  await load()
  void message.info('已刷新为最新内容，请确认后重新提交')
}

// ------------------------------------------------------------------ 字段渲染

/** Combo 类字段的候选来源。⚠️ `uuid` 字段要的是 id 而不是业务编码。 */
function comboSource(field: BaseFormField): RegistryKey | null {
  return field.name === 'workshop_id' ? 'workshops' : null
}

function searchCombo(key: RegistryKey, keyword: string) {
  return baseApi[key].optionsById(keyword)
}

async function resolveComboValue(key: RegistryKey, value: string) {
  const found = (await baseApi[key].optionsById()).find((item) => item.value === value)
  return found ?? null
}

function enumOptions(field: BaseFormField): { value: string; label: string }[] {
  const spec = fieldSpec(field.name)
  return (spec?.values ?? []).map((value) => ({
    value,
    label: field.options?.[value] ?? value,
  }))
}

/** 小数位：docs/06 §2.4「数量精度 3、金额 4、工价 6」。这里只有"一扎件数"一类数量。 */
function precisionOf(field: BaseFormField): number {
  return field.name === 'default_bundle_qty' ? 3 : 4
}

/*
 * ⚠️ 为什么下面这几个小函数，而不是 `v-model:value="form[field.name] as string"`：
 *   `v-model` 的目标必须是**可赋值表达式**，而 `x as T` 不是；而 `form[field.name]`
 *   的类型是 `unknown`，直接绑到 Select / InputNumber 的 prop 上又过不了类型检查。
 *   在这里收口一次，模板里只写 `onXxx(field, $event)`，两头都干净。
 *
 * ⚠️ **必须监听 `update:value` / `update:checked`，不能监听 `change`**：
 *   antd-vue 的 Input 在**每次输入**时发 `update:value`，而 `change` 只在原生
 *   change（即失焦）时才发。绑 `@change` 的症状极其隐蔽 —— 界面完全正常、校验也
 *   过，唯独用户输入的值**从来没进过表单模型**，点保存时报"请填写 XXX"。
 *   （本卡写组件测试时真的踩到了：Test Utils 的 `setValue` 只触发 `input`。）
 */
function textValue(field: BaseFormField): string {
  const value = form[field.name]
  return value === null || value === undefined ? '' : String(value)
}

/**
 * InputNumber 的值。
 *
 * ⚠️ 空值返回**空串**而不是 `null`：antd 的 `ValueType` 是 `string | number`，
 *    而 `exactOptionalPropertyTypes` 下把 `undefined` 传给可选 prop 会直接报错。
 *    空串在 InputNumber 里就是"没填"的显示态。
 */
function numberValue(field: BaseFormField): string | number {
  const raw = form[field.name]
  if (raw === null || raw === undefined || raw === '') return ''
  const value = Number(raw)
  return Number.isNaN(value) ? '' : value
}

function boolValue(field: BaseFormField): boolean {
  return form[field.name] === true
}

function onText(field: BaseFormField, value: string): void {
  form[field.name] = value
}

function onNumber(field: BaseFormField, value: number | null): void {
  form[field.name] = value
}

/** InputNumber 清空时给的是空串 —— 归一成 `null`，让 `buildPayload` 当成"不提交"。 */

function onBool(field: BaseFormField, value: boolean): void {
  form[field.name] = value
}

function onEnum(field: BaseFormField, value: unknown): void {
  // ⚠️ 清空（allow-clear）给的是 `undefined`：留成 `undefined` 才能被 `buildPayload`
  //    当成"不提交"，否则会发一个 `null` 过去 —— PATCH 里 null 的语义是"不改"，
  //    但 create 里 null 会与"没填"混淆。
  form[field.name] = value === undefined || value === null || value === '' ? undefined : value
}

function onUuid(field: BaseFormField, value: string | null): void {
  form[field.name] = value
}

function onItems(value: SizeGroupItem[]): void {
  form['items'] = value
}

const itemsOf = computed<SizeGroupItem[]>(() => (form['items'] as SizeGroupItem[]) ?? [])
const sizeClassOf = computed(() => String(form['size_class'] ?? ''))

onMounted(() => {
  void load()
})
</script>

<template>
  <Card :bordered="false" class="form-card">
    <Space direction="vertical" size="middle" style="width: 100%">
      <Typography.Title :level="4" style="margin: 0">
        {{ isEdit ? `编辑${decl.itemLabel} ${code ?? ''}` : `新建${decl.itemLabel}` }}
      </Typography.Title>

      <Alert v-if="loading" type="info" message="加载中…" show-icon />
      <Alert v-if="loadError" type="error" :message="loadError" show-icon />
      <Alert v-if="submitError" :type="conflicted ? 'warning' : 'error'" show-icon>
        <template #message>{{ submitError }}</template>
        <template v-if="conflicted" #action>
          <Button size="small" @click="onReload">刷新为最新内容</Button>
        </template>
      </Alert>

      <!--
        ⚠️ 码表成员在**编辑态不渲染**：后端 `DictOut` 里没有 `items` 字段，
        也就是说当前接口**读不回**已有成员。若这里渲染一个空编辑器，用户一保存
        就用空数组全量替换掉了整张码表（`replace_size_group_items` 是 delete + 批量
        insert）。所以编辑态明确说明"不动成员"，要调整得等后端补回显接口。
      -->
      <Alert
        v-if="isEdit && decl.formFields.some((item) => item.name === 'items')"
        type="info"
        show-icon
        message="保存时不会改动尺码成员"
        description="当前版本的取详情接口不回显码表成员，编辑态改动它有清空成员的风险。成员顺序请在新建码表时确定。"
      />

      <Form
        ref="formRef"
        :model="form"
        :rules="rules"
        layout="horizontal"
        :label-col="{ span: 6 }"
        :wrapper-col="{ span: 16 }"
        :validate-trigger="'blur'"
      >
        <FormItem
          v-for="field in decl.formFields"
          :key="field.name"
          :name="field.name"
          :label="field.label"
        >
          <!--
            码表成员：有序编辑器（不是文本框，理由见 OrderedSizeItems 的注释）。
            ⚠️ **编辑态整个不渲染** —— 后端 `DictOut` 没有 `items` 字段，接口读不回
            已有成员，渲染一个空编辑器等于给用户一个"一保存就清空全表"的陷阱。
          -->
          <OrderedSizeItems
            v-if="field.name === 'items' && !isEdit"
            :model-value="itemsOf"
            :size-class="sizeClassOf"
            @update:model-value="onItems"
          />
          <span v-else-if="field.name === 'items'" class="field-hint">
            成员保持不变（取详情接口不回显成员，见上方说明）
          </span>

          <!-- 布尔：Switch（`is_piecework`）。默认值取自契约，不在前端写死 -->
          <Switch
            v-else-if="fieldSpec(field.name)?.type === 'bool'"
            :checked="boolValue(field)"
            :disabled="!isEditable(field.name)"
            @update:checked="(checked: boolean | string | number) => onBool(field, checked === true)"
          />

          <!-- 枚举：Select + 中文映射（docs/06 §1「前端只做中文映射」） -->
          <Select
            v-else-if="fieldSpec(field.name)?.type === 'enum'"
            :value="textValue(field)"
            :options="enumOptions(field)"
            :disabled="!isEditable(field.name)"
            allow-clear
            :placeholder="field.placeholder ?? `请选择${field.label}`"
            @update:value="(value: unknown) => onEnum(field, value)"
          />

          <!--
            外键（workshop_id）：Combo + 候选接口（docs/06 §10 强制）。
            ⚠️ 提交的是 **UUID** 而不是车间编码 —— `WorkshopGroupCreate.workshop_id`
            是 UUID，提交 `C01` 会被后端 `10001` 拒。
          -->
          <Combo
            v-else-if="fieldSpec(field.name)?.type === 'uuid'"
            :model-value="textValue(field) || null"
            :fetch-options="(keyword: string) => searchCombo(comboSource(field) ?? 'workshops', keyword)"
            :resolve-label="
              (value: string) => resolveComboValue(comboSource(field) ?? 'workshops', value)
            "
            :disabled="!isEditable(field.name)"
            :placeholder="field.placeholder ?? `输入关键字搜索${field.label}`"
            @update:model-value="(value: string | null) => onUuid(field, value)"
          />

          <!--
            数字：InputNumber（docs/06 §2.4「禁止 `type="number"` 原生」）。
            ⚠️ decimal 的默认值在契约里是**字符串**（docs/05 §3），InputNumber 收到
            字符串会显示成 NaN —— 所以取值走 `numberValue()` 统一转数字。
          -->
          <InputNumber
            v-else-if="
              fieldSpec(field.name)?.type === 'int' || fieldSpec(field.name)?.type === 'decimal'
            "
            :value="numberValue(field)"
            :precision="fieldSpec(field.name)?.type === 'int' ? 0 : precisionOf(field)"
            :min="0"
            :disabled="!isEditable(field.name)"
            :placeholder="field.placeholder ?? field.label"
            style="width: 100%"
            @update:value="(value: string | number | null) => onNumber(field, value === null ? null : Number(value))"
          />

          <Input
            v-else
            :value="textValue(field)"
            :disabled="!isEditable(field.name)"
            :placeholder="field.placeholder ?? field.label"
            allow-clear
            @update:value="(value: string) => onText(field, value)"
          />

          <template v-if="!isEditable(field.name)" #extra>
            <span class="field-hint">{{ disabledReason(field.name) }}</span>
          </template>
        </FormItem>

        <FormItem :wrapper-col="{ offset: 6, span: 16 }">
          <Space>
            <Button
              v-can="isEdit ? contract.permissions.update : contract.permissions.create"
              type="primary"
              :loading="saving"
              @click="onSubmit"
            >
              保存
            </Button>
            <Button @click="router.back()">取消</Button>
          </Space>
        </FormItem>
      </Form>
    </Space>
  </Card>
</template>

<style scoped>
.form-card {
  box-shadow: var(--shadow-card);
}

.field-hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}
</style>