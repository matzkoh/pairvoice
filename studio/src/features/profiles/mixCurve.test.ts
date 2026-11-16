import { describe, expect, it } from 'vitest'

import { mixWeight } from './mixCurve'

describe('mixWeight', () => {
  it('keeps both ends and the middle', () => {
    expect([mixWeight(0), mixWeight(0.5), mixWeight(1)]).toEqual([0, 0.5, 1])
  })

  it('gives most of the slider to the middle weights where the voice changes', () => {
    expect(mixWeight(0.1)).toBeCloseTo(0.25, 2)
    expect(mixWeight(0.9)).toBeCloseTo(0.75, 2)
  })

  it('clamps positions outside the slider', () => {
    expect([mixWeight(-0.1), mixWeight(1.1)]).toEqual([0, 1])
  })
})
