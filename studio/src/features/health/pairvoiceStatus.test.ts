import { describe, expect, it } from 'vitest'

import type { ModelState, PairvoiceHealth } from '@/lib/api-types'

import { pairvoiceStatus } from './pairvoiceStatus'

function health(over: Partial<PairvoiceHealth> = {}): PairvoiceHealth {
  return {
    ok: true,
    llm: { model: 'llm', state: 'loaded', detail: '', last_used: null },
    tts: { model: 'tts', state: 'loaded', detail: '', last_used: null },
    mute: { active: false, reason: null, until: null },
    queue: { running: 0, waiting: 0 },
    dropped_recent: 0,
    config_stale: false,
    ...over,
  }
}

function withStates(llm: ModelState, tts: ModelState): PairvoiceHealth {
  return health({
    llm: { model: 'llm', state: llm, detail: '', last_used: null },
    tts: { model: 'tts', state: tts, detail: '', last_used: null },
  })
}

describe('pairvoiceStatus の8分岐', () => {
  it('pairvoice が null なら停止', () => {
    expect(pairvoiceStatus(null)).toEqual({ tone: 'bad', label: 'pairvoice 停止' })
  })

  it('ミュート中は理由つきで出す', () => {
    const status = pairvoiceStatus(
      health({ mute: { active: true, reason: '会議中', until: null } }),
    )
    expect(status).toEqual({ tone: 'warn', label: 'ミュート中（会議中）' })
  })

  it('downloading はモデル取得中', () => {
    expect(pairvoiceStatus(withStates('downloading', 'loaded'))).toEqual({
      tone: 'warn',
      label: 'モデル取得中',
    })
  })

  it('loading はロード中', () => {
    expect(pairvoiceStatus(withStates('loaded', 'loading'))).toEqual({
      tone: 'warn',
      label: 'ロード中',
    })
  })

  it('failed はロード失敗', () => {
    expect(pairvoiceStatus(withStates('failed', 'loaded'))).toEqual({
      tone: 'bad',
      label: 'ロード失敗',
    })
  })

  it('misconfigured は設定不備', () => {
    expect(pairvoiceStatus(withStates('loaded', 'misconfigured'))).toEqual({
      tone: 'bad',
      label: '設定不備',
    })
  })

  it('failed と misconfigured が揃ったら設定不備を優先する', () => {
    expect(pairvoiceStatus(withStates('failed', 'misconfigured')).label).toBe('設定不備')
  })

  it('dropped_recent が3以上ならキュー詰まり', () => {
    expect(pairvoiceStatus(health({ dropped_recent: 3 }))).toEqual({
      tone: 'warn',
      label: 'キュー詰まり（3件破棄）',
    })
  })

  it('dropped_recent が2なら詰まり扱いにしない', () => {
    expect(pairvoiceStatus(health({ dropped_recent: 2 })).label).toBe('pairvoice')
  })

  it('config_stale は再起動を促す', () => {
    expect(pairvoiceStatus(health({ config_stale: true }))).toEqual({
      tone: 'warn',
      label: '設定が未反映（再起動が必要）',
    })
  })

  it('何も無ければ ok', () => {
    expect(pairvoiceStatus(health())).toEqual({ tone: 'ok', label: 'pairvoice' })
  })
})

// 順序が仕様なので、境界どうしを衝突させて「早いものが勝つ」ことを固定する。
// 個々の分岐が通るだけのテストでは、順序を入れ替えても気づけない。
describe('pairvoiceStatus の優先順位', () => {
  it('ミュートはモデルの状態より先', () => {
    const status = pairvoiceStatus(
      health({
        mute: { active: true, reason: '手動', until: null },
        llm: { model: 'llm', state: 'downloading', detail: '', last_used: null },
      }),
    )
    expect(status.label).toBe('ミュート中（手動）')
  })

  it('downloading は loading より先', () => {
    expect(pairvoiceStatus(withStates('loading', 'downloading')).label).toBe('モデル取得中')
  })

  it('loading は failed より先', () => {
    expect(pairvoiceStatus(withStates('loading', 'failed')).label).toBe('ロード中')
  })

  it('failed はキュー詰まりより先', () => {
    const h = withStates('failed', 'loaded')
    h.dropped_recent = 10
    expect(pairvoiceStatus(h).label).toBe('ロード失敗')
  })

  it('キュー詰まりは config_stale より先', () => {
    expect(pairvoiceStatus(health({ dropped_recent: 5, config_stale: true })).label).toBe(
      'キュー詰まり（5件破棄）',
    )
  })
})

it('読み込み済みでも前提が崩れていれば（detail がある）設定不備として理由を出す', () => {
  const status = pairvoiceStatus(
    health({ tts: { model: 'tts', state: 'loaded', detail: 'profile_missing', last_used: null } }),
  )
  expect(status).toEqual({ tone: 'bad', label: '設定不備（profile_missing）' })
})
