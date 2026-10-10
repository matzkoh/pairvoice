import { describe, expect, it } from 'vitest'

import { formatWhen } from './formatWhen'

describe('formatWhen', () => {
  it('corpus.jsonl の "YYYY-MM-DD HH:mm:ss" 形式を M/D HH:mm に整形する', () => {
    // ローカル時刻として解釈される（オフセット無しの日時は ECMAScript ではローカル
    // 時刻）。年をまたがない範囲で月日と時分だけを検証する。
    expect(formatWhen('2026-03-05 09:07:00')).toBe('3/5 09:07')
  })

  it('parse できない文字列はそのまま返す', () => {
    expect(formatWhen('not-a-date')).toBe('not-a-date')
  })
})
