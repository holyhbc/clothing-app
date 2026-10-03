<script setup lang="ts">
/**
 * 工作台占位。
 *
 * 只做两件事：证明「登录态 + 有权限」这条路真能走通，以及给 403 页一个可返回的落点。
 * 真正的模块入口与统计卡片在 T-WEB-003~005。
 */
import { computed } from 'vue'
import { Card, Descriptions, Space, Tag, Typography } from 'ant-design-vue'
import { formatDateTime } from '@garment/shared'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

// ⚠️ `formatDateTime` 收的是**后端传来的 ISO8601 字符串**（docs/03 §2.1 第 7 条），
//    内部按 `Asia/Shanghai` 渲染。这里展示"当前时间"是前端自己造的，
//    同样转成 ISO 字符串再传，别顺手改成 `new Date()` —— 那个会让类型不匹配，
//    也让"前端时间从哪来"变得不清楚。
const nowIso = new Date().toISOString()

const scopeText = computed(() => {
  switch (auth.dataScope) {
    case 'FACTORY':
      return '全厂'
    case 'WORKSHOP':
      return '本车间及授权车间'
    case 'GROUP':
      return '本组'
    case 'SELF':
      return '仅本人'
    default:
      return '未知'
  }
})
</script>

<template>
  <div class="home-page">
    <Space direction="vertical" size="middle" style="width: 100%">
      <Typography.Title :level="4" style="margin: 0"> 工作台 </Typography.Title>

      <Card :bordered="false" class="home-card">
        <Descriptions :column="1" size="small" bordered>
          <Descriptions.Item label="姓名">
            {{ auth.user?.name ?? '—' }}
          </Descriptions.Item>
          <Descriptions.Item label="工号">
            {{ auth.user?.employee_no ?? '—' }}
          </Descriptions.Item>
          <Descriptions.Item label="角色">
            <Space wrap>
              <Tag v-for="role in auth.roles" :key="role.code">
                {{ role.name }}
              </Tag>
              <span v-if="auth.roles.length === 0">—</span>
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="数据范围">
            {{ scopeText }}
          </Descriptions.Item>
          <Descriptions.Item label="权限点数量">
            {{ auth.permissions.size }}
          </Descriptions.Item>
          <Descriptions.Item label="当前时间">
            {{ formatDateTime(nowIso) }}
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </Space>
  </div>
</template>

<style scoped>
.home-page {
  padding: var(--space-6);
  /* docs/06 §2.1：超宽屏不拉满，限宽居中 */
  max-width: var(--page-max-width);
  margin: 0 auto;
  width: 100%;
}

.home-card {
  box-shadow: var(--shadow-card);
}
</style>
