import { expect, it } from 'vitest'

import { REVIEW_PAGE_SIZE, readReviewSearch, setFilter, setQuery, showMore } from './searchState'

it('何も無ければ未レビュー・検索なし・先頭100件', () => {
  expect(readReviewSearch({})).toEqual({ filter: 'unreviewed', q: '', visible: REVIEW_PAGE_SIZE })
})

it('絞り込みと検索を変えると表示件数が先頭に戻る', () => {
  const opened = { filter: 'all' as const, visible: 300 }
  expect(setFilter(opened, 'bad')).toEqual({ filter: 'bad', visible: undefined })
  expect(setQuery(opened, 'API')).toEqual({ filter: 'all', q: 'API', visible: undefined })
  expect(setQuery(opened, '').q).toBeUndefined()
})

it('もっと見るは表示件数を1ページ分伸ばす', () => {
  expect(showMore({}).visible).toBe(REVIEW_PAGE_SIZE * 2)
  expect(showMore({ visible: 200 }).visible).toBe(300)
})
