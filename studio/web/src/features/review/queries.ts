import { queryOptions, useQuery } from '@tanstack/react-query'

import { apiGet } from '@/lib/api'

import type { CorpusResponse } from '../../../../shared/api-types'
import { computeReviewCounts } from './reviewCounts'

// 1リクエストで取得する件数。
const REVIEW_FETCH_CHUNK = 500

// コーパスの全件を読む。/api/corpus はフィルタも検索クエリも受け付けないので、
// 絞り込み・検索・件数表示を全件に対して正しく保つには全件を手元に持つ必要がある
// visible をサーバーの limit にはしない。
async function fetchAllCorpus(): Promise<CorpusResponse> {
  const first = await apiGet<CorpusResponse>(`/api/corpus?limit=${REVIEW_FETCH_CHUNK}`)
  const offsets: number[] = []
  // 1ページ目が空なら total が壊れている。続きを取っても進まないので打ち切る
  if (first.items.length > 0) {
    for (let offset = first.items.length; offset < first.total; offset += REVIEW_FETCH_CHUNK) {
      offsets.push(offset)
    }
  }
  const rest = await Promise.all(
    offsets.map((offset) =>
      apiGet<CorpusResponse>(`/api/corpus?limit=${REVIEW_FETCH_CHUNK}&offset=${offset}`),
    ),
  )
  const items = first.items.slice()
  for (const page of rest) {
    if (page.items.length === 0) break // 取得が進まなくなった場合の保険。後ろのページとの間に穴を空けない
    items.push(...page.items)
  }
  return { total: first.total, items, prompt_changed_at: first.prompt_changed_at }
}

// ウィンドウのフォーカスが戻るたびに全件（チャンク取得）を取り直さないよう
// staleTime を置く。既定の 0 だと refetchOnWindowFocus と組み合わさり、サイドバーに
// 常駐する useUnreviewedCount がフォーカスのたびに corpus 全件を取り直す。
// 1分は、画面を開きっぱなしにしている間にフックが読み上げを増やす頻度に対して
// 許容できる遅れとして選んだ値。投票・アーカイブは setQueryData で即時に反映するので
// この値に関係しない。
const CORPUS_STALE_TIME_MS = 60_000

export function corpusQueryOptions() {
  return queryOptions({
    queryKey: ['corpus'] as const,
    queryFn: fetchAllCorpus,
    staleTime: CORPUS_STALE_TIME_MS,
  })
}

// サイドバーの未レビュー件数バッジ。suspense にすると全件取得が終わるまで
// サイドバーごと止まり、レビュー以外の画面の表示までブロックしてしまう。
// 通常の useQuery で読み、読めるまでは null（呼び出し側は何も出さない）。
export function useUnreviewedCount(): number | null {
  const { data } = useQuery(corpusQueryOptions())
  return data ? computeReviewCounts(data.items).unreviewed : null
}
