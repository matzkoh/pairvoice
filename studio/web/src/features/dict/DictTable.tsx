import { keepPreviousData, useQuery, useQueryClient, useSuspenseQuery } from '@tanstack/react-query'
import { useDeferredValue, useEffect, useRef, useState, useTransition } from 'react'

import { PageHeader } from '@/components/PageHeader'
import { StatusText } from '@/components/StatusText'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { Textarea } from '@/components/ui/textarea'
import { apiSend, toErrorMessage } from '@/lib/api'
import { isTypingTarget } from '@/lib/hotkeys'
import { createIdGenerator } from '@/lib/ids'
import { useTransientStatus } from '@/lib/useTransientStatus'

import type { DictRowData, DictTestResponse } from '../../../../shared/api-types'
import { DictRow, type EditableDictRow, type Field } from './DictRow'
import { dictQueryOptions } from './queries'

const DEFAULT_PREVIEW_TEXT = 'admin-console の修正が PR #4688 で通ったよ。✕ボタンも直した'

type SaveStatus = { message: string; isError: boolean } | null

// ロード時は末尾に常に空行を1つ足す。ここに入力すると
// 新しい空行が増えていく。id はサーバーの応答に無いのでここで採番する
// （from の内容を key にすると1文字打つたびに input が作り直される）。
function toEditableRows(rows: readonly DictRowData[]): EditableDictRow[] {
  return [...rows.map((r, i) => ({ id: i, ...r })), { id: rows.length, from: '', to: '', memo: '' }]
}

// 保存・プレビューの両方で「from が空の行」を落とす。
// 末尾の空行と、from だけ消して残った空エントリがここで落ちる。id はサーバーに
// 意味の無い項目なので外す。
function toWireRows(rows: readonly EditableDictRow[]): DictRowData[] {
  return rows.filter((r) => r.from.trim() !== '').map(({ from, to, memo }) => ({ from, to, memo }))
}

export function DictTable() {
  const { data } = useSuspenseQuery(dictQueryOptions())
  const queryClient = useQueryClient()

  // 明示的な保存ボタンを押すまでサーバーに送らない。編集中の値は「まだ保存して
  // いない下書き」であって「サーバーに送った楽観的な結果」ではないので、
  // useOptimistic では表せない。
  const [rows, setRows] = useState<EditableDictRow[]>(() => toEditableRows(data.rows))
  // 末尾の空行に文字が入ると新しい空行を追加する。その新しい行の id は
  // ここから払い出す（初期表示分の 0..data.rows.length は toEditableRows が使う）。
  const [nextRowId] = useState(() => createIdGenerator(data.rows.length))
  // 削除の履歴をスタックで持つ。undo は pop して元の位置に挿し戻す。
  // 1つしか戻せない実装だと連続削除の誤操作の取り返しがつかない。
  const undoStackRef = useRef<{ index: number; row: EditableDictRow }[]>([])

  const [pending, startTransition] = useTransition()
  const [rawStatus, setStatus] = useState<SaveStatus>(null)
  // 保存成功のメッセージだけ自動で消える。
  const status = useTransientStatus(rawStatus)

  // Cmd/Ctrl+Z で直前の削除を復元する。入力欄にフォーカスがある間はブラウザの
  // ネイティブ undo（セル文字の取り消し）に任せ、アプリ側では横取りしない。
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const isUndo = (e.metaKey || e.ctrlKey) && e.key === 'z'
      if (!isUndo) return
      if (isTypingTarget(document.activeElement)) return
      const last = undoStackRef.current.pop()
      if (!last) return
      e.preventDefault()
      setRows((prev) => [...prev.slice(0, last.index), last.row, ...prev.slice(last.index)])
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  function changeCell(id: number, field: Field, value: string) {
    // updater の中で払い出すと、二重に呼ばれたときに副作用が漏れる。使わなくても番号が飛ぶだけ
    const newRowId = nextRowId()
    setRows((prev) => {
      const idx = prev.findIndex((r) => r.id === id)
      if (idx === -1) return prev
      const updated = { ...prev[idx]!, [field]: value }
      const next = [...prev.slice(0, idx), updated, ...prev.slice(idx + 1)]
      const wasLast = idx === prev.length - 1
      const hasContent = updated.from !== '' || updated.to !== '' || updated.memo !== ''
      if (wasLast && hasContent) {
        next.push({ id: newRowId, from: '', to: '', memo: '' })
      }
      return next
    })
  }

  function deleteRow(id: number) {
    setRows((prev) => {
      const idx = prev.findIndex((r) => r.id === id)
      if (idx === -1) return prev
      undoStackRef.current.push({ index: idx, row: prev[idx]! })
      return [...prev.slice(0, idx), ...prev.slice(idx + 1)]
    })
  }

  function save() {
    startTransition(async () => {
      try {
        await apiSend('/dict', 'PUT', { rows: toWireRows(rows) })
        // 保存後に再読込はしない（rows はローカルの編集内容のまま）。他所からの再訪問に
        // 備えてキャッシュだけ古さの印を付けておく。
        await queryClient.invalidateQueries({ queryKey: ['dict'] })
        setStatus({ message: '保存しました', isError: false })
      } catch (err: unknown) {
        // ここで再 throw すると transition 内の例外は最も近い error boundary
        // （root の errorComponent）まで伝播し、画面全体が消える。ステータス行に赤字を
        // 出すだけにして、編集内容はそのまま画面に残す。
        const message = toErrorMessage(err)
        setStatus({ message: `保存に失敗しました: ${message}`, isError: true })
      }
    })
  }

  const [previewText, setPreviewText] = useState(DEFAULT_PREVIEW_TEXT)
  // 打鍵のたびに問い合わせを積まないよう、入力に遅れて追いかける
  const deferredText = useDeferredValue(previewText)
  const deferredRows = useDeferredValue(rows)
  // メモは置き換えに効かないので外す（メモを打っても取り直さない）
  const previewInput = {
    text: deferredText,
    rows: toWireRows(deferredRows).map(({ from, to }) => ({ from, to })),
  }
  // 例外は useQuery が error に収めるので、画面ごと消えずにインラインで出せる
  const preview = useQuery({
    queryKey: ['dict-test', previewInput],
    queryFn: () => apiSend<DictTestResponse>('/dict/test', 'POST', previewInput),
    placeholderData: keepPreviousData,
  })

  return (
    <div>
      <PageHeader
        title="辞書"
        description="読み上げる前に、表記を読みに置き換えます。"
        actions={
          <>
            {status && <StatusText as="span" message={status.message} isError={status.isError} />}
            <Button size="sm" disabled={pending} onClick={save}>
              {pending ? '保存中…' : '保存'}
            </Button>
          </>
        }
      />
      <section className="mb-5 rounded-lg border bg-card p-4">
        <label htmlFor="dict-preview" className="mb-1.5 block text-xs text-muted-foreground">
          試し置換（保存前の表で置き換えます）
        </label>
        <Textarea
          id="dict-preview"
          rows={2}
          value={previewText}
          onChange={(e) => setPreviewText(e.target.value)}
        />
        {/* 失敗したときに前回の結果を残すと、今の入力での結果に見えてしまう */}
        {preview.isError ? (
          <p className="mt-2 text-xs text-destructive">
            プレビューに失敗しました: {toErrorMessage(preview.error)}
          </p>
        ) : (
          preview.data && (
            <p className="mt-2 rounded-md bg-muted px-3 py-2 text-sm" aria-label="置換後">
              {preview.data.result}
            </p>
          )
        )}
      </section>
      <div className="overflow-hidden rounded-lg border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
            <tr>
              <th className="w-10">
                <span className="sr-only">行</span>
              </th>
              <th className="w-1/4 px-2 py-2 font-medium">表記</th>
              <th className="w-1/4 px-2 py-2 font-medium">読み</th>
              <th className="px-2 py-2 font-medium">メモ</th>
              <th className="w-10">
                <span className="sr-only">削除</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <DictRow
                key={row.id}
                row={row}
                rownum={i + 1}
                isLast={i === rows.length - 1}
                onChange={(field, value) => changeCell(row.id, field, value)}
                onDelete={() => deleteRow(row.id)}
              />
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        最後の行に書くと次の空行が増えます。行の削除は <Kbd>⌘Z</Kbd> で戻せます。
      </p>
    </div>
  )
}
