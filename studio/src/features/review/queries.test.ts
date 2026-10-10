import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiGet } from '@/lib/api'
import type { CorpusResponse } from '@/lib/api-types'

import { corpusQueryOptions } from './queries'

vi.mock('@/lib/api', () => ({
  apiGet: vi.fn(),
}))

afterEach(() => {
  vi.clearAllMocks()
})

function page(items: CorpusResponse['items'], total: number): CorpusResponse {
  return { items, total, prompt_changed_at: null }
}

function corpusItem(id: string): CorpusResponse['items'][number] {
  return {
    ts: '2026-07-29 10:00:00',
    message_id: id,
    input: 'in',
    summary: 'out',
    verdict: null,
    ideal: null,
    archived: false,
    stale: false,
  }
}

function chunk(prefix: string): CorpusResponse['items'] {
  return Array.from({ length: 500 }, (_, i) => corpusItem(`${prefix}${i}`))
}

describe('corpusQueryOptions().queryFn', () => {
  it('total に届くまで limit=500 チャンクでループ取得し、全件を1つにまとめる', async () => {
    const first = page([corpusItem('a'), corpusItem('b')], 3)
    const second = page([corpusItem('c')], 3)
    vi.mocked(apiGet).mockResolvedValueOnce(first).mockResolvedValueOnce(second)

    const result = await corpusQueryOptions().queryFn!({} as never)

    expect(vi.mocked(apiGet).mock.calls).toEqual([
      ['/corpus?limit=500'],
      ['/corpus?limit=500&offset=2'],
    ])
    expect(result.items.map((i) => i.message_id)).toEqual(['a', 'b', 'c'])
    expect(result.total).toBe(3)
  })

  it('1回で total に届けば1回しか呼ばない', async () => {
    vi.mocked(apiGet).mockResolvedValueOnce(page([corpusItem('a')], 1))

    await corpusQueryOptions().queryFn!({} as never)

    expect(vi.mocked(apiGet)).toHaveBeenCalledTimes(1)
  })

  it('取得が進まなくなったら（items が空）打ち切る安全弁を持つ', async () => {
    // total が実際の件数と食い違っている壊れたケースでも無限ループしない。
    vi.mocked(apiGet)
      .mockResolvedValueOnce(page([corpusItem('a')], 5))
      .mockResolvedValueOnce(page([], 5))

    const result = await corpusQueryOptions().queryFn!({} as never)

    expect(vi.mocked(apiGet)).toHaveBeenCalledTimes(2)
    expect(result.items).toHaveLength(1)
  })

  it('2ページ目以降はまとめて並行に取り、offset の順につなぐ', async () => {
    let releaseSecond: (() => void) | undefined
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === '/corpus?limit=500') return page(chunk('a'), 1200)
      if (path.endsWith('offset=500')) {
        // 後ろのページが先に返っても順序が崩れないことを見るため、2ページ目を遅らせる
        await new Promise<void>((resolve) => {
          releaseSecond = resolve
        })
        return page(chunk('b'), 1200)
      }
      if (path.endsWith('offset=1000')) return page(chunk('c').slice(0, 200), 1200)
      throw new Error(`unexpected path: ${path}`)
    })

    const pending = corpusQueryOptions().queryFn!({} as never)
    await vi.waitFor(() => expect(vi.mocked(apiGet)).toHaveBeenCalledTimes(3))
    releaseSecond?.()
    const result = await pending

    expect(result.items).toHaveLength(1200)
    expect(result.items[500]!.message_id).toBe('b0')
    expect(result.items[1000]!.message_id).toBe('c0')
  })
})
