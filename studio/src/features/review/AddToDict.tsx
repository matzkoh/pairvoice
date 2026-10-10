import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { BookPlus } from 'lucide-react'
import { useActionState, useDeferredValue, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { dictQueryOptions } from '@/features/dict/queries'
import { findDictRow, upsertDictRow } from '@/features/dict/upsertDictRow'
import { apiSend, toErrorMessage } from '@/lib/api'
import type { DictTestResponse } from '@/lib/api-types'

import { selectedTextWithin } from './selection'
import { useAddDictEntry } from './useAddDictEntry'

// summaryId は要約を表示している要素の id。開くときにその中の選択文字列を表記の初期値にする
type Props = { summary: string; summaryId: string }

// 読み上げを聞いて読み間違いに気づいたその場で、辞書に1行足す
export function AddToDict({ summary, summaryId }: Props) {
  const [open, setOpen] = useState(false)
  const [initialFrom, setInitialFrom] = useState('')
  const [done, setDone] = useState('')

  return (
    <>
      <Popover
        open={open}
        onOpenChange={(next) => {
          if (next) {
            setInitialFrom(selectedTextWithin(document.getElementById(summaryId)))
            setDone('')
          }
          setOpen(next)
        }}
      >
        <PopoverTrigger
          render={
            <Button
              variant="outline"
              size="sm"
              // 押した瞬間に要約の文字選択が外れないようにする（選択は開くときに読む）
              onMouseDown={(e) => e.preventDefault()}
            />
          }
        >
          <BookPlus className="size-3.5" aria-hidden="true" />
          辞書に追加
        </PopoverTrigger>
        <PopoverContent align="start" className="w-80">
          {open && (
            <AddToDictForm
              summary={summary}
              initialFrom={initialFrom}
              onDone={(message) => {
                setOpen(false)
                setDone(message)
              }}
            />
          )}
        </PopoverContent>
      </Popover>
      {done && <span className="text-xs text-muted-foreground">{done}</span>}
    </>
  )
}

type FormProps = { summary: string; initialFrom: string; onDone: (message: string) => void }

function AddToDictForm({ summary, initialFrom, onDone }: FormProps) {
  const { data, dataUpdatedAt } = useQuery(dictQueryOptions())
  const addEntry = useAddDictEntry()
  const [from, setFrom] = useState(initialFrom)
  const [to, setTo] = useState('')
  // 触るまでは null。置き換える行があればそのメモを引き継ぎ、黙って消さない
  const [memo, setMemo] = useState<string | null>(null)
  const existing = data ? findDictRow(data.rows, from.trim()) : undefined
  const entry = { from: from.trim(), to, memo: memo ?? existing?.memo ?? '' }

  // 打鍵のたびに問い合わせを積まないよう、入力に遅れて追いかける
  const previewFrom = useDeferredValue(entry.from)
  const previewTo = useDeferredValue(entry.to)

  // 保存前の入力を今の辞書に当てはめた結果を見せる。辞書の取り直しは dataUpdatedAt で拾う
  const preview = useQuery({
    queryKey: ['dict-test', summary, previewFrom, previewTo, dataUpdatedAt],
    queryFn: () =>
      apiSend<DictTestResponse>('/dict/test', 'POST', {
        text: summary,
        rows: upsertDictRow(data?.rows ?? [], { ...entry, from: previewFrom, to: previewTo }),
      }),
    enabled: previewFrom !== '' && data !== undefined,
    placeholderData: keepPreviousData,
  })

  const [error, submit, pending] = useActionState<string, FormData>(async () => {
    try {
      await addEntry(entry)
      onDone(`「${entry.from}」→「${entry.to}」を辞書に追加しました`)
      return ''
    } catch (err: unknown) {
      return `辞書に追加できませんでした: ${toErrorMessage(err)}`
    }
  }, '')

  return (
    <form action={submit} className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="add-dict-from" className="mb-1 block text-xs text-muted-foreground">
            表記
          </label>
          <Input id="add-dict-from" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div>
          <label htmlFor="add-dict-to" className="mb-1 block text-xs text-muted-foreground">
            読み
          </label>
          <Input
            id="add-dict-to"
            value={to}
            autoFocus={initialFrom !== ''}
            onChange={(e) => setTo(e.target.value)}
          />
        </div>
      </div>
      <div>
        <label htmlFor="add-dict-memo" className="mb-1 block text-xs text-muted-foreground">
          メモ
        </label>
        <Input id="add-dict-memo" value={entry.memo} onChange={(e) => setMemo(e.target.value)} />
      </div>
      {existing && (
        <p className="text-xs text-warning">
          「{existing.from}」は「{existing.to}」で登録済みです。保存すると置き換えます。
        </p>
      )}
      {/* 失敗したときに前回の結果を残すと、今の入力での結果に見えてしまう */}
      {preview.isError ? (
        <p className="text-xs text-destructive">
          プレビューできませんでした: {toErrorMessage(preview.error)}
        </p>
      ) : (
        preview.data && (
          <p className="rounded-md bg-muted px-3 py-2 text-xs" aria-label="置換後">
            {preview.data.result}
          </p>
        )
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={pending || entry.from === '' || to.trim() === ''}>
          {existing ? '置き換えて保存' : '保存'}
        </Button>
      </div>
    </form>
  )
}
