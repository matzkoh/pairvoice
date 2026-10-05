import type { DictRowData } from '../../../../shared/api-types'

export function findDictRow(rows: readonly DictRowData[], from: string): DictRowData | undefined {
  const key = from.trim()
  return rows.find((r) => r.from === key)
}

// 同じ表記が2行あると、どちらが効くかが置換の順序に依存して読めなくなるので、足さずに置き換える
export function upsertDictRow(rows: readonly DictRowData[], entry: DictRowData): DictRowData[] {
  const next = { from: entry.from.trim(), to: entry.to, memo: entry.memo }
  const index = rows.findIndex((r) => r.from === next.from)
  return index === -1 ? [...rows, next] : rows.map((r, i) => (i === index ? next : r))
}
