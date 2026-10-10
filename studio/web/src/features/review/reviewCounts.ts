import type { CorpusItem } from '@/lib/api-types'

export const FILTERS = ['all', 'unreviewed', 'bad', 'archived', 'stale'] as const
export type Filter = (typeof FILTERS)[number]

export function isFilter(value: unknown): value is Filter {
  return (FILTERS as readonly unknown[]).includes(value)
}

// 呼び出し側は必ず全件（CorpusResponse.items）を渡すこと。visible で先に絞った
// 配列を渡すと、件数表示と検索が「取得できた分だけ」を母集団にして静かに壊れる。
export function filterReviews(
  items: readonly CorpusItem[],
  filter: Filter,
  q: string,
): CorpusItem[] {
  return items.filter((r) => {
    if (q && !r.summary.includes(q) && !r.input.includes(q)) return false
    if (filter === 'archived') return r.archived
    if (r.archived) return false // アーカイブ以外の絞り込みではアーカイブ済みを表示しない
    if (filter === 'stale') return r.stale
    if (r.stale) return false
    if (filter === 'unreviewed' && r.verdict) return false
    if (filter === 'bad' && r.verdict !== 'bad') return false
    return true
  })
}

export type ReviewCounts = Record<Filter, number> & {
  // 一括アーカイブの活性条件。非アーカイブ・非stale・判定済みの件数
  reviewedUnarchived: number
}

// 絞り込みの件数・一括アーカイブの活性条件・サイドバーの未レビューバッジは、ここから1箇所で計算する。
// 母集団は必ず全件（CorpusResponse.items）。絞り込み済みの配列を渡すと「未レビュー」を
// 選んだ瞬間に「すべて」の件数が化ける
export function computeReviewCounts(items: readonly CorpusItem[]): ReviewCounts {
  const counts = { all: 0, unreviewed: 0, bad: 0, archived: 0, stale: 0, reviewedUnarchived: 0 }
  for (const r of items) {
    if (r.archived) counts.archived++
    else if (r.stale) counts.stale++
    else {
      counts.all++
      if (!r.verdict) counts.unreviewed++
      else counts.reviewedUnarchived++
      if (r.verdict === 'bad') counts.bad++
    }
  }
  return counts
}

// 一括アーカイブの実際の対象。活性条件と違って stale を問わない。対象を絞ると、
// 旧プロンプトの評価済みの行がいつまでも残り続ける。
export function bulkArchiveTargets(items: readonly CorpusItem[]): CorpusItem[] {
  return items.filter((r) => !r.archived && (r.verdict === 'good' || r.verdict === 'bad'))
}
