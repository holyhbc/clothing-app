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
  print_seq: number | undefined
}>({
  from_hands: 1,
  to_hands: 1,
  size_code: '',
  printed_qty: 1,
  is_reprint: false,
  print_seq: undefined,
})

const sizeOptions = computed(() => {
  const set = new Set(bundles.value.map(b => b.size_code))
  return Array.from(set).sort()
})

const handsRange = computed(() => {
  const filtered = printParams.value.size_code
    ? bundles.value.filter(b => b.size_code === printParams.value.size_code)
    : bundles.value
  if (filtered.length === 0) return { min: 1, max: 1 }
  const hands = filtered.map(b => b.hands).sort((a, b) => a - b)
  return { min: hands[0], max: hands[hands.length - 1] }
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
    previewItems.value = res.data as LabelItemOut[]
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
    const payload: LabelPrintIn = {
      from_hands: printParams.value.from_hands,
      to_hands: printParams.value.to_hands,
      size_code: printParams.value.size_code,
      printed_qty: printParams.value.printed_qty,
      is_reprint: printParams.value.is_reprint,
      print_seq: printParams.value.is_reprint ? printParams.value.print_seq : undefined,
      hands_seq: undefined,
      hands_total_of_size: undefined,
    }
    await registerLabelPrints(orderId.value, payload)
    message.success(printParams.value.is_reprint ? '重打登记成功' : '打印登记成功')
    await loadPreview() // 刷新预览以显示打印状态
    window.print()
  } catch (error) {
    message.error(error instanceof Error ? error.message : '打印登记失败')
  }
}

function resetForm(): void {
  printParams.value = {
    from_hands: handsRange.value.min,
    to_hands: handsRange.value.max,
    size_code: printParams.value.size_code,
    printed_qty: 1,
    is_reprint: false,
    print_seq: undefined,
    hands_seq: undefined,
    hands_total_of_size: undefined,
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
          <Form :model="printParams" layout="inline" style="display: flex; flex-wrap: wrap; gap: var(--space-4); align-items: flex-end;">
            <Form.Item label="尺码" name="size_code">
              <Select v-model="printParams.size_code" :options="sizeOptions.map(s => ({ label: s, value: s }))" placeholder="请选择尺码" style="width: 140px" allow-clear />
            </Form.Item>

            <Form.Item label="起始手号" name="from_hands">
              <InputNumber v-model="printParams.from_hands" :min="handsRange.min" :max="handsRange.max" :style="{ width: '120px' }" />
            </Form.Item>

            <Form.Item label="结束手号" name="to_hands">
              <InputNumber v-model="printParams.to_hands" :min="handsRange.min" :max="handsRange.max" :style="{ width: '120px' }" />
            </Form.Item>

            <Form.Item label="每手打印张数" name="printed_qty">
              <InputNumber v-model="printParams.printed_qty" :min="1" :max="10" :style="{ width: '140px' }" />
            </Form.Item>

            <Form.Item label="重打" name="is_reprint" style="margin-left: auto;">
              <label style="display: flex; align-items: center; gap: var(--space-2); cursor: pointer;">
                <input v-model="printParams.is_reprint" type="checkbox" @change="() => {}" />
                <span>重打</span>
              </label>
            </Form.Item>

            <Form.Item v-if="printParams.is_reprint" label="批次序号" name="print_seq" style="margin-left: var(--space-4);">
              <InputNumber v-model="printParams.print_seq" :min="1" :style="{ width: '120px' }" />
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

<style scoped>
/* 打印样式：40mm × 40mm，隐藏交互元素 */
@page {
  size: 40mm 40mm;
  margin: 0;
}

@media print {
  .no-print,
  .breadcrumb-bar,
  .ant-card-head,
  .ant-form,
  .ant-btn,
  .print-hint,
  .hands-table,
  .label-card > .cut-marks:not(.print-only) {
    display: none !important;
  }

  .label-card {
    page-break-inside: avoid;
    break-inside: avoid;
    width: 40mm;
    height: 40mm;
    margin: 0;
    padding: 2mm;
    box-sizing: border-box;
    position: relative;
  }

  .label-content {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    font-size: 8px;
    line-height: 1.2;
  }

  .label-header {
    display: flex;
    justify-content: space-between;
    margin-bottom: 1mm;
  }

  .label-title {
    font-weight: 600;
    font-size: 9px;
  }

  .label-sub {
    font-size: 7px;
    color: var(--color-text-second);
  }

  .label-body {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.5mm;
  }

  .label-row {
    display: flex;
    justify-content: space-between;
  }

  .label-key {
    font-size: 7px;
    color: var(--color-text-second);
  }

  .label-value {
    font-size: 8px;
    font-family: var(--font-mono);
  }

  .hands-text {
    font-weight: 600;
    font-size: 8px;
  }

  .qty {
    font-weight: 600;
    font-size: 9px;
  }

  .code {
    font-size: 6px;
    word-break: break-all;
  }

  .label-codes {
    display: flex;
    justify-content: space-between;
    margin-top: 1mm;
    gap: 1mm;
  }

  .code-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1;
  }

  .qr-code,
  .barcode {
    width: 18mm;
    height: 18mm;
  }

  .qr-code img,
  .barcode img {
    width: 100%;
    height: 100%;
  }

  .code-label {
    font-size: 6px;
    color: var(--color-text-second);
    margin-top: 0.5mm;
  }

  .cut-marks.print-only {
    display: block !important;
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    pointer-events: none;
  }

  .cut-mark {
    position: absolute;
    width: 2mm;
    height: 2mm;
    border: 0.2mm solid var(--color-print-ink);
  }

  .cut-mark.tl { top: -1mm; left: -1mm; border-right: none; border-bottom: none; }
  .cut-mark.tr { top: -1mm; right: -1mm; border-left: none; border-bottom: none; }
  .cut-mark.bl { bottom: -1mm; left: -1mm; border-right: none; border-top: none; }
  .cut-mark.br { bottom: -1mm; right: -1mm; border-left: none; border-top: none; }
}

/* 屏幕预览样式 */
.label-card {
  width: 160px; /* 40mm ≈ 151px @ 96dpi，放大便于预览 */
  height: 160px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-card);
  box-shadow: var(--shadow-card);
  overflow: hidden;
  position: relative;
  box-sizing: border-box;
}

.print-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: var(--space-4);
  padding: var(--space-4);
}

.label-content {
  width: 100%;
  height: 100%;
  padding: 8px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  font-size: var(--font-size-sm);
}

.label-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 4px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--color-border);
}

.label-title {
  font-weight: 600;
  font-size: var(--font-size-base);
}

.label-sub {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.label-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.label-row {
  display: flex;
  justify-content: space-between;
  font-size: var(--font-size-sm);
}

.label-key {
  color: var(--color-text-second);
}

.label-value {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.hands-row .hands-text {
  font-weight: 600;
  color: var(--color-primary);
}

.qty {
  font-weight: 600;
  font-size: var(--font-size-base);
}

.code {
  font-size: 10px;
  word-break: break-all;
  max-width: 100px;
}

.label-codes {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  gap: 8px;
}

.code-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
}

.qr-code,
.barcode {
  width: 80px;
  height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-print-paper);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.qr-code img,
.barcode img {
  max-width: 100%;
  max-height: 100%;
}

.code-label {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
  margin-top: 4px;
}

.print-hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.hands-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-size-sm);
}

.hands-table th,
.hands-table td {
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
  text-align: left;
}

.hands-table th {
  background: var(--color-bg);
  font-weight: 600;
  color: var(--color-text-second);
}

.code-cell {
  font-family: var(--font-mono);
  font-size: 11px;
}

.qty-cell {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.cut-marks {
  display: none;
}

.breadcrumb-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border)
}

.breadcrumb-item a {
  color: var(--color-primary);
  text-decoration: none;
}

.breadcrumb-item a:hover {
  text-decoration: underline;
}

.breadcrumb-sep {
  color: var(--color-text-third);
}
</style>
