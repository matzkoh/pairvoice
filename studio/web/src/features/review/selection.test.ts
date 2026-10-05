/** @vitest-environment jsdom */
import { expect, it } from 'vitest'

import { nextSelection, selectedTextWithin, selectionAfterRemoval } from './selection'

const IDS = ['a', 'b', 'c']

it('未選択から j は先頭、k は末尾', () => {
  expect(nextSelection(IDS, null, 1)).toBe('a')
  expect(nextSelection(IDS, null, -1)).toBe('c')
})

it('端では止まる', () => {
  expect(nextSelection(IDS, 'c', 1)).toBe('c')
  expect(nextSelection(IDS, 'a', -1)).toBe('a')
})

it('選択中の行が一覧から消えていたら、未選択と同じ扱いにする', () => {
  expect(nextSelection(IDS, 'z', 1)).toBe('a')
})

it('空の一覧では何も選ばない', () => {
  expect(nextSelection([], null, 1)).toBeNull()
  expect(selectionAfterRemoval([], 'a')).toBeNull()
})

it('消える行の次を選ぶ。末尾なら1つ前、唯一なら無し', () => {
  expect(selectionAfterRemoval(IDS, 'a')).toBe('b')
  expect(selectionAfterRemoval(IDS, 'c')).toBe('b')
  expect(selectionAfterRemoval(['a'], 'a')).toBeNull()
})

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
