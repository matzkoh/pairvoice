import { X } from 'lucide-react'

import { Button } from '@/components/ui/button'

import type { DictRowData } from '../../../../shared/api-types'

// サーバーの応答には行の id が無い。編集中に行が動いても入力欄の同一性を保つため、
// クライアントで採番した id を持たせる。key に内容を使うと1文字打つたびに input が作り直される。
export type EditableDictRow = DictRowData & { id: number }

export type Field = 'from' | 'to' | 'memo'

const CELL_INPUT =
  'h-8 w-full border-0 bg-transparent px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset'

type Props = {
  row: EditableDictRow
  rownum: number
  // 末尾の空行だけ削除ボタンを持たない
  isLast: boolean
  onChange: (field: Field, value: string) => void
  onDelete: () => void
}

export function DictRow({ row, rownum, isLast, onChange, onDelete }: Props) {
  return (
    <tr className="border-t">
      <td className="w-10 px-2 text-center text-xs text-muted-foreground tabular-nums">{rownum}</td>
      <td className="p-0">
        <input
          aria-label={`${rownum}行目の表記`}
          value={row.from}
          onChange={(e) => onChange('from', e.target.value)}
          className={`${CELL_INPUT} font-mono text-xs`}
        />
      </td>
      <td className="p-0">
        <input
          aria-label={`${rownum}行目の読み`}
          value={row.to}
          onChange={(e) => onChange('to', e.target.value)}
          className={`${CELL_INPUT} font-mono text-xs`}
        />
      </td>
      <td className="p-0">
        <input
          aria-label={`${rownum}行目のメモ`}
          value={row.memo}
          onChange={(e) => onChange('memo', e.target.value)}
          className={`${CELL_INPUT} text-xs text-muted-foreground`}
        />
      </td>
      <td className="w-10 text-center">
        {!isLast && (
          <Button
            variant="ghost"
            size="icon"
            className="size-7 text-muted-foreground"
            aria-label="行を削除"
            onClick={onDelete}
          >
            <X className="size-3.5" aria-hidden="true" />
          </Button>
        )}
      </td>
    </tr>
  )
}
