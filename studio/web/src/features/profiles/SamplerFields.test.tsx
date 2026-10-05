/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { SamplerFields } from './SamplerFields'
import { initialValues } from './samplerKnobs'

afterEach(cleanup)

it('Sampling の10項目を出し、Advanced は折りたたむ', () => {
  render(<SamplerFields values={initialValues()} onChange={vi.fn()} disabled={false} />)

  expect(screen.getByLabelText(/Num Steps/)).toBeTruthy()
  // details の中にあっても getByLabelText は見つけるので、開閉は open 属性で見る
  expect(screen.getByText('Advanced').closest('details')?.open).toBe(false)
})

// 説明はラベルの title に入れる。ⓘ は印だけで、入力欄のアクセシブル名を汚さない
it('ノブの説明をラベルの title で読める', () => {
  render(<SamplerFields values={initialValues()} onChange={vi.fn()} disabled={false} />)

  expect(screen.getByText(/Num Steps/).title).toContain('Euler')
  // ⓘ をラベルの外に置いているので、入力欄の名前は完全一致で引けるままになる
  expect(screen.getByLabelText('Context KV Cache')).toBeTruthy()
})

it('スライダーを動かすと数値で通知する', () => {
  const onChange = vi.fn()
  render(<SamplerFields values={initialValues()} onChange={onChange} disabled={false} />)

  fireEvent.change(screen.getByLabelText(/Num Steps/), { target: { value: '60' } })

  expect(onChange).toHaveBeenCalledWith('num_steps', 60)
})

it('チェックボックスは真偽値で通知する', () => {
  const onChange = vi.fn()
  render(<SamplerFields values={initialValues()} onChange={onChange} disabled={false} />)

  fireEvent.click(screen.getByLabelText('Context KV Cache'))

  expect(onChange).toHaveBeenCalledWith('context_kv_cache', false)
})

it('空欄可の項目は生の文字列で通知する（入力途中を丸めない）', () => {
  const onChange = vi.fn()
  render(<SamplerFields values={initialValues()} onChange={onChange} disabled={false} />)

  fireEvent.change(screen.getByLabelText('Seed'), { target: { value: '0.' } })

  expect(onChange).toHaveBeenCalledWith('rng_seed', '0.')
})

it('Time Schedule が linear のとき Sway Coeff は無効', () => {
  const { unmount } = render(
    <SamplerFields values={initialValues()} onChange={vi.fn()} disabled={false} />,
  )
  expect(screen.getByLabelText(/Sway Coeff/)).toHaveProperty('disabled', true)
  unmount()

  render(
    <SamplerFields
      values={initialValues({ t_schedule_mode: 'sway' })}
      onChange={vi.fn()}
      disabled={false}
    />,
  )
  expect(screen.getByLabelText(/Sway Coeff/)).toHaveProperty('disabled', false)
})

it('生成中は全項目を無効にする', () => {
  render(<SamplerFields values={initialValues()} onChange={vi.fn()} disabled={true} />)

  expect(screen.getByLabelText(/Num Steps/)).toHaveProperty('disabled', true)
  expect(screen.getByLabelText('Seed')).toHaveProperty('disabled', true)
})
