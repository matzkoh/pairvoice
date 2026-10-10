import { useQueryClient, useSuspenseQuery } from '@tanstack/react-query'
import { useTransition } from 'react'

import { ConfirmButton } from '@/components/ConfirmButton'
import { Button } from '@/components/ui/button'
import { apiSend, toErrorMessage } from '@/lib/api'

import { type HistorySource, historyQueryOptions } from './queries'

type Props = {
  source: HistorySource
  // 未保存の変更がある時だけ二段確認にする（呼び出し側の dirty をそのまま渡す）
  confirmBeforeRestore?: boolean
  // 本体と履歴のほかに取り直すもの
  onRestored?: () => Promise<unknown>
  onError?: (message: string) => void
}

export function HistoryList({ source, confirmBeforeRestore = false, onRestored, onError }: Props) {
  const { data } = useSuspenseQuery(historyQueryOptions(source))
  const queryClient = useQueryClient()
  const [pending, startTransition] = useTransition()

  function restore(name: string) {
    // 前回の失敗を残すと、成功しても失敗の表示が消えない
    onError?.('')
    startTransition(async () => {
      try {
        await apiSend(`${source.path}/restore`, 'POST', { name })
        // 復元は本体と履歴の両方を変える（復元も履歴に1件増える）。履歴は本体のキーの下
        await Promise.all([queryClient.invalidateQueries({ queryKey: source.key }), onRestored?.()])
      } catch (err: unknown) {
        // 投げ直すと root の errorComponent まで飛んで画面ごと消える
        onError?.(toErrorMessage(err))
      }
    })
  }

  if (data.items.length === 0) {
    return <p className="text-sm text-muted-foreground">履歴はまだありません。</p>
  }

  return (
    <ul className="divide-y rounded-lg border bg-card">
      {data.items.map((item) => (
        <li key={item.name} className="flex items-center gap-2 px-3 py-1.5">
          <span className="flex-1 font-mono text-xs text-muted-foreground tabular-nums">
            {item.ts}
          </span>
          {confirmBeforeRestore ? (
            <ConfirmButton
              variant="ghost"
              idle="復元"
              armed="未保存の変更を破棄して復元"
              disabled={pending}
              onConfirm={() => restore(item.name)}
            />
          ) : (
            <Button variant="ghost" size="sm" disabled={pending} onClick={() => restore(item.name)}>
              復元
            </Button>
          )}
        </li>
      ))}
    </ul>
  )
}
