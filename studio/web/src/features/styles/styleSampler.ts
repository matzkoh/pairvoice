import type { SamplerOverrides } from '@/lib/api-types'

import { initialValues, type SamplerValues, toSamplerPayload } from '../profiles/samplerKnobs'

// 画面に出す値は、いま pairvoice が渡している値（config.toml とモデル既定）にスタイルを重ねたもの
export function styleValues(base: SamplerOverrides | undefined, style: SamplerOverrides) {
  return initialValues({ ...base, ...style })
}

// スタイルには config.toml と違う値の項目だけを残す。全項目を持たせると、あとで
// config.toml を直しても、そのスタイルで読む分にだけ効かなくなる
export function samplerDiff(
  values: SamplerValues,
  base: SamplerOverrides | undefined,
): { sampler: SamplerOverrides; errors: string[] } {
  const { sampler, errors } = toSamplerPayload(values)
  const baseline = new Map(Object.entries(toSamplerPayload(initialValues(base)).sampler))
  const diff: SamplerOverrides = Object.fromEntries(
    Object.entries(sampler).filter(([key, value]) => baseline.get(key) !== value),
  )
  return { sampler: diff, errors }
}
