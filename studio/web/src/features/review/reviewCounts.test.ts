import { describe, expect, it } from 'vitest'

import type { CorpusItem } from '@/lib/api-types'

import { bulkArchiveTargets, computeReviewCounts, filterReviews } from './reviewCounts'

function item(overrides: Partial<CorpusItem>): CorpusItem {
  return {
    ts: '2026-07-29 10:00:00',
    message_id: 'm',
    input: 'input text',
    summary: 'summary text',
    verdict: null,
    ideal: null,
    archived: false,
    stale: false,
    ...overrides,
  }
}

describe('computeReviewCounts', () => {
  it('all/unreviewed/bad は archived と stale を除いた集合から数える', () => {
    const items = [
      item({ message_id: '1' }), // 未レビュー・active
      item({ message_id: '2', verdict: 'good' }), // active
      item({ message_id: '3', verdict: 'bad' }), // active・bad
      item({ message_id: '4', archived: true }), // archived
      item({ message_id: '5', stale: true }), // stale
    ]
    const counts = computeReviewCounts(items)
    expect(counts.all).toBe(3)
    expect(counts.unreviewed).toBe(1)
    expect(counts.bad).toBe(1)
    expect(counts.archived).toBe(1)
    expect(counts.stale).toBe(1)
    expect(counts.reviewedUnarchived).toBe(2)
  })

  it('archived の件数は stale かどうかを問わない', () => {
    const items = [
      item({ message_id: '1', archived: true, stale: false }),
      item({ message_id: '2', archived: true, stale: true }),
    ]
    expect(computeReviewCounts(items).archived).toBe(2)
  })

  it('stale の件数は archived を除く', () => {
    const items = [
      item({ message_id: '1', stale: true, archived: false }),
      item({ message_id: '2', stale: true, archived: true }),
    ]
    expect(computeReviewCounts(items).stale).toBe(1)
  })
})

describe('filterReviews', () => {
  // 「チップの件数と検索は visible 件だけを見ていると静かに間違う」ことへの
  // 回帰確認。101件目以降にしか出てこない語で検索してもヒットすることを、
  // visible には一切依存しない filterReviews 単体で保証する。
  it('検索は渡された配列の全件が対象で、visible による件数の制限を持たない', () => {
    const items = Array.from({ length: 150 }, (_, i) =>
      item({ message_id: `m${i}`, summary: i === 149 ? 'めずらしい単語' : `summary ${i}` }),
    )
    const result = filterReviews(items, 'all', 'めずらしい単語')
    expect(result).toHaveLength(1)
    expect(result[0]!.message_id).toBe('m149')
  })

  it('検索は summary と input の両方に部分一致する', () => {
    const items = [
      item({ message_id: '1', summary: 'match here', input: 'x' }),
      item({ message_id: '2', summary: 'x', input: 'match here' }),
      item({ message_id: '3', summary: 'x', input: 'y' }),
    ]
    const result = filterReviews(items, 'all', 'match here')
    expect(result.map((r) => r.message_id)).toEqual(['1', '2'])
  })

  it('all/unreviewed/bad フィルタは archived・stale を隠す', () => {
    const items = [
      item({ message_id: '1' }),
      item({ message_id: '2', archived: true }),
      item({ message_id: '3', stale: true }),
    ]
    expect(filterReviews(items, 'all', '').map((r) => r.message_id)).toEqual(['1'])
  })

  it('archived フィルタは archived だけを返す', () => {
    const items = [item({ message_id: '1' }), item({ message_id: '2', archived: true })]
    expect(filterReviews(items, 'archived', '').map((r) => r.message_id)).toEqual(['2'])
  })

  it('stale フィルタは stale だけを返す', () => {
    const items = [item({ message_id: '1' }), item({ message_id: '2', stale: true })]
    expect(filterReviews(items, 'stale', '').map((r) => r.message_id)).toEqual(['2'])
  })

  it('unreviewed フィルタは verdict が付いている行を除く', () => {
    const items = [item({ message_id: '1' }), item({ message_id: '2', verdict: 'good' })]
    expect(filterReviews(items, 'unreviewed', '').map((r) => r.message_id)).toEqual(['1'])
  })

  it('bad フィルタは verdict !== "bad" を除く', () => {
    const items = [
      item({ message_id: '1', verdict: 'good' }),
      item({ message_id: '2', verdict: 'bad' }),
    ]
    expect(filterReviews(items, 'bad', '').map((r) => r.message_id)).toEqual(['2'])
  })
})

describe('bulkArchiveTargets / reviewedUnarchived', () => {
  // ボタン活性条件（stale除外）と実際の対象（stale込み）が食い違っている。
  // 修正ではなく現状の挙動なので、その食い違いごと固定する回帰テスト。
  it('reviewedUnarchived は stale を除外するが bulkArchiveTargets は含む', () => {
    const items = [
      item({ message_id: '1', verdict: 'good', stale: true }),
      item({ message_id: '2', verdict: 'bad' }),
    ]
    expect(computeReviewCounts(items).reviewedUnarchived).toBe(1)
    expect(bulkArchiveTargets(items).map((r) => r.message_id)).toEqual(['1', '2'])
  })

  it('archived な行はどちらからも除外する', () => {
    const items = [item({ message_id: '1', verdict: 'good', archived: true })]
    expect(computeReviewCounts(items).reviewedUnarchived).toBe(0)
    expect(bulkArchiveTargets(items)).toHaveLength(0)
  })
})
