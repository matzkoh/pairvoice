/** @vitest-environment jsdom */
import { expect, it } from 'vitest'

import { selectedTextWithin } from './selection'

it('要素の中で選択した文字列だけを返し、外にはみ出す選択は無視する', () => {
  document.body.innerHTML = '<p id="in">API を直しました</p><p id="out">外</p>'
  const inside = document.getElementById('in')!
  const selection = window.getSelection()!
  const range = document.createRange()
  range.setStart(inside.firstChild!, 0)
  range.setEnd(inside.firstChild!, 3)
  selection.removeAllRanges()
  selection.addRange(range)
  expect(selectedTextWithin(inside, selection)).toBe('API')

  range.setEnd(document.getElementById('out')!.firstChild!, 1)
  selection.removeAllRanges()
  selection.addRange(range)
  expect(selectedTextWithin(inside, selection)).toBe('')
})
