<script setup lang="ts">
/**
 * 金额文本（docs/03 §2.1 第 6 条「金额展示统一 `formatMoney`，禁止手写 `toFixed`」；
 * docs/06 §2.2「金额列右对齐 + 等宽数字」）。
 *
 * ## 为什么要单独一个组件
 *
 * 「右对齐 + `tabular-nums`」很容易漏：漏一列看不出来，漏十列整张表的钱就对不齐了。
 * 而 `tabular-nums` 在 CSS 里只是 `global.css` 的一个 class —— 没人 review 时会注意到。
 * 收进组件就**漏不掉**。
 *
 * ⚠️ 入参接受 `string`：后端把 `numeric(18,4)` 序列化成字符串（docs/05 §3），
 *    `0.378000` 这种值 JS 的 `number` 表示不了。`formatMoney` 内部已处理，不要在这里转。
 *
 * ⚠️ **不做自己的空值处理**：`formatMoney(null)` 本身就返回 `-`。这里再包一层
 *    `—` 会造出第二种"空"的写法 —— 而同一张表里一处显示 `-` 一处显示 `—`，
 *    用户会以为哪个是"真的没填"。
 */
import { computed } from 'vue'
import { formatMoney } from '@garment/shared'

const props = withDefaults(
  defineProps<{
    /** 金额。后端返回字符串或数字都接受；空值显示 `-`。 */
    value: string | number | null | undefined
    /**
     * 小数位。金额 2 位；**单价是 4~6 位**（docs/04 §7：`numeric(18,4)`），
     * 所以不能写死 —— 写死会把 0.378000 显示成 0.38，单价表直接失去意义。
     */
    places?: number
  }>(),
  { places: 2 },
)

const text = computed(() => formatMoney(props.value, props.places))
</script>

<template>
  <span class="money-text" :title="text">{{ text }}</span>
</template>

<style scoped>
/*
 * 右对齐 + 等宽数字：`tabular-nums` 让每个字形占同样宽度，
 * 竖着看一列金额小数点才对得齐（docs/06 §2.2）。
 * `min-width` 是为了让「零」和「十万」这两列的位数变化不推挤右边界。
 */
.money-text {
  display: inline-block;
  min-width: 6em;
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-feature-settings: 'tnum';
  white-space: nowrap;
}
</style>
