import { KNOBS, MODEL_DEFAULTS, parseOptionalNumber, type SamplerValues } from './samplerKnobs'

// config.toml に持ち帰らない項目。どちらもサンプラー設定ではなく、その1回の
// 生成だけの指定である（固定してしまうと全ての読み上げが同じ seed・同じ長さになる）。
const NOT_PERSISTED = new Set(['rng_seed', 'seconds'])

function format(value: number | string | boolean): string {
  if (typeof value === 'string') return JSON.stringify(value)
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

// モデル既定と異なる値だけを出す。既定と同じ値まで全部書くと config.toml が膨れ、
// 「意図して外した値だけを書く場所」という性質が薄れる。
export function tomlSnippet(values: SamplerValues): string | null {
  const lines: string[] = []
  for (const knob of KNOBS) {
    if (NOT_PERSISTED.has(knob.key)) continue
    const value = values[knob.key]
    if (value === MODEL_DEFAULTS[knob.key]) continue
    if (knob.kind === 'optionalNumber') {
      const parsed = parseOptionalNumber(value)
      // 数値として読めない値は送信時に errors で申告されるので、ここでは黙って落とす
      if (parsed === 'blank' || parsed === 'invalid') continue
      lines.push(`${knob.key} = ${format(parsed)}`)
      continue
    }
    lines.push(`${knob.key} = ${format(value)}`)
  }
  if (lines.length === 0) return null
  return `[tts.sampler]\n${lines.join('\n')}\n`
}
