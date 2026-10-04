<script setup lang="ts">
/**
 * 尺码列表页。
 *
 * ⚠️ **刻意只有一个元素**：九个资源的列表行为完全同构（后端 `resources.py::RESOURCES`
 *    就是为此而生），所以实现在 `ResourceList.vue` 里，差异（中文名 / 列 / 筛选项）
 *    在 `api/base.ts` 的注册表里。抄九份的话，「停用必填原因」「被引用不给删除入口」
 *    这些规则就有九份拷贝，漏一份就是一个能随便停用主数据的漏洞。
 *
 * 保留这个文件是为了 docs/03 §2.5 的目录约定（每个模块固定 `List.vue` / `Form.vue`），
 * 以及给这个资源留一个放专属逻辑的位置。
 */
import ResourceList from '../ResourceList.vue'

defineOptions({ name: 'SizesList' })
</script>

<template>
  <ResourceList resource-key="sizes" />
</template>
