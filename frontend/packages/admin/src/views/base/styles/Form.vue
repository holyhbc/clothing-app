<script setup lang="ts">
/**
 * 款号新建 / 编辑（docs/06 §2.4）。
 *
 * ## 货号**由用户自定义**（Q-P0-04）
 *
 * 「生成建议号」按钮调的是 `GET /styles/suggested-no` —— 只读、**不建档**。
 * ⚠️ 它会**消耗一个序号**：用户点了又改掉，那个号就空了一格。空一号不影响唯一性
 *    （唯一索引在 `styles` 上），只影响"建议号跳号"，所以这个代价可以接受 ——
 *    但文案要说清，免得用户以为"点了就等于占了这个号"。
 *
 * ⚠️ **不能复用 `POST /styles?suggest_style_no=true`**：那个端点会真的建档，
 *    按钮每点一次就多一个款号。
 *
 * ## 编辑态的硬规则
 *
 * `style_no` **不可改**（§4.4）：历史单据按字符串冗余存它，改号等于让所有历史单据
 * 指向一个不存在的款号。所以它在编辑态**只读**，且后端 `StylePatch` 里根本没有这个字段
 * （发了也是 `extra=forbid` 的 `10001`）。
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
import { PERM } from '@garment/shared'
import type { OptionOut, StyleDetailOut } from '@garment/shared'
import { baseApi } from '@/api/base'
import {
  createStyle,
  getStyle,
  patchStyle,
  searchCustomerOptionsById,
  suggestStyleNo,
} from '@/api/styles'
import Combo from '@/components/Combo.vue'

defineOptions({ name: 'StyleForm' })

const route = useRoute()
const router = useRouter()

/** 路径参数是**业务编码**（货号），不是 UUID（docs/modules/01 §4.4）。 */
const styleNo = computed(() => {
  const raw = route.params['styleNo']
  return typeof raw === 'string' && raw !== '' ? raw : null
})
const isEdit = computed(() => styleNo.value !== null)

const formRef = ref<FormInstance | null>(null)
const loading = ref(false)
const saving = ref(false)
const loadError = ref<string | null>(null)
const submitError = ref<string | null>(null)
/** 乐观锁冲突（`10003`）：提示刷新，而不是让用户再点一次保存（那还是冲突）。 */
const conflicted = ref(false)
/** 当前详情（`version` 与回显都要从这里取）。 */
const current = ref<StyleDetailOut['style'] | null>(null)

const form = reactive({
  style_no: '',
  name: '',
  category_id: null as string | null,
  customer_id: null as string | null,
  customer_style_no: '',
  merchandiser_id: null as string | null,
  bulk_qty: null as number | null,
  is_active: true,
})

/** 分类候选（**必填**，B-CAT-02）。 */
const categoryOptions = ref<OptionOut[]>([])

async function loadCategoryOptions(): Promise<void> {
  try {
    categoryOptions.value = await baseApi['product-categories'].optionsById()
  } catch {
    categoryOptions.value = []
  }
}

function searchCustomers(keyword: string): Promise<OptionOut[]> {
  return searchCustomerOptionsById(keyword)
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    if (styleNo.value === null) return
    const detail = await getStyle(styleNo.value)
    current.value = detail.style
    form.style_no = detail.style.style_no
    form.name = detail.style.name
    form.category_id = detail.style.category_id
    // ⚠️ 可选字段要 `?? null`：`StyleListOut.customer_id` 是可选的
    form.customer_id = detail.style.customer_id ?? null
    // ⚠️ 这两个字段曾经取不到（`StyleListOut` 不带它们），编辑态一直留空 ——
    //    空 = 不改，于是「打开款号、看清大货数量、改个款名保存」会把大货数量
    //    **静默留在旧值**上，而界面上一个空框看起来像「没填」。后端补齐字段后这里
    //    改成回显，否则用户第一次看到大货数量就是空白的
    form.customer_style_no = detail.style.customer_style_no ?? ''
    form.bulk_qty = detail.style.bulk_qty ?? null
    form.is_active = detail.style.is_active
    form.merchandiser_id = detail.style.merchandiser_id ?? null
  } catch (caught) {
    loadError.value = caught instanceof Error ? caught.message : '加载款号失败，请返回列表重试'
  } finally {
    loading.value = false
  }
}

/** 建议号：只读端点，不建档。 */
const suggesting = ref(false)

async function onSuggest(): Promise<void> {
  if (suggesting.value) return
  suggesting.value = true
  try {
    const suggestion = await suggestStyleNo(form.customer_id)
    form.style_no = suggestion.style_no
    // ⚠️ 回填后**不锁定**输入框：Q-P0-04 的口径是「只作参考，用户输入一律优先」。
    //   提示要说清"消耗了一个序号"，否则用户会以为这个号已经占住了。
    void message.info(`建议号 ${suggestion.style_no}（仅供参考，可直接改；该序号已被占用）`)
  } catch (caught) {
    void message.error(caught instanceof Error ? caught.message : '生成建议号失败，请手动输入货号')
  } finally {
    suggesting.value = false
  }
}

const rules = computed<Record<string, Rule | Rule[]>>(() => ({
  style_no: [
    {
      required: true,
      // ⚠️ **不做格式正则校验**：modules/01 §2 R1 与 Q-P0-04 冲突（"用户自定义"与
      //    "必须匹配 {前缀}-{年份}-{4位}"无法同时成立），后端也只做「去空白 + 转大写 +
      //    长度上限」。规范给新口径前不许发明正则（AGENTS.md §2.3）。
      message: '请填写货号（工厂自己的编号，由你决定格式）',
      trigger: 'blur',
    },
  ],
  name: [{ required: true, message: '请填写款名', trigger: 'blur' }],
  // 分类必填（B-CAT-02）：款号用于「按分类算裁剪工资」，缺了分类算不出工资
  category_id: [{ required: true, message: '请选择商品分类', trigger: 'blur' }],
}))

async function onSubmit(): Promise<void> {
  if (saving.value) return
  saving.value = true
  submitError.value = null
  conflicted.value = false
  try {
    await formRef.value?.validate()
  } catch {
    saving.value = false
    return
  }
  const payload = {
    // 后端会 `strip() + upper()` 归一（modules/01 §2 R1），这里也 trim 一下，
    // 免得用户输入的空格进到日志里
    style_no: form.style_no.trim(),
    name: form.name.trim(),
    category_id: form.category_id as string,
    customer_id: form.customer_id,
    customer_style_no: form.customer_style_no.trim() === '' ? null : form.customer_style_no.trim(),
    bulk_qty: form.bulk_qty,
    // ⚠️ 必传（生成类型里是 `boolean` 而非可选）：建议号已经由**独立只读端点**给了，
    //    这里传 false 免得服务端白取一个序号 —— 那个序号会被消耗掉。
    suggest_style_no: false,
    // ⚠️ **不属于建档体**：`StyleCreate` 没有 `is_active`（建档一律启用），
    //    放进 payload 会被 `extra="forbid"` 拒成 `10001`。停用走独立端点（要填原因）。
    //    它只在编辑时作为 PATCH 的字段，见下面 `editing` 那段。
    is_active: form.is_active as boolean,
  }
  const editing = styleNo.value !== null
  try {
    if (styleNo.value === null) {
      await createStyle(payload)
      void message.success('已建档')
    } else {
      // ⚠️ **不带 `style_no`**：它不可改，StylePatch 里也没有这个字段
      await patchStyle(styleNo.value, {
        name: payload.name,
        category_id: payload.category_id,
        customer_id: payload.customer_id,
        customer_style_no: payload.customer_style_no,
        bulk_qty: payload.bulk_qty,
        // `is_active` 在编辑态才发（见上面的注释）
        ...(editing ? { is_active: payload.is_active } : {}),
        version: current.value?.version ?? 1,
      })
      void message.success('已保存')
    }
    await router.replace({ name: 'base-styles' })
  } catch (caught) {
    if (
      caught instanceof Error &&
      'code' in caught &&
      (caught as { code: number }).code === 10003
    ) {
      conflicted.value = true
      submitError.value = '这条款号已被他人修改，直接保存会覆盖对方的结果。请先刷新看最新内容。'
    } else {
      // 失败**保留输入**（docs/06 §2.4）：清空的话用户填的几分钟就没了
      submitError.value = caught instanceof Error ? caught.message : '保存失败，请重试'
    }
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
  void loadCategoryOptions()
})
</script>

<template>
  <Card :bordered="false" class="form-card">
    <Space direction="vertical" size="middle" style="width: 100%">
      <Typography.Title :level="4" style="margin: 0">
        {{ isEdit ? `编辑款号 ${styleNo ?? ''}` : '新建款号' }}
      </Typography.Title>

      <Alert v-if="loadError" type="error" :message="loadError" show-icon />
      <Alert v-if="submitError" :type="conflicted ? 'warning' : 'error'" show-icon>
        <template #message>{{ submitError }}</template>
        <template v-if="conflicted" #action>
          <Button size="small" @click="load">刷新为最新内容</Button>
        </template>
      </Alert>

      <Alert
        type="info"
        show-icon
        message="货号由你自己填"
        description="系统只给一个「建议号」供参考，不是强制的。款号 = 工厂自己的编号；客户那边的货号填在下面的「客户货号备注」里。"
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
        <FormItem name="style_no" label="货号">
          <Space.Compact style="width: 100%">
            <Input
              v-model:value="form.style_no"
              :disabled="isEdit"
              :placeholder="isEdit ? '货号建后不可改' : '如 HB-2026-0001（自己决定格式）'"
              allow-clear
            />
            <!--
              「生成建议号」**只在新建态**出现：编辑态货号不可改，给一个填不了结果的
              按钮毫无意义（点了要么没反应、要么报错）。
            -->
            <Button
              v-if="!isEdit"
              v-can="PERM.BASE_CREATE"
              :loading="suggesting"
              @click="onSuggest"
            >
              生成建议号
            </Button>
          </Space.Compact>
        </FormItem>

        <FormItem name="name" label="款名">
          <Input v-model:value="form.name" placeholder="如 全棉圆领 T 恤" allow-clear />
        </FormItem>

        <FormItem name="category_id" label="商品分类">
          <!--
            分类候选是内置的 6 个（套装 / 单衣 / 单裤 / 棉毛 / 背心 / 打底裤），
            数量固定且很小，所以用 Select 而不是 Combo —— Combo 是给会累积到几千条的
            主数据用的（docs/06 §10 开篇就是这个理由）。
          -->
          <Select
            :value="form.category_id ?? undefined"
            :options="
              categoryOptions.map((option) => ({ value: option.value, label: option.label }))
            "
            placeholder="必填 —— 裁剪工资按分类算"
            allow-clear
            @update:value="
              (value: unknown) => (form.category_id = typeof value === 'string' ? value : null)
            "
          />
        </FormItem>

        <FormItem name="customer_id" label="归属客户">
          <!--
            ⚠️ 客户**没有页面**（docs/12 L-063），所以用 Combo 的输入即搜而不是跳页。
            `value` 是 UUID（`styles.customer_id` 是 uuid 列）。
          -->
          <Combo
            v-model="form.customer_id"
            :fetch-options="searchCustomers"
            placeholder="可空 —— 归属客户只影响建议号分组与筛选，不是销售依据"
          />
        </FormItem>

        <FormItem name="customer_style_no" label="客户货号备注">
          <Input
            v-model:value="form.customer_style_no"
            placeholder="客户自己的货号 / 唛头，客户需要时才填"
            allow-clear
          />
        </FormItem>

        <FormItem name="bulk_qty" label="大货数量">
          <!-- 数量精度 0（件）；docs/06 §2.4：禁止 `type="number"` 原生 -->
          <!--
            ⚠️ 用 `:value` + `@update:value` 而不是 `v-model:value`：antd 的
            `ValueType` 是 `string | number`（不含 null），而"没填"在这里就是 null。
          -->
          <InputNumber
            :value="form.bulk_qty ?? ''"
            :min="0"
            :precision="0"
            style="width: 100%"
            @update:value="
              (value: string | number | null) =>
                (form.bulk_qty = value === null ? null : Number(value))
            "
          />
        </FormItem>

        <FormItem name="is_active" label="启用">
          <!--
            停用走独立的 `disable` 端点（要填原因、留日志），这里只是建档时的初值。
            所以编辑态也允许改 —— 它对应 `StylePatch.is_active`，是合法的字段。
          -->
          <Switch v-model:checked="form.is_active" />
        </FormItem>

        <FormItem :wrapper-col="{ offset: 6, span: 16 }">
          <Space>
            <Button
              v-can="isEdit ? PERM.BASE_UPDATE : PERM.BASE_CREATE"
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
</style>
