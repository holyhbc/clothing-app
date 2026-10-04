<script setup lang="ts">
/**
 * 模板复制**结果**的展示（CopyDialog 成功之后的那一半）。
 *
 * ## 为什么单独抽出来
 *
 * 复制成功后的展示有一个**硬要求**：`messages[]` 里含「尺码比例未复制」，不显示它
 * 用户会以为工序与比例都配好了，问题推迟到出布头对不上账时才爆（modules/01 §5.1）。
 * 而要在单测里走到"成功"这一步，得先把 Modal 里的 RadioGroup / Select / Combo
 * 全点一遍 —— 那是**交互路径**的测试，很脆（teleport + focus 时序）。
 *
 * 抽出来之后：交互路径由 CopyDialog 的测试守「没选参数不许提交」，
 * 展示要求由本组件的测试守「messages 必须逐条显示」。两边各自清楚。
 */
import { Alert, Descriptions, DescriptionsItem, Space, Table, Typography } from 'ant-design-vue'
import type { ColumnsType } from 'ant-design-vue/es/table'
import { formatMoney } from '@garment/shared'
import type { CopiedPriceOut, TemplateCopyOut } from '@garment/shared'

defineOptions({ name: 'StyleCopyResult' })

defineProps<{ result: TemplateCopyOut }>()

const priceColumns: ColumnsType<CopiedPriceOut> = [
  { key: 'operation_no', title: '工序号', dataIndex: 'operation_no', width: 100 },
  { key: 'source_unit_price', title: '源款单价', dataIndex: 'source_unit_price', width: 130 },
  { key: 'target_unit_price', title: '写入单价', dataIndex: 'target_unit_price', width: 130 },
  { key: 'rate_source', title: '源档位', dataIndex: 'rate_source', width: 110 },
]

/** 逐项新价的行键（`operation_no` 唯一）。⚠️ 具名函数：模板表达式里写不了类型标注。 */
function priceRowKey(row: CopiedPriceOut): string {
  return row.operation_no
}
</script>

<template>
  <Space direction="vertical" size="small" style="width: 100%">
    <Descriptions :column="2" size="small" bordered>
      <DescriptionsItem label="写入工序行">{{ result.structure_written }}</DescriptionsItem>
      <DescriptionsItem label="覆盖工序行">{{ result.structure_overwritten }}</DescriptionsItem>
      <DescriptionsItem label="写入单价行">{{ result.price_written }}</DescriptionsItem>
      <DescriptionsItem label="覆盖单价行">{{ result.price_overwritten }}</DescriptionsItem>
    </Descriptions>

    <!--
      ⚠️ **必须逐条展示** `messages`：里面含「尺码比例未复制」这条硬提示
      （modules/01 §5.1），漏了它用户会以为比例也配好了。
    -->
    <Alert
      v-for="(text, index) in result.messages ?? []"
      :key="index"
      type="info"
      show-icon
      :message="text"
    />

    <Alert v-if="(result.skipped ?? []).length > 0" type="info" show-icon message="被跳过的项">
      <ul class="skipped-list">
        <li v-for="(item, index) in result.skipped ?? []" :key="index">
          {{ item['target'] }} —— {{ item['reason'] }}
        </li>
      </ul>
    </Alert>

    <Table
      :columns="priceColumns"
      :data-source="result.prices ?? []"
      :row-key="priceRowKey"
      size="small"
      :pagination="false"
    >
      <template #bodyCell="{ column, record }">
        <span v-if="column.key === 'source_unit_price'" class="money">
          {{ formatMoney(record.source_unit_price, 6) }}
        </span>
        <span v-else-if="column.key === 'target_unit_price'" class="money">
          {{ formatMoney(record.target_unit_price, 6) }}
        </span>
      </template>
      <template #emptyText>
        <Typography.Text type="secondary">
          没有单价被复制（模式「只复制工序结构」时不写单价）
        </Typography.Text>
      </template>
    </Table>

    <!--
      ADR-0029：复制错误率必须为 0，而**逐项核对是唯一的验证方式** ——
      所以这句提示不是客套，是把「验证责任」显式交回给人。
    -->
    <Typography.Text type="secondary" class="field-hint">
      请逐项核对上面的新价 —— ADR-0029 要求复制错误率为 0。
    </Typography.Text>
  </Space>
</template>

<style scoped>
.skipped-list {
  margin: 0;
  padding-left: var(--space-4);
}

.field-hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-third);
}

.money {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
