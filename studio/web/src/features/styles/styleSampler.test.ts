import { expect, it } from 'vitest'

import { samplerDiff, styleValues } from './styleSampler'

it('config.toml と違う値の項目だけを残す', () => {
  const base = { cfg_scale_speaker: 3 }
  const values = styleValues(base, { duration_scale: 1.2 })

  expect(values.cfg_scale_speaker).toBe(3)
  expect(samplerDiff(values, base)).toEqual({ sampler: { duration_scale: 1.2 }, errors: [] })
})

it('config.toml と同じ値に戻した項目は落とす', () => {
  const base = { cfg_scale_speaker: 3 }
  const values = styleValues(base, { cfg_scale_speaker: 4 })
  values.cfg_scale_speaker = 3

  expect(samplerDiff(values, base).sampler).toEqual({})
})

it('空欄可の項目は入れたときだけ残し、読めない値はエラーにする', () => {
  const values = styleValues(undefined, { rng_seed: 7 })
  expect(samplerDiff(values, undefined).sampler).toEqual({ rng_seed: 7 })

  values.seconds = 'abc'
  expect(samplerDiff(values, undefined).errors).toEqual(['seconds は数値か空欄にしてください'])
})
