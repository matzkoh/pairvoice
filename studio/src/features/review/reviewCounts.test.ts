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
  // どの行が残るかは注釈で証明している。並び順と重複は証明の外なので、ここで押さえる
  it('残す行を元の順に1度ずつ返す', () => {
    const items = [
      item({ message_id: '1', summary: 'match here', input: 'x' }),
      item({ message_id: '2', summary: 'x', input: 'y' }),
      item({ message_id: '3', summary: 'x', input: 'match here' }),
    ]
    expect(filterReviews(items, 'all', 'match').map((r) => r.message_id)).toEqual(['1', '3'])
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
