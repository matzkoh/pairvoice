import type { DictRowData } from '@/lib/api-types'

export function findDictRow(rows: readonly DictRowData[], from: string): DictRowData | undefined {
  //@ verify
  //@ ensures \result === undefined <==> forall(i: nat, i < rows.length ==> rows[i].from !== from.trim())
  //@ ensures \result !== undefined ==> \result.from === from.trim() && rows.includes(\result)
  const key = from.trim()
  return rows.find((r) => r.from === key)
}

// 同じ表記が2行あると、どちらが効くかが置換の順序に依存して読めなくなるので、足さずに置き換える
export function upsertDictRow(rows: readonly DictRowData[], entry: DictRowData): DictRowData[] {
  //@ verify
  //@ ensures forall(i: nat, i < rows.length ==> rows[i].from !== entry.from.trim()) ==> \result.length === rows.length + 1 && forall(i: nat, i < rows.length ==> \result[i] === rows[i]) && \result[rows.length] === { from: entry.from.trim(), to: entry.to, memo: entry.memo }
  //@ ensures exists(k: nat, k < rows.length && rows[k].from === entry.from.trim()) ==> \result.length === rows.length && exists(k: nat, k < rows.length && rows[k].from === entry.from.trim() && forall(i: nat, i < k ==> rows[i].from !== entry.from.trim()) && \result[k] === { from: entry.from.trim(), to: entry.to, memo: entry.memo } && forall(i: nat, i < rows.length && i !== k ==> \result[i] === rows[i]))
  const next = { from: entry.from.trim(), to: entry.to, memo: entry.memo }
  const index = rows.findIndex((r) => r.from === next.from)
  return index === -1 ? [...rows, next] : rows.map((r, i) => (i === index ? next : r))
}
