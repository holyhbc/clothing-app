import { h as vueH } from 'vue'
import type { VNode } from 'vue'

/* 概念原型用：放宽 Vue h 的重载限制，允许 h(tag, props, child1, child2, ...) 多子节点写法 */
export const h = vueH as unknown as (...args: unknown[]) => VNode
