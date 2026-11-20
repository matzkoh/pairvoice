import { expect, it } from 'vitest'

import { VOICE_BARS, voiceBars, voiceHue } from './voiceColor'

const IDS = Array.from({ length: 500 }, (_, i) => `voice-${i}`)

it('同じ ID からは、いつも同じ色と形が出る', () => {
  expect(voiceHue('abc')).toBe(voiceHue('abc'))
  expect(voiceBars('abc')).toEqual(voiceBars('abc'))
})

it('声の色は、蛍光ペンと紛れる黄の帯を避ける', () => {
  for (const id of IDS) {
    const hue = voiceHue(id)
    expect(hue).toBeGreaterThanOrEqual(0)
    expect(hue).toBeLessThan(360)
    expect(hue < 75 || hue >= 125).toBe(true)
  }
})

it('声の印の棒は、決まった本数で枠に収まる', () => {
  for (const id of IDS) {
    const bars = voiceBars(id)
    expect(bars).toHaveLength(VOICE_BARS)
    for (const bar of bars) {
      expect(bar).toBeGreaterThanOrEqual(0.3)
      expect(bar).toBeLessThanOrEqual(1)
    }
  }
})
