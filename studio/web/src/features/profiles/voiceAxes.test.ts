import { expect, it } from 'vitest'

import { AXES, captionFor, randomPoint } from './voiceAxes'

it('段階の組から caption を組み立てる', () => {
  expect(captionFor({ gender: 1, age: 1, pitch: 3, texture: 2, mood: 1, speed: 1 })).toBe(
    '二十代の女性の声。声は高め、澄んだ声。落ち着いた調子で、ややゆっくり話す。',
  )
})

it('答えていない軸は caption に書かない', () => {
  expect(captionFor({ gender: 1 })).toBe('女性の声。')
  expect(captionFor({ age: 0, speed: 4 })).toBe('十代の人の声。速く話す。')
})

it('でたらめな点は答えた軸をそろえ、答えていない軸を散らす', () => {
  let s = 7
  const random = () => (s = (s * 1103515245 + 12345) % 2 ** 31) / 2 ** 31
  const points = Array.from({ length: 5 }, () => randomPoint({ gender: 1 }, random))
  for (const point of points) {
    expect(point.gender).toBe(1)
    for (const axis of AXES) expect(point[axis.key]).toBeLessThan(axis.levels.length)
  }
  expect(new Set(points.map((p) => p.age)).size).toBeGreaterThan(1)
})
