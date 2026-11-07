import type { ReactNode } from 'react'

import { StatusText } from '@/components/StatusText'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { useHotkeys } from '@/lib/hotkeys'

type Props = {
  dirty: boolean
  pending: boolean
  // 保存の結果。出ている間は未保存・保存済みの表示の代わりに出す
  status: { message: string; isError: boolean } | null
  onSave: () => void
  label?: string
  // 保存の左に並べる操作（変更を破棄など）
  children?: ReactNode
}

// 画面の下に付いてくる保存の帯。見出しに置くと、長い表や本文を書いている所から
// 保存が画面の外へ出て押しづらい。浮かせると表の右端の操作に重なるので、本文と同じ幅の帯にする
export function SaveBar({ dirty, pending, status, onSave, label = '保存', children }: Props) {
  // 入力欄で打っている最中にも保存できるよう修飾キー付きにする（ブラウザのページ保存は横取りする）
  useHotkeys({
    'mod+s': () => {
      if (!pending) onSave()
    },
  })

  return (
    <div className="sticky bottom-0 z-10 mt-2 flex items-center justify-end gap-3 border-t bg-background py-3">
      {status ? (
        <StatusText as="span" message={status.message} isError={status.isError} />
      ) : dirty ? (
        <span className="flex items-center gap-1.5 text-xs">
          <span className="size-1.5 rounded-full bg-primary" aria-hidden="true" />
          未保存の変更あり
        </span>
      ) : (
        <span className="text-xs text-muted-foreground">保存済み</span>
      )}
      {children}
      <Button size="sm" disabled={pending} onClick={onSave} aria-keyshortcuts="Meta+S Control+S">
        {pending ? '保存中…' : label}
        <Kbd aria-hidden="true">⌘S</Kbd>
      </Button>
    </div>
  )
}
