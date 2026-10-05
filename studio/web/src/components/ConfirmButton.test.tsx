/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { ConfirmButton } from './ConfirmButton'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

it('1回目は確認の文言に変わるだけで、2回目で確定する', () => {
  const onConfirm = vi.fn()
  render(<ConfirmButton idle="中断" armed="本当に中断しますか" onConfirm={onConfirm} />)
  fireEvent.click(screen.getByRole('button', { name: '中断' }))
  expect(onConfirm).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '本当に中断しますか' }))
  expect(onConfirm).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('button', { name: '中断' })).toBeTruthy()
})

it('フォーカスが外れると確認前に戻る', () => {
  const onConfirm = vi.fn()
  render(<ConfirmButton idle="中断" armed="本当に中断しますか" onConfirm={onConfirm} />)
  const button = screen.getByRole('button', { name: '中断' })
  fireEvent.click(button)
  fireEvent.blur(button)
  expect(screen.getByRole('button', { name: '中断' })).toBeTruthy()
  expect(onConfirm).not.toHaveBeenCalled()
})

it('5秒放置すると確認前に戻る', () => {
  vi.useFakeTimers()
  render(<ConfirmButton idle="中断" armed="本当に中断しますか" onConfirm={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: '中断' }))
  act(() => {
    vi.advanceTimersByTime(5000)
  })
  expect(screen.getByRole('button', { name: '中断' })).toBeTruthy()
})
