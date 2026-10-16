/**
 * 工位机扫码状态机（T-PIECE-007，`04 §5.1` 链路 A 十二步的前端部分）。
 *
 * ## 为什么单独一个 composable 而不是塞进 `Scan.vue`
 *
 * 「扫码枪 HID 输入 → 缓冲 → Enter → 校验 → 提交 → 反馈」这条链**必须能单独测**：
 * 组件测里要塞 DOM 事件、要 mock 路由与 store，一个 400 行的 `Scan.vue` 里
 * 缓冲逻辑与表格/选择器的渲染混在一起后，`onKey` 的一个 off-by-one 根本看不出来
 * （它表现为「偶尔少一个字符」，而重跑测试又过了）。
 *
 * ⚠️ 本文件**不依赖 Vue 组件生命周期**，`Scan.vue` 只负责把 `keydown` 转发进来。
 */
import { reactive } from 'vue'
import { ApiError } from '@garment/shared'
import type { PieceworkCountOut } from '@garment/shared'
import { countPiecework } from '@/api/piecework'
import { FLASH_MS, playTone } from './feedback'

/**
 * 打菲号格式。**与后端 `bundling/service/numbering.py::BUNDLE_NO_PATTERN` 对齐**：
 * `BD-` + 8 位日期 + `-` + 6 位单号 + `-` + 尺码码(1~3) + 2 位手序号 + `-` + 4 位件序号。
 *
 * ⚠️ 尺码码是 **1~3 位**（Q-B18 未定稿），不是 `04 §5.1` 那行示例里的固定 2 位 ——
 * 照示例写死 2 位会把 `S01` / `XXL01` 这类合法码判成非法、直接吞掉不发请求。
 */
export const BUNDLE_NO_PATTERN = /^BD-[0-9]{8}-[0-9]{6}-[A-Z0-9]{1,3}[0-9]{2}-[0-9]{4}$/

/** 扫码上下文（页面顶部设置条维护）。 */
export interface ScanConfig {
  employeeId: string | null
  workstationNo: string | null
  operationNo: string | null
  /** 暂停扫码（`06 §2.5` 第 7 条）：为真时忽略所有扫码。 */
  paused: boolean
  /** 部分生产件数；`null` = 整手（`04 §5.1` 步骤 5）。 */
  qty: number | null
}

export type ScanPhase = 'idle' | 'success' | 'duplicate' | 'error'

export interface ScanState {
  /** 扫码枪字符缓冲（Enter 前的累积）。 */
  buffer: string
  phase: ScanPhase
  /** 行内提示文案（黄/红/绿各一）。 */
  message: string
  /** 本次请求进行中；期间忽略新的 Enter，避免连按产生重复请求。 */
  busy: boolean
  /** ★ 服务端权威计数（不是前端累加，幂等命中不会重复 +1）。 */
  todayHands: number
  todayQty: string
  todayAmount: string
  /** 最近一次成功结果（用于「第 N 手 · N/M 件」展示）。 */
  last: PieceworkCountOut | null
  /** 闪烁方向：成功绿 / 重复黄 / 失败红。 */
  flash: 'ok' | 'warn' | 'error' | null
}

/** 判断键盘事件是否落在输入控件上（此时不应进入扫码缓冲）。 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (target === null || typeof target !== 'object') return false
  const el = target as { tagName?: unknown; isContentEditable?: unknown }
  const tag = typeof el.tagName === 'string' ? el.tagName.toUpperCase() : ''
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return el.isContentEditable === true
}

function escapeHint(code: string): string {
  // 只在行内展示，截断到 32 字符避免长串撑破布局。
  return code.length > 32 ? `${code.slice(0, 32)}…` : code
}

function hhmm(iso: unknown): string {
  if (typeof iso !== 'string') return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** 幂等键：`bundle_no|workstation_no|operation_no|时间片`（`04 §5.1` / `09 §2.4`）。 */
export function idempotencyKey(bundleNo: string, config: ScanConfig, now = Date.now()): string {
  const slice = Math.floor(now / 10_000)
  return `${bundleNo}|${config.workstationNo ?? ''}|${config.operationNo ?? ''}|${slice}`
}

/** `32001` 的 details → 一行黄色提示（`06 §2.5` 第 3 条：不弹窗）。 */
export function duplicateText(details: Record<string, unknown>): string {
  const who = typeof details['counted_by_name'] === 'string' ? details['counted_by_name'] : '他人'
  const when = hhmm(details['counted_at'])
  const qty = typeof details['counted_qty'] === 'string' ? details['counted_qty'] : ''
  const hands = typeof details['hands'] === 'number' ? details['hands'] : null
  const timePart = when === '' ? '' : ` ${when}`
  const handPart = hands === null ? '' : `第 ${hands} 手 `
  return `该码 ${handPart}已由 ${who}${timePart} 计 ${qty} 件`
}

export function createScanMachine() {
  const state = reactive<ScanState>({
    buffer: '',
    phase: 'idle',
    message: '',
    busy: false,
    todayHands: 0,
    todayQty: '0',
    todayAmount: '0',
    last: null,
    flash: null,
  })

  let flashTimer: ReturnType<typeof setTimeout> | null = null
  let successTimer: ReturnType<typeof setTimeout> | null = null

  function flash(kind: 'ok' | 'warn' | 'error'): void {
    state.flash = kind
    if (flashTimer !== null) clearTimeout(flashTimer)
    flashTimer = setTimeout(() => {
      state.flash = null
      flashTimer = null
    }, FLASH_MS)
  }

  function fail(message: string, kind: 'warn' | 'error'): void {
    state.phase = kind === 'warn' ? 'duplicate' : 'error'
    state.message = message
    playTone(kind)
    flash(kind)
  }

  function reset(): void {
    state.buffer = ''
    state.phase = 'idle'
    state.message = ''
    state.flash = null
  }

  async function submit(bundleNo: string, config: ScanConfig): Promise<void> {
    if (!BUNDLE_NO_PATTERN.test(bundleNo)) {
      fail(`不是有效的打菲号：${escapeHint(bundleNo)}`, 'error')
      return
    }
    if (config.employeeId === null || config.workstationNo === null || config.operationNo === null) {
      fail('请先选定操作员、工序并填写工位号', 'error')
      return
    }
    state.busy = true
    try {
      const out = await countPiecework(
        {
          bundle_no: bundleNo,
          employee_id: config.employeeId,
          scanned_at: new Date().toISOString(),
          station_id: config.workstationNo,
          ...(config.qty === null ? {} : { qty: String(config.qty) }),
        },
        idempotencyKey(bundleNo, config),
      )
      state.last = out
      state.todayHands = out.today_hands
      state.todayQty = out.today_qty
      state.todayAmount = out.today_amount
      state.phase = 'success'
      const partial = out.qty !== out.bundle_qty
      const qtyText = partial ? `${out.qty}/${out.bundle_qty}` : out.qty
      state.message = `第 ${out.hands} 手 · ${qtyText} 件 · +¥${out.amount}`
      if (successTimer !== null) clearTimeout(successTimer)
      successTimer = setTimeout(() => {
        if (state.phase === 'success') state.phase = 'idle'
        successTimer = null
      }, 1200)
      playTone('ok')
      flash('ok')
    } catch (error) {
      if (error instanceof ApiError && error.code === 32001) {
        fail(duplicateText(error.details), 'warn')
      } else if (error instanceof ApiError && error.code === 32006) {
        fail('该手件数不足，请追加手（新裁剪 + 新打菲）', 'error')
      } else if (error instanceof ApiError) {
        fail(error.message, 'error')
      } else {
        fail('网络异常，请重试', 'error')
      }
    } finally {
      state.busy = false
    }
  }

  /** 转发一个 `keydown`。返回 `true` 表示事件被扫码链路消费（调用方可据此 `preventDefault`）。 */
  function onKey(event: KeyboardEvent, config: ScanConfig): boolean {
    if (config.paused) return false
    if (isTypingTarget(event.target)) return false
    if (event.key === 'Enter') {
      const code = state.buffer.trim()
      state.buffer = ''
      // 缓冲为空（比如随手按了下 Enter）不算错误，直接忽略。
      if (code === '' || state.busy) return false
      void submit(code, config)
      return true
    }
    if (event.key.length === 1) {
      state.buffer += event.key
      return true
    }
    return false
  }

  /** 仅供测试/卸载清理定时器。 */
  function dispose(): void {
    if (flashTimer !== null) clearTimeout(flashTimer)
    if (successTimer !== null) clearTimeout(successTimer)
    flashTimer = null
    successTimer = null
  }

  return { state, onKey, submit, reset, dispose }
}

export type ScanMachine = ReturnType<typeof createScanMachine>
