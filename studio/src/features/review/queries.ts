import { queryOptions, useQuery } from '@tanstack/react-query'

import { apiGet } from '@/lib/api'
import type { CorpusCounts, CorpusResponse } from '@/lib/api-types'

// 1リクエストで取得する件数。
const REVIEW_FETCH_CHUNK = 500

// コーパスの全件を読む。/api/corpus はフィルタも検索クエリも受け付けないので、
// 絞り込み・検索・件数表示を全件に対して正しく保つには全件を手元に持つ必要がある
// visible をサーバーの limit にはしない。
async function fetchAllCorpus(): Promise<CorpusResponse> {
  const first = await apiGet<CorpusResponse>(`/corpus?limit=${REVIEW_FETCH_CHUNK}`)
  const offsets: number[] = []
  // 1ページ目が空なら total が壊れている。続きを取っても進まないので打ち切る
  if (first.items.length > 0) {
    for (let offset = first.items.length; offset < first.total; offset += REVIEW_FETCH_CHUNK) {
      offsets.push(offset)
    }
  }
  const rest = await Promise.all(
    offsets.map((offset) =>
      apiGet<CorpusResponse>(`/corpus?limit=${REVIEW_FETCH_CHUNK}&offset=${offset}`),
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
// レビュー画面がフォーカスのたびに corpus 全件を取り直す。
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

export const CORPUS_COUNTS_KEY = ['corpus', 'counts'] as const

// サイドバーの未レビュー件数バッジ。どの画面にも出るので、全件は読まずに件数だけを取る。
// 投票・アーカイブのあとは useReviewActions が取り直させる。
// suspense にするとサイドバーごと止まるので、読めるまでは null（呼び出し側は何も出さない）。
export function useUnreviewedCount(): number | null {
  const { data } = useQuery({
    queryKey: CORPUS_COUNTS_KEY,
    queryFn: () => apiGet<CorpusCounts>('/corpus/counts'),
    staleTime: CORPUS_STALE_TIME_MS,
  })
  return data?.unreviewed ?? null
}
