import { expect, it } from 'vitest'

import { createIdGenerator } from './ids'

it('生成器ごとに 1 から数え、start を渡すとその次から始まる', () => {
  const a = createIdGenerator()
  const b = createIdGenerator(5)
  expect([a(), a(), b(), a()]).toEqual([1, 2, 6, 3])
})
