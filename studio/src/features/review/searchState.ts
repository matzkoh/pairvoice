import type { Filter } from './reviewCounts'

// URL に載せる値。すべて任意にして、サイドバーや ⌘K から search 無しで開けるようにする
export type ReviewSearch = { filter?: Filter; q?: string; visible?: number }

export const REVIEW_PAGE_SIZE = 100
export const DEFAULT_FILTER: Filter = 'unreviewed'

export function readReviewSearch(search: ReviewSearch) {
  return {
    filter: search.filter ?? DEFAULT_FILTER,
    q: search.q ?? '',
    visible: search.visible ?? REVIEW_PAGE_SIZE,
  }
}

// 絞り込み・検索を変えたら表示件数を先頭に戻す
export function setFilter(prev: ReviewSearch, filter: Filter): ReviewSearch {
  return { ...prev, filter, visible: undefined }
}

export function setQuery(prev: ReviewSearch, q: string): ReviewSearch {
  return { ...prev, q: q === '' ? undefined : q, visible: undefined }
}

export function showMore(prev: ReviewSearch): ReviewSearch {
  return { ...prev, visible: readReviewSearch(prev).visible + REVIEW_PAGE_SIZE }
}
