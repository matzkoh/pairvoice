import type { Filter } from './reviewCounts'

// URL に載せる値。すべて任意にして、ヘッダーや ⌘K から search 無しで開けるようにする
export type ReviewSearch = { filter?: Filter; q?: string; visible?: number }

export const REVIEW_PAGE_SIZE = 100
export const DEFAULT_FILTER: Filter = 'unreviewed'

export function readReviewSearch(search: ReviewSearch) {
  //@ verify
  //@ ensures search.filter === undefined ==> \result.filter === DEFAULT_FILTER
  //@ ensures search.filter !== undefined ==> \result.filter === search.filter
  //@ ensures search.q === undefined ==> \result.q === ''
  //@ ensures search.q !== undefined ==> \result.q === search.q
  //@ ensures search.visible === undefined ==> \result.visible === REVIEW_PAGE_SIZE
  //@ ensures search.visible !== undefined ==> \result.visible === search.visible
  return {
    filter: search.filter ?? DEFAULT_FILTER,
    q: search.q ?? '',
    visible: search.visible ?? REVIEW_PAGE_SIZE,
  }
}

// 絞り込み・検索を変えたら表示件数を先頭に戻す
export function setFilter(prev: ReviewSearch, filter: Filter): ReviewSearch {
  //@ verify
  //@ ensures \result.filter === filter && \result.q === prev.q && \result.visible === undefined
  return { ...prev, filter, visible: undefined }
}

export function setQuery(prev: ReviewSearch, q: string): ReviewSearch {
  //@ verify
  //@ ensures q === '' ==> \result.q === undefined
  //@ ensures q !== '' ==> \result.q === q
  //@ ensures \result.filter === prev.filter && \result.visible === undefined
  return { ...prev, q: q === '' ? undefined : q, visible: undefined }
}

export function showMore(prev: ReviewSearch): ReviewSearch {
  //@ verify
  //@ ensures \result.visible === readReviewSearch(prev).visible + REVIEW_PAGE_SIZE
  //@ ensures \result.filter === prev.filter && \result.q === prev.q
  return { ...prev, visible: readReviewSearch(prev).visible + REVIEW_PAGE_SIZE }
}
