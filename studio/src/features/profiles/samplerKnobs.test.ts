import { expect, it } from 'vitest'

import {
  KNOBS,
  MODEL_DEFAULTS,
  type SamplerKey,
  initialValues,
  toSamplerPayload,
} from './samplerKnobs'

it('17項目すべてにノブがあり、Advanced は7項目', () => {
  expect(KNOBS).toHaveLength(17)
  expect(KNOBS.filter((k) => k.group === 'advanced')).toHaveLength(7)
})

// Python 側の test_sampler_config_and_overrides_share_the_same_fields と対になる。
// SamplerOverrides に項目を足しても KNOBS に足し忘れると、pairvoice には効くのに
// 画面に出てこない項目ができる（型は KNOBS の網羅を強制しない）。
it('ノブのキーが SamplerOverrides の17項目と過不足なく一致する', () => {
  // SamplerOverrides は全項目 optional なので、型からキー一覧は取れない。
  // 仕様が固定している順序をそのまま並べ、ずれたら落ちるようにする
  const expected: SamplerKey[] = [
    'num_steps',
    'cfg_guidance_mode',
    'cfg_scale_text',
    'cfg_scale_caption',
    'cfg_scale_speaker',
    't_schedule_mode',
    'sway_coeff',
    'duration_scale',
    'seconds',
    'rng_seed',
    'cfg_min_t',
    'cfg_max_t',
    'context_kv_cache',
    'speaker_kv_scale',
    'truncation_factor',
    'rescale_k',
    'rescale_sigma',
  ]

  expect(KNOBS.map((knob) => knob.key)).toEqual(expected)
})

// キー名だけでは効き方が読めないので、説明の無いノブを作らせない
it('17項目すべてに説明がある', () => {
  const missing = KNOBS.filter((knob) => knob.hint.trim() === '').map((knob) => knob.key)

  expect(missing).toEqual([])
})

it('初期値はモデル既定で、/health の値がその上に重なる', () => {
  const values = initialValues({ cfg_scale_speaker: 1.5, t_schedule_mode: 'sway' })

  expect(values.num_steps).toBe(40) // 触られていない項目はモデル既定のまま
  expect(values.cfg_scale_speaker).toBe(1.5)
  expect(values.t_schedule_mode).toBe('sway')
})

it('/health が無くてもモデル既定で埋まる', () => {
  expect(initialValues()).toEqual(MODEL_DEFAULTS)
})

it('空欄の optionalNumber はペイロードに載らない', () => {
  const { sampler, errors } = toSamplerPayload(initialValues())

  expect(errors).toEqual([])
  expect('seconds' in sampler).toBe(false)
  expect('rng_seed' in sampler).toBe(false)
  expect('speaker_kv_scale' in sampler).toBe(false)
  expect('truncation_factor' in sampler).toBe(false)
  expect('rescale_k' in sampler).toBe(false)
  expect('rescale_sigma' in sampler).toBe(false)
})

it('空欄でない項目は常に送る（触っていないから既定に落ちる、という暗黙の経路を作らない）', () => {
  const { sampler } = toSamplerPayload(initialValues())

  expect(sampler.num_steps).toBe(40)
  expect(sampler.cfg_guidance_mode).toBe('independent')
  expect(sampler.context_kv_cache).toBe(true)
  expect(sampler.cfg_min_t).toBe(0.5)
})

it('optionalNumber は数として読めれば載る', () => {
  const { sampler, errors } = toSamplerPayload({
    ...initialValues(),
    seconds: '3.5',
    rescale_k: '0.6',
  })

  expect(errors).toEqual([])
  expect(sampler.seconds).toBe(3.5)
  expect(sampler.rescale_k).toBe(0.6)
})

it('optionalNumber が数として読めなければエラーを返し、その項目を送らない', () => {
  const { sampler, errors } = toSamplerPayload({ ...initialValues(), seconds: 'あ' })

  expect(errors).toHaveLength(1)
  expect(errors[0]).toContain('seconds')
  expect('seconds' in sampler).toBe(false)
})
