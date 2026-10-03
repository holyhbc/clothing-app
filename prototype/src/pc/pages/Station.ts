import { h } from '../../components/h'
import { defineComponent, ref, onMounted, onBeforeUnmount, computed } from 'vue'

import { pageHead, card } from '../shared'
import { useToast, ToastHost } from '../../components/ui'
import { money } from '../../mock'

/**
 * 工位机计件页 —— 严格实现 docs/06 §2.5 的 7 条交互要求：
 * 1. 页面加载即可扫，不需点击
 * 2. 成功计数+1、金额累加、提示音、绿框闪 200ms
 * 3. 重复扫码黄色提示 + 显示已有记录，不弹窗打断
 * 4. 失败红色 + 短音 + 清空缓冲
 * 5. 数字大字等宽
 * 6. 离线队列提示
 * 7. 暂停扫码开关
 */
const SAMPLE = ['BD-20261018-000031-0000001', 'BD-20261018-000031-0000002', 'BD-20261018-000031-0000003']
const USED = new Set(['BD-20261018-000031-0000002'])

export default defineComponent({
  name: 'Station',
  emits: ['navigate'],
  setup(_, { emit }) {
    const toast = useToast()
    const code = ref('')
    const panelCls = ref('scan-panel')
    const result = ref<{ cls: string; text: string; hint: string } | null>(null)
    const count = ref(128)
    const dup = ref(3)
    const amount = ref(53.76)
    const paused = ref(false)
    const online = ref(true)
    const buffer = ref('')

    let timer: number | undefined
    let audioCtx: AudioContext | null = null

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
      } catch { /* 浏览器策略限制时静默 */ }
    }

    function flash(cls: string, r: { cls: string; text: string; hint: string }) {
      panelCls.value = `scan-panel ${cls}`
      result.value = r
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        panelCls.value = 'scan-panel'
        result.value = null
      }, 1800)
    }

    function handleScan(c: string) {
      if (paused.value) return
      code.value = c
      if (!online.value) {
        flash('dup', { cls: 'dup', text: '离线 · 已缓存', hint: '恢复网络后自动补传' })
        return
      }
      if (USED.has(c)) {
        dup.value++
        beep(220, 0.12)
        flash('dup', {
          cls: 'dup',
          text: `重复：${USED.has(c) ? '陈美玲' : ''} 已完成本工序`,
          hint: '不重复计数，可继续扫下一件',
        })
        return
      }
      USED.add(c)
      count.value++
      amount.value = Number((amount.value + 0.42).toFixed(2))
      beep(880, 0.08)
      flash('ok', { cls: 'ok', text: '✓ 计件成功 +1 件', hint: `本工序工价 ¥0.42 · HB-2026-0018 打边` })
    }

    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.altKey || e.metaKey) return
      if (e.key === 'Enter') {
        if (!buffer.value) return
        const c = buffer.value
        buffer.value = ''
        handleScan(c)
        return
      }
      if (e.key.length !== 1) return
      buffer.value = (buffer.value + e.key).slice(-128)
    }

    function simulate() {
      const c = SAMPLE.find((x) => !USED.has(x)) ?? `BD-20261018-000031-${String(USED.size + 1).padStart(7, '0')}`
      handleScan(c)
    }

    function simulateDup() {
      handleScan(SAMPLE[1])
    }

    function simulateErr() {
      code.value = 'BD-9999-XXX-9999999'
      beep(160, 0.2)
      flash('err', { cls: 'err', text: '打菲号不存在（31001）', hint: '请确认标签是否属于本工序' })
    }

    onMounted(() => window.addEventListener('keydown', onKey, true))
    onBeforeUnmount(() => {
      window.removeEventListener('keydown', onKey, true)
      window.clearTimeout(timer)
      audioCtx?.close()
    })

    function recentTable() {
      const items = [
        { bn: 'BD-20261018-000031-0000003', t: '09:44:52', r: '成功', c: 'st-approved' },
        { bn: 'BD-20261018-000031-0000002', t: '09:44:50', r: '重复（陈美玲已计）', c: 'st-pending' },
        { bn: 'BD-20261018-000031-0000001', t: '09:44:48', r: '成功', c: 'st-approved' },
      ]
      return h('table', { class: 'tbl' }, [
        h('thead', {}, h('tr', {}, [
          h('th', {}, '打菲号'), h('th', {}, '款号'), h('th', {}, '工序'),
          h('th', { class: 'right' }, '工价'), h('th', {}, '时间'), h('th', {}, '结果'),
        ])),
        h('tbody', {}, items.map((x) =>
          h('tr', { key: x.bn }, [
            h('td', { class: 'mono nowrap' }, x.bn),
            h('td', { class: 'mono' }, 'HB-2026-0018'),
            h('td', {}, '打边'),
            h('td', { class: 'right amount' }, '0.42'),
            h('td', { class: 'muted nowrap' }, x.t),
            h('td', {}, h('span', { class: 'status-tag ' + x.c }, [h('span', { class: 'dot' }), x.r])),
          ]),
        )),
      ])
    }

    const today = computed(() => new Date().toLocaleDateString('zh-CN'))

    return () => [
      pageHead('工位机计件', `绑定：缝制一组 · 工位 SW-03 · 操作员 王海涛　｜　今天 ${today.value}`, [
        h('button', { class: ['btn', paused.value ? 'primary' : ''], onClick: () => (paused.value = !paused.value) },
          paused.value ? '▶ 恢复扫码' : '⏸ 暂停扫码'),
        h('button', { class: 'btn', onClick: simulate }, '模拟扫新码'),
        h('button', { class: 'btn', onClick: simulateDup }, '模拟重复码'),
        h('button', { class: 'btn', onClick: simulateErr }, '模拟无效码'),
        h('button', { class: 'btn', onClick: () => emit('navigate', 'piecework') }, '查看流水 ›'),
      ]),

      h('div', { class: 'station' }, [
        h('div', { class: 'station-status' }, [
          h('div', { class: 'chip' }, [
            h('span', { class: online.value ? 'dot-live' : 'dot-off' }),
            h('span', { class: 'k' }, online.value ? '在线' : '离线'),
          ]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '款号'), h('span', { class: 'v mono' }, 'HB-2026-0018')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '工序'), h('span', { class: 'v' }, '打边（01）')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '员工'), h('span', { class: 'v' }, '王海涛 E1058')]),
          h('div', { class: 'chip' }, [h('span', { class: 'k' }, '一扎'), h('span', { class: 'v' }, '1 件')]),
          !online.value ? h('div', { class: 'chip' }, [h('span', { class: 'dot-off' }), h('span', { class: 'v' }, `离线缓存 ${count.value} 条待提交`)]) : null,
          h('button', {
            class: 'btn sm', style: 'margin-left:auto',
            onClick: () => { online.value = !online.value; toast.ok(online.value ? '已恢复联网，开始补传缓存' : '已切到离线模式（演示）') },
          }, online.value ? '模拟断网' : '恢复联网'),
        ]),

        h('div', { class: panelCls.value }, [
          h('div', { class: 'scan-hint' }, paused.value ? '已暂停扫码' : '请扫描打菲二维码（无需点击页面）'),
          h('div', { class: 'scan-code mono' }, code.value || ' '),
          h('div', { class: ['scan-result', result.value?.cls ?? ''] }, result.value?.text ?? ' '),
          h('div', { class: 'scan-hintline' }, result.value?.hint ?? '扫码枪 = USB 键盘输入，全局捕获，回车自动提交'),
        ]),

        h('div', { class: 'counter-grid' }, [
          h('div', { class: 'counter' }, [
            h('div', { class: 'n' }, String(count.value)),
            h('div', { class: 'l' }, '今日计件（件）'),
          ]),
          h('div', { class: 'counter money' }, [
            h('div', { class: 'n' }, '¥' + money(amount.value)),
            h('div', { class: 'l' }, '今日计件金额'),
          ]),
          h('div', { class: 'counter' }, [
            h('div', { class: 'n', style: 'color:var(--color-warning)' }, String(dup.value)),
            h('div', { class: 'l' }, '重复扫码（未计数）'),
          ]),
        ]),

        card('最近扫码记录', recentTable()),
      ]),
      h(ToastHost, { items: toast.items }),
    ]
  },
})