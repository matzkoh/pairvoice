export function nextSelection(
  ids: readonly string[],
  current: string | null,
  delta: 1 | -1,
): string | null {
  //@ verify
  //@ requires delta === 1 || delta === -1
  //@ ensures ids.length === 0 <==> \result === null
  //@ ensures \result !== null ==> ids.includes(\result)
  //@ ensures ids.length > 0 && current === null ==> \result === (delta === 1 ? ids[0] : ids[ids.length - 1])
  //@ ensures ids.length > 0 && current !== null && !ids.includes(current) ==> \result === (delta === 1 ? ids[0] : ids[ids.length - 1])
  //@ ensures current !== null && ids.includes(current) && 0 <= ids.indexOf(current) + delta && ids.indexOf(current) + delta < ids.length ==> \result === ids[ids.indexOf(current) + delta]
  //@ ensures current !== null && ids.includes(current) && (ids.indexOf(current) + delta < 0 || ids.indexOf(current) + delta >= ids.length) ==> \result === current
  if (ids.length === 0) return null
  const index = current === null ? -1 : ids.indexOf(current)
  if (index === -1) return delta === 1 ? ids[0]! : ids[ids.length - 1]!
  return ids[Math.min(ids.length - 1, Math.max(0, index + delta))]!
}

// 投票やアーカイブで選択中の行が今の絞り込みから外れるとき、次に選ぶ行。
// 「未レビュー」で 1 を押し続けて順に片付けられるよう、同じ位置の行を選ぶ
export function selectionAfterRemoval(ids: readonly string[], removedId: string): string | null {
  //@ verify
  //@ ensures \result === null <==> forall(i: nat, i < ids.length ==> ids[i] === removedId)
  //@ ensures \result !== null ==> ids.includes(\result) && \result !== removedId
  //@ ensures ids.length >= 2 && forall(i: nat, forall(j: nat, i < ids.length && j < ids.length && i !== j ==> ids[i] !== ids[j])) && ids.includes(removedId) && ids.indexOf(removedId) < ids.length - 1 ==> \result === ids[ids.indexOf(removedId) + 1]
  //@ ensures ids.length >= 2 && forall(i: nat, forall(j: nat, i < ids.length && j < ids.length && i !== j ==> ids[i] !== ids[j])) && ids[ids.length - 1] === removedId ==> \result === ids[ids.length - 2]
  const index = ids.indexOf(removedId)
  const rest = ids.filter((id) => id !== removedId)
  if (rest.length === 0) return null
  if (index === -1) return rest[0]!
  return rest[Math.min(index, rest.length - 1)]!
}

export function selectedTextWithin(
  root: HTMLElement | null,
  selection: Selection | null = window.getSelection(),
): string {
  if (!root || !selection || selection.rangeCount === 0 || selection.isCollapsed) return ''
  const range = selection.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) return ''
  return selection.toString().trim()
}
