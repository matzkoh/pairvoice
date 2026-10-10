import { expect, it } from 'vitest'

import { initialValues } from './samplerKnobs'
import { tomlSnippet } from './tomlSnippet'

it('モデル既定のままなら null（貼る必要がない）', () => {
  expect(tomlSnippet(initialValues())).toBeNull()
})

it('既定と異なる項目だけを [tts.sampler] に出す', () => {
  const snippet = tomlSnippet({ ...initialValues(), num_steps: 60 })

  expect(snippet).toBe('[tts.sampler]\nnum_steps = 60\n')
})

it('文字列は引用符で、真偽値は小文字で出す', () => {
  const snippet = tomlSnippet({
    ...initialValues(),
    t_schedule_mode: 'sway',
    context_kv_cache: false,
  })

  expect(snippet).toBe('[tts.sampler]\nt_schedule_mode = "sway"\ncontext_kv_cache = false\n')
})

it('埋めた optionalNumber も差分として出す', () => {
  expect(tomlSnippet({ ...initialValues(), rescale_k: '0.6' })).toBe(
    '[tts.sampler]\nrescale_k = 0.6\n',
  )
})

it('rng_seed と seconds は含めない（サンプラー設定ではない）', () => {
  const snippet = tomlSnippet({ ...initialValues(), rng_seed: '7', seconds: '3.5' })

  expect(snippet).toBeNull()
})

it('数として読めない optionalNumber は含めない', () => {
  expect(tomlSnippet({ ...initialValues(), rescale_k: 'あ' })).toBeNull()
})
