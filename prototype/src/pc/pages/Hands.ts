import { h } from '../../components/h'
import { defineComponent, ref, computed } from 'vue'

import { Modal, useToast, ToastHost } from '../../components/ui'
import { pageHead, card, descGrid } from '../shared'
import { money, qtyFmt } from '../../mock'

/* ===================== 工位机计件：按手扫码（ADR-0016） ===================== */
export const StationHand = defineComponent({
  name: 'StationHand',
  emits: ['navigate'],
  setup(_, { emit }) {
    const toast = useToast()
    const online = ref(true)
    const paused = ref(false)

    // 计数：手与件双轨
    const hands = ref(12)
    const pieces = ref(730)
    const amount = ref(306.60)
    const dup = ref(2)

    const code = ref('')
    const panelCls = ref('scan-panel')
    const result = ref<{ cls: string; text: string; hint: string } | null>(null)

    // 部分生产弹窗
    const partial = ref<{ bundleNo: string; size: string; handNo: string; total: number } | null>(null)
    const partialQty = ref('')
    const partialErr = ref('')

    let timer: number | undefined
    let audioCtx: AudioContext | null = null

    // 演示用码池：未计 → 部分生产 → 已计
    const POOL = [
      { no: 'BD-20261018-000031-XL02-0001', size: 'XL', handNo: '第 2 手', total: 60, rate: 0.42 },
      { no: 'BD-20261018-000031-XXL01-0001', size: 'XXL', handNo: '第 1 手', total: 60, rate: 0.42 },
      { no: 'BD-20261018-000031-XL01-0001', size: 'XL', handNo: '第 1 手', total: 60, rate: 0.42 },
    ]
    const used = new Set<string>()

    function beep(freq: number, dur: number) {
      try {
        audioCtx ??= new AudioContext()
        const o = audioCtx.createOscillator()
        const g = audioCtx.createGain()
        o.frequency.value = freq
        o.type = 'sine'
        g.gain.setValueAtTime(0.08, audioCtx.currentTime)
        g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + dur)
        o.connect(g).connect(audioCtx.destination)
        o.start(); o.stop(audioCtx.currentTime + dur)
      } catch { /* 浏览器限制 */ }
    }

    function flash(cls: string, r: { cls: string; text: string; hint: string }) {
      panelCls.value = `scan-panel ${cls}`
      result.value = r
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        panelCls.value = 'scan-panel'
        result.value = null
      }, 2200)
    }

    function commit(n: number, b: typeof POOL[number], handText: string) {
      hands.value += 1
      pieces.value += n
      amount.value = Number((amount.value + n * b.rate).toFixed(2))
      used.add(b.no)
      beep(880, 0.08)
      flash('ok', {
        cls: 'ok',
        text: `✓ ${b.size} ${handText}　${n} 件　+ ¥${money(n * b.rate)}`,
        hint: `该手共 ${b.total} 件，已计 ${n} 件（${b.no}）`,
      })
    }

    function handleScan(c: string) {
      if (paused.value) return
      code.value = c
      const b = POOL.find((x) => x.no === c)
      if (!b) {
        beep(160, 0.2)
        flash('err', { cls: 'err', text: '打菲号不存在（31001）', hint: '请确认标签是否属于本工序' })
        return
      }
      if (used.has(c)) {
        dup.value++
        beep(220, 0.12)
        flash('dup', {
          cls: 'dup',
          text: `${b.size} ${b.handNo} 已被 陈美玲 计过`,
          hint: '一码只能由一个员工计一次（32001）',
        })
        return
      }
      if (!online.value) {
        flash('dup', { cls: 'dup', text: '离线 · 已缓存', hint: '恢复网络后自动补传' })
        return
      }
      commit(b.total, b, b.handNo)
    }

    function simulatePartial() {
      const b = POOL[0]
      partial.value = { bundleNo: b.no, size: b.size, handNo: b.handNo, total: b.total }
      partialQty.value = ''
      partialErr.value = ''
    }

    function submitPartial() {
      const v = Number(partialQty.value)
      if (!partialQty.value || !Number.isFinite(v) || v <= 0) { partialErr.value = '请输入 > 0 的件数'; return }
      if (!Number.isInteger(v)) { partialErr.value = '件数必须是整数（尾数不计件，32005）'; return }
      if (v > partial.value!.total) { partialErr.value = `32006 超过该手件数 ${partial.value!.total}；超产需追加手`; return }
      const b = POOL[0]
      commit(v, b, `${partial.value!.handNo}（部分生产）`)
      partial.value = null
    }

    return () => [
      pageHead('工位机计件（按手）', `缝制一组 · 工位 SW-03 · 操作员 王海涛　｜　ADR-0016：一码 = 一手 = 一个员工`, [
        h('button', { class: ['btn', paused.value ? 'primary' : ''], onClick: () => (paused.value = !paused.value) },
          paused.value ? '▶ 恢复扫码' : '⏸ 暂停扫码'),
        h('button', { class: 'btn', onClick: () => { POOL.forEach((b) => used.delete(b.no)); toast.ok('演示池已重置') } }, '重置演示'),
        h('button', { class: 'btn', onClick: () => handleScan(POOL.find((x) => !used.has(x.no))?.no ?? POOL[0].no) }, '模拟扫新手'),
        h('button', { class: 'btn', onClick: simulatePartial }, '模拟部分生产'),
        h('button', { class: 'btn', onClick: () => handleScan('BD-9999-XX-9999999') }, '模拟无效码'),
        h('button', { class: 'btn', onClick: () => emit('navigate', 'piecework') }, '查看流水 ›'),
      ]),

      h('div', { class: 'station' }, [
        h('div', { class: 'station-status' }, [
          h('div', { class: 'chip' }, [h('span', { class: online.value ? 'dot-live' : 'dot-off' }), h('span', { class: 'k' }, online.value ? '在线' : '离线')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '款号'), h('span', { class: 'v mono' }, 'HB-2026-0018')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '颜色'), h('span', { class: 'v' }, '本白')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '工序'), h('span', { class: 'v' }, '打边（01）')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '每手'), h('span', { class: 'v' }, '60 件')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '工价'), h('span', { class: 'v amount' }, '¥0.42')]),
          h('button', {
            class: 'btn sm', style: 'margin-left:auto',
            onClick: () => { online.value = !online.value; toast.ok(online.value ? '已恢复联网' : '已切到离线（演示）') },
          }, online.value ? '模拟断网' : '恢复联网'),
        ]),

        h('div', { class: panelCls.value }, [
          h('div', { class: 'scan-hint' }, paused.value ? '已暂停扫码' : '请扫描打菲标签（无需点击页面）'),
          h('div', { class: 'scan-code mono' }, code.value || ' '),
          h('div', { class: ['scan-result', result.value?.cls ?? ''] }, result.value?.text ?? ' '),
          h('div', { class: 'scan-hintline' }, result.value?.hint ?? '扫码即知这手件数；若只做完一部分，可手写实际件数（不得超过该手件数）'),
        ]),

        h('div', { class: 'counter-grid', style: 'grid-template-columns:repeat(4,1fr)' }, [
          h('div', { class: 'counter' }, [
            h('div', { class: 'n' }, String(hands.value)),
            h('div', { class: 'l' }, '今日计件（手）'),
          ]),
          h('div', { class: 'counter' }, [
            h('div', { class: 'n' }, qtyFmt(pieces.value)),
            h('div', { class: 'l' }, '今日计件（件）'),
          ]),
          h('div', { class: 'counter money' }, [
            h('div', { class: 'n' }, '¥' + money(amount.value)),
            h('div', { class: 'l' }, '今日工钱'),
          ]),
          h('div', { class: 'counter' }, [
            h('div', { class: 'n', style: 'color:var(--color-warning)' }, String(dup.value)),
            h('div', { class: 'l' }, '重复扫码（未计）'),
          ]),
        ]),

        h('div', { class: 'alert info', style: 'margin-top:16px' }, [
          h('b', {}, '为什么按手？'), ' 一码 = 一手 = ',
          h('b', {}, '一个员工的责任单元'),
          '。XL 打 2 手就是 ',
          h('code', {}, 'XL01'),
          '、',
          h('code', {}, 'XL02'),
          ' 两个码，两人各认一手，追溯清楚。',
        ]),
      ]),
      h(ToastHost, { items: toast.items }),
    ]
  },
})

/* ===================== 打菲按手列表（ADR-0016） ===================== */
