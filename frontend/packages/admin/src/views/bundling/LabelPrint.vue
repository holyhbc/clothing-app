<script setup lang="ts">
/**
 * 打菲标签打印页（T-BUND-010）。
 *
 * 功能：
 * - 打印预览网格（40mm×40mm），含款号/色/码/工序/第 N 手 / 共 M 手/件数/二维码/条码
 * - 按手区间选择（from_hands / to_hands / size_code）
 * - 重打（必传 `print_seq`、写日志 `REPRINT`）
 * - `@page { size: 40mm 40mm; margin: 0 }` + `@media print` 隐藏交互元素
 * - 二维码：纯文本 `bundle_no`，纠错 M，≥20mm
 * - 条码：Code128 字符集 B，内容同 `bundle_no`
 *
 * 后端接口：
 * - `GET /bundling-orders/{id}/labels` 导出标签数据（含 `hands_text` 成品文案）
 * - `POST /bundling-orders/{id}/label-prints` 登记打印（必传 `hands_seq`、`size_code`、重打必传 `print_seq`）
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, Card, InputNumber, message, Select, Space, Spin, Form, Divider, Alert } from 'ant-design-vue'
import { exportLabels, registerLabelPrints, listBundlesByOrder, getBundlingOrder } from '@/api/bundling'
import { PERM } from '@garment/shared'
import type { LabelItemOut, BundleListOut, LabelPrintIn } from '@garment/shared'
import PageLayout from '@/components/PageLayout.vue'
import { useLabelCodes } from '@/composables/useLabelCodes'

defineOptions({ name: 'BundlingLabelPrint' })

const route = useRoute()
const router = useRouter()

const { generateQR, generateBarcode } = useLabelCodes()

const orderId = computed(() => route.params.orderId as string)

const order = ref<{ doc_no: string; status: string; style_no: string; color_code: string; operation_no: string } | null>(null)
const bundles = ref<BundleListOut[]>([])
const loading = ref(false)
const previewLoading = ref(false)
const errorMessage = ref('')

// 打印参数
const printParams = ref<{
  from_hands: number
  to_hands: number
  size_code: string
  printed_qty: number
  is_reprint: boolean
  // ⚠️ 用 `null` 表示「没填」，且**不能**直接绑到 `InputNumber` 的 `value` 上：
  //   antd 的 `value` 类型是 `ValueType = string | number`，而本仓开了
  //   `exactOptionalPropertyTypes`（可选 ≠ 可为 `undefined`）—— 传 `undefined`
  //   或 `null` 都是类型错误。见 :func:`printSeqBinding`。
  //   ⚠️ 本页原先写 `v-model`（→ `modelValue`）恰好**把这个类型错误一起吞掉了**：
  //   写错的那一处把两个问题同时掩盖了 —— 这是「靠类型检查兜底」的反面教材。
  print_seq: number | null
}>({
  from_hands: 1,
  to_hands: 1,
  size_code: '',
  printed_qty: 1,
  is_reprint: false,
  print_seq: null,
})

const sizeOptions = computed(() => {
  const set = new Set(bundles.value.map(b => b.size_code))
  return Array.from(set).sort()
})

const handsRange = computed<{ min: number; max: number }>(() => {
  const filtered = printParams.value.size_code
    ? bundles.value.filter(b => b.size_code === printParams.value.size_code)
    : bundles.value
  if (filtered.length === 0) return { min: 1, max: 1 }
  // 用 Math.min/max 而不是「排序后取首尾」：语义就是 min/max，
  // 而且取索引在 `noUncheckedIndexedAccess` 下仍是 `number | undefined`，
  // 哪怕前面已经判过 `length === 0`，TS 也推不出来。
  const hands = filtered.map(b => b.hands)
  return { min: Math.min(...hands), max: Math.max(...hands) }
})

const previewItems = ref<LabelItemOut[]>([])
const showPreview = ref(false)
const qrCodeUrls = ref<Map<string, string>>(new Map())
const barcodeUrls = ref<Map<string, string>>(new Map())

async function loadOrder(): Promise<void> {
  if (!orderId.value) {
    errorMessage.value = '缺少打菲单 ID'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    const [orderRes, bundlesRes] = await Promise.all([
      getBundlingOrder(orderId.value),
      listBundlesByOrder(orderId.value, { status: 'ACTIVE' }),
    ])
    order.value = {
      doc_no: orderRes.doc_no,
      status: orderRes.status,
      style_no: orderRes.style_no,
      color_code: orderRes.color_code,
      operation_no: orderRes.operation_no,
    }
    bundles.value = bundlesRes.items
    // 默认选第一个尺码
    if (bundles.value.length > 0 && !printParams.value.size_code) {
      const firstBundle = bundles.value[0]
      if (firstBundle) {
        printParams.value.size_code = firstBundle.size_code
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadPreview(): Promise<void> {
  if (!orderId.value) return
  previewLoading.value = true
  qrCodeUrls.value.clear()
  barcodeUrls.value.clear()
  try {
    const res = await exportLabels(orderId.value, {
      from_hands: printParams.value.from_hands,
      to_hands: printParams.value.to_hands,
      size_code: printParams.value.size_code,
      format: 'data',
    })
    // ⚠️ 取 `res.items`，不是 `res.data` —— `/labels` 返回的是整个 `LabelExportOut`
    //   （`doc_no` / `hands_count` / `total_qty` / `items`），响应里**没有** `data` 键。
    //   之前这里写的是 `res.data`，于是 `previewItems` 拿到 `undefined`，
    //   紧接着的 `for (const item of previewItems.value)` 抛
    //   `previewItems.value is not iterable`，被下面的 catch 吞掉、预览清空 ——
    //   表现是「点生成预览什么都没发生」，而不是任何一条看得懂的报错。
    previewItems.value = res.items
    showPreview.value = true
    // 预生成二维码/条码
    for (const item of previewItems.value) {
      qrCodeUrls.value.set(item.bundle_no, await generateQR(item.qr_content, 180)) // 打印尺寸约 18mm
      barcodeUrls.value.set(item.bundle_no, await generateBarcode(item.barcode_content, 280, 100))
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : '预览加载失败')
    showPreview.value = false
  } finally {
    previewLoading.value = false
  }
}

/** 打印登记 + 浏览器打印：先登记再打印 */
async function handlePrintAndPrint(): Promise<void> {
  if (!orderId.value) return
  try {
    const sizeCode = printParams.value.size_code
    const payload: LabelPrintIn = {
      from_hands: printParams.value.from_hands,
      to_hands: printParams.value.to_hands,
      // ★ `hands_seq` **后端必传**：`label_guard.resolve_hand_span()` 第一句就是
      //   `if hands_seq is None: raise BusinessError(10002, "必须带 hands_seq")`。
      //   之前这里写的是 `undefined` —— 也就是说**点「打印」必然报 10002**，
      //   标签打印登记在真实环境下 100% 失败（单测没抓到：`registerLabelPrints`
      //   被 mock 了，只断言「调用了这个函数」，没断言参数）。
      //   语义是「本次打印第几手」，且服务端会校验它与 `from_hands` 相等
      //   （矛盾报 10001），所以取区间起点。
      hands_seq: printParams.value.from_hands,
      printed_qty: printParams.value.printed_qty,
      is_reprint: printParams.value.is_reprint,
      // 「非重打」时**不传** print_seq —— 传了空值会变成「重打批次序号 = 0」，
      // 后端按 B15 判「重打必带 print_seq」而报 `10002`，而用户看到的却是
      // 「我明明没重打」。⚠️ 判空用 `!= null`：`print_seq` 现在用 `null` 表示未填
      //（见 `printParams` 的类型注释），写 `!== undefined` 会漏掉这一路。
      ...(printParams.value.is_reprint && printParams.value.print_seq !== null
        ? { print_seq: printParams.value.print_seq }
        : {}),
      // `size_code` 不传 = 全部尺码。**空串不等于不传** —— 传 `''` 会变成
      // `WHERE size_code = ''`，一个都匹配不上。
      ...(sizeCode ? { size_code: sizeCode } : {}),
      // 「共 M 手」快照，服务端拿它跟库内权威值核对，对不上报 10001。
      // 没选具体尺码时手数是**按尺码各自编号**的，全局 max 对不上任何单一尺码，
      // 所以这时不传。
      ...(sizeCode ? { hands_total_of_size: handsRange.value.max } : {}),
    }
    await registerLabelPrints(orderId.value, payload)
    message.success(printParams.value.is_reprint ? '重打登记成功' : '打印登记成功')
    await loadPreview() // 刷新预览以显示打印状态
    window.print()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '打印登记失败')
  }
}

/**
 * 批次序号输入框的 props：未填时**整个不传 `value` 键**，而不是传 `undefined`。
 *
 * ⚠️ 两种「空」写法都会被类型检查拒绝（`exactOptionalPropertyTypes` 下可选属性
 *   不能显式给 `undefined`；`null` 又不在 antd 的 `ValueType` 里），所以只能用
 *   `v-bind` 把 `value` 这个键**存在与否**表达出来。
 */
const printSeqBinding = computed((): { value?: number } =>
  printParams.value.print_seq === null ? {} : { value: printParams.value.print_seq },
)

/** `InputNumber` 的 `update:value` 在清空时给 `null`、正常时给 `number`。 */
function onPrintSeqUpdate(value: number | string | null): void {
  printParams.value.print_seq = typeof value === 'number' ? value : null
}

function resetForm(): void {
  printParams.value = {
    from_hands: handsRange.value.min,
    to_hands: handsRange.value.max,
    size_code: printParams.value.size_code,
    printed_qty: 1,
    is_reprint: false,
    print_seq: null,
  }
}

watch(() => printParams.value.size_code, () => {
  printParams.value.from_hands = handsRange.value.min
  printParams.value.to_hands = handsRange.value.max
})

onMounted(() => {
  void loadOrder()
})

const canPrint = computed(() => order.value?.status === 'APPROVED')

/** 溯源面包屑：`款号 > 裁剪单 > 打菲单 > 标签打印` */
const breadcrumbs = computed(() => {
  if (!order.value) return []
  return [
    { label: order.value.style_no, to: { name: 'base-styles-detail', params: { styleNo: order.value.style_no } } },
    { label: '打菲单', to: { name: 'bundling-orders' } },
    { label: order.value.doc_no, to: { name: 'bundling-orders-detail', params: { orderId: orderId.value } } },
    { label: '标签打印', to: null },
  ]
})
</script>

<template>
  <PageLayout :title="`标签打印 - ${order?.doc_no ?? ''}`">
    <template #extra>
      <Button @click="router.back()">返回</Button>
      <Button v-if="canPrint" v-can="PERM.BUNDLING_PRINT" type="primary" :disabled="previewLoading" @click="handlePrintAndPrint">
        打印
      </Button>
      <Button v-if="canPrint" v-can="PERM.BUNDLING_PRINT" :loading="previewLoading" :disabled="!showPreview" @click="loadPreview">
        刷新预览
      </Button>
    </template>

    <Alert
      v-if="errorMessage !== ''"
      type="error"
      show-icon
      message="加载失败"
      :description="errorMessage"
      style="margin-bottom: var(--space-4)"
    />

    <Spin :spinning="loading">
      <template v-if="order !== null">
        <!-- 溯源面包屑 -->
        <div class="breadcrumb-bar" style="margin-bottom: var(--space-4); font-size: var(--font-size-sm);">
          <span v-for="(crumb, i) in breadcrumbs" :key="i" class="breadcrumb-item">
            <a v-if="crumb.to" @click.prevent="router.push(crumb.to)">{{ crumb.label }}</a>
            <span v-else>{{ crumb.label }}</span>
            <span v-if="i < breadcrumbs.length - 1" class="breadcrumb-sep"> › </span>
          </span>
        </div>

        <Card size="small" title="打印参数" style="margin-bottom: var(--space-4)">
          <!--
            ⚠️ ★★ **antd 组件一律 `v-model:value`，不是 `v-model`**（2026-10-13 修，
            E2E 挖出）。本页原先四个 `Select`/`InputNumber` 全写成 `v-model`，
            Vue 于是把值当 `modelValue` 传给子组件 —— 而 antd 的受控属性叫
            `value`，**不认 `modelValue`**。后果是它变成一个**透传的 DOM 属性**：

                <div modelvalue="1" class="ant-input-number …">
                  <input class="ant-input-number-input" value="">   ← 框里空白

            两个后果都很隐蔽，**页面上完全看不出异常**：
              1. 框里**显示空白**，而模型里明明是 1 / 2 / 1；
              2. 用户**打字不生效** —— 填了「批次序号」提交，请求体里**没有**
                 `print_seq`，后端按 B15 报 `10002`，操作员看到的只是
                 「重打登记失败」，根本猜不到是自己填的值没进去。

            单测抓不到：`LabelPrint.test.ts` 断的是组件的 **props**（min/max/placeholder），
            而 props 传对了 —— 断的是「组件拿到什么」，没断「用户看到什么」。

            ⚠️ 同理 **`Form.Item` 上不要写 `name`**：本页没有任何校验规则，
            `name` 只会让 antd 再注入一份 `value`/`onUpdate:value`，形成第二份绑定。
            全仓其余页面（`Login.vue` / `Password.vue`）都是「`Form.Item` 只管
            标签与布局、值由子组件自己管」，本页原本是唯一的例外。
          -->
          <Form layout="inline" style="display: flex; flex-wrap: wrap; gap: var(--space-4); align-items: flex-end;">
            <Form.Item label="尺码">
              <Select v-model:value="printParams.size_code" :options="sizeOptions.map(s => ({ label: s, value: s }))" placeholder="请选择尺码" style="width: 140px" allow-clear />
            </Form.Item>

            <Form.Item label="起始手号">
              <InputNumber v-model:value="printParams.from_hands" :min="handsRange.min" :max="handsRange.max" :style="{ width: '120px' }" />
            </Form.Item>

            <Form.Item label="结束手号">
              <InputNumber v-model:value="printParams.to_hands" :min="handsRange.min" :max="handsRange.max" :style="{ width: '120px' }" />
            </Form.Item>

            <Form.Item label="每手打印张数">
              <InputNumber v-model:value="printParams.printed_qty" :min="1" :max="10" :style="{ width: '140px' }" />
            </Form.Item>

            <Form.Item label="重打" style="margin-left: auto;">
              <label style="display: flex; align-items: center; gap: var(--space-2); cursor: pointer;">
                <input v-model="printParams.is_reprint" type="checkbox" @change="() => {}" />
                <span>重打</span>
              </label>
            </Form.Item>

            <Form.Item v-if="printParams.is_reprint" label="批次序号" style="margin-left: var(--space-4);">
              <InputNumber
                v-bind="printSeqBinding"
                :min="1"
                :style="{ width: '120px' }"
                @update:value="onPrintSeqUpdate"
              />
            </Form.Item>
          </Form>

          <Divider style="margin: var(--space-3) 0;" />

          <Space>
            <Button type="primary" :loading="previewLoading" :disabled="!canPrint" @click="loadPreview">
              生成预览
            </Button>
            <Button :disabled="!canPrint" @click="resetForm">重置</Button>
          </Space>
        </Card>

        <!-- 预览网格 -->
        <Card v-if="showPreview" size="small" title="打印预览（40mm × 40mm）" style="margin-bottom: var(--space-4)">
          <template #extra>
            <span class="print-hint">按 Ctrl+P 或点击右上角「打印」按钮进行打印</span>
          </template>

          <div ref="printGrid" class="print-grid">
            <div v-for="item in previewItems" :key="item.bundle_no" class="label-card" style="page-break-inside: avoid;">
              <!-- 标签内容区 -->
              <div class="label-content">
                <div class="label-header">
                  <span class="label-title">{{ item.style_no }}</span>
                  <span class="label-sub">{{ item.operation_no }}</span>
                </div>

                <div class="label-body">
                  <div class="label-row">
                    <span class="label-key">色码</span>
                    <span class="label-value">{{ item.color_code }}</span>
                  </div>
                  <div class="label-row">
                    <span class="label-key">尺码</span>
                    <span class="label-value">{{ item.size_code }}</span>
                  </div>
                  <div class="label-row hands-row">
                    <span class="label-key">手号</span>
                    <span class="label-value hands-text">{{ item.hands_text }}</span>
                  </div>
                  <div class="label-row">
                    <span class="label-key">件数</span>
                    <span class="label-value qty">{{ item.bundle_qty }}</span>
                  </div>
                  <div class="label-row">
                    <span class="label-key">打菲号</span>
                    <span class="label-value code">{{ item.bundle_no }}</span>
                  </div>
                </div>

                <div class="label-codes">
                  <div class="code-item">
                    <div class="qr-code">
                      <img :src="qrCodeUrls.get(item.bundle_no)" :alt="`QR: ${item.qr_content}`" />
                    </div>
                    <div class="code-label">二维码</div>
                  </div>
                  <div class="code-item">
                    <div class="barcode">
                      <img :src="barcodeUrls.get(item.bundle_no)" :alt="`Barcode: ${item.barcode_content}`" />
                    </div>
                    <div class="code-label">条码</div>
                  </div>
                </div>
              </div>

              <!-- 仅打印时显示的裁切标记 -->
              <div class="cut-marks print-only">
                <div class="cut-mark tl"></div>
                <div class="cut-mark tr"></div>
                <div class="cut-mark bl"></div>
                <div class="cut-mark br"></div>
              </div>
            </div>
          </div>
        </Card>

        <!-- 当前打印范围的手列表 -->
        <Card v-if="showPreview" size="small" title="本次打印范围手清单">
          <table class="hands-table">
            <thead>
              <tr>
                <th>尺码</th>
                <th>手号</th>
                <th>打菲号</th>
                <th>件数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in previewItems" :key="item.bundle_no">
                <td>{{ item.size_code }}</td>
                <td>第 {{ item.hands_seq }} 手 / 共 {{ item.hands_total_of_size }} 手</td>
                <td class="code-cell">{{ item.bundle_no }}</td>
                <td class="qty-cell">{{ item.bundle_qty }}</td>
              </tr>
            </tbody>
          </table>
        </Card>
      </template>
    </Spin>
  </PageLayout>
</template>

<style scoped src="./LabelPrint.css"></style>
