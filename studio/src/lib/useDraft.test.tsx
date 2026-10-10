/** @vitest-environment jsdom */
import { act, renderHook } from '@testing-library/react'
import { expect, it } from 'vitest'

import { useDraft } from './useDraft'

it('サーバーの値が変われば取り直し、送った値が戻ってきただけなら打ち足した分を残す', () => {
  const { result, rerender } = renderHook(({ saved }) => useDraft(saved), {
    initialProps: { saved: 'A' },
  })
  act(() => {
    result.current.setDraft('B')
    result.current.markSubmitted('B')
  })
  act(() => result.current.setDraft('B+'))
  rerender({ saved: 'B' })
  expect(result.current.draft).toBe('B+')

  // 送っていない値への変化（別の場所での書き換え・復元）は取り直す
  rerender({ saved: 'C' })
  expect(result.current.draft).toBe('C')
})
