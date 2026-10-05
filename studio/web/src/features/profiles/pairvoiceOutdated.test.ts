import { describe, expect, it } from 'vitest'

import type { PairvoiceHealth } from '../../../../shared/api-types'
import { pairvoiceOutdated } from './pairvoiceOutdated'

function health(over: Partial<PairvoiceHealth['tts']> = {}): PairvoiceHealth {
  return {
    ok: true,
    llm: { model: 'llm', state: 'loaded', detail: '', last_used: null },
    tts: { model: 'tts', state: 'loaded', detail: '', last_used: null, ...over },
    mute: { active: false, reason: null, until: null },
    queue: { running: 0, waiting: 0 },
    dropped_recent: 0,
    config_stale: false,
  }
}

describe('pairvoiceOutdated', () => {
  it('sampler があれば古くない', () => {
    expect(pairvoiceOutdated(health({ sampler: {} }))).toBe(false)
  })

  it('sampler キーが無ければ（旧サーバー）古いと判定する', () => {
    expect(pairvoiceOutdated(health())).toBe(true)
  })

  it('pairvoice 自体が停止していれば古い扱いにしない（別の表示に任せる）', () => {
    expect(pairvoiceOutdated(null)).toBe(false)
  })
})
