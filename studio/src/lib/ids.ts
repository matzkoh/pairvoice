// 1 から順に払い出す連番。start を渡すとその次の値から始まる
export function createIdGenerator(start = 0): () => number {
  let n = start
  return () => ++n
}
