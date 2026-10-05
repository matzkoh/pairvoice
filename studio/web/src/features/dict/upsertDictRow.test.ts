import { expect, it } from 'vitest'

import { findDictRow, upsertDictRow } from './upsertDictRow'

const ROWS = [{ from: 'API', to: 'エーピーアイ', memo: '' }]

it('無い表記は末尾に足し、表記の前後の空白は落とす', () => {
  expect(upsertDictRow(ROWS, { from: ' PR ', to: 'ピーアール', memo: '' })).toEqual([
    ...ROWS,
    { from: 'PR', to: 'ピーアール', memo: '' },
  ])
})

it('同じ表記は位置を保ったまま置き換える', () => {
  expect(upsertDictRow(ROWS, { from: 'API', to: 'エーピーアイー', memo: 'm' })).toEqual([
    { from: 'API', to: 'エーピーアイー', memo: 'm' },
  ])
})

it('表記で既存の行を探す', () => {
  expect(findDictRow(ROWS, ' API ')).toEqual(ROWS[0])
  expect(findDictRow(ROWS, 'PR')).toBeUndefined()
})
