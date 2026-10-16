/**
 * 工位机扫码反馈：短提示音（Web Audio 现场合成，范围决策 B）。
 *
 * ⚠️ **不引入音频资源文件**：`06 §2.5` 要求"播放一声短提示音"，但音频资源会带来
 * 网络加载路径与离线可用性问题（工厂网络差）。用 `AudioContext` + `OscillatorNode`
 * 现场合成一个几十毫秒的正弦音即可，跨浏览器、零资源、离线可用。
 *
 * ⚠️ **必须 feature-detect**：jsdom / 部分旧浏览器没有 `AudioContext`，直接
 * `new AudioContext()` 会抛异常 —— 而扫码反馈音抛异常会**打断扫码主流程**。
 * 这里拿不到就静默返回：没声音总比卡住产线好。
 */

export type ToneKind = 'ok' | 'warn' | 'error'

/** 成功绿闪 / 重复黄闪 / 失败红闪的时长（`06 §2.5`：绿框闪 200ms）。 */
export const FLASH_MS = 200

//: 三种反馈的音高（Hz）。成功高、重复中、失败低，操作员听音即可分辨，不必看屏幕。
const TONE_FREQ: Record<ToneKind, number> = { ok: 880, warn: 520, error: 300 }
const TONE_MS: Record<ToneKind, number> = { ok: 80, warn: 120, error: 180 }

type AudioContextCtor = new () => AudioContext

function getAudioContextCtor(): AudioContextCtor | null {
  const globalRef = globalThis as typeof globalThis & {
    AudioContext?: AudioContextCtor
    webkitAudioContext?: AudioContextCtor
  }
  return globalRef.AudioContext ?? globalRef.webkitAudioContext ?? null
}

/** 播放一声短提示音；环境不支持时静默跳过（绝不抛错打断扫码）。 */
export function playTone(kind: ToneKind): void {
  const Ctor = getAudioContextCtor()
  if (Ctor === null) return
  try {
    const ctx = new Ctor()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'sine'
    osc.frequency.value = TONE_FREQ[kind]
    // 留一点音量但不过响：车间环境嘈杂，太轻听不见、太响吓人。
    gain.gain.value = 0.15
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start()
    osc.stop(ctx.currentTime + TONE_MS[kind] / 1000)
    osc.onended = () => {
      void ctx.close()
    }
  } catch {
    // 静默：反馈音是"锦上添花"，它失败不能影响扫码结果。
  }
}
