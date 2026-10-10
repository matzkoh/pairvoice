import { useQueryClient } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { useRef, useState, useTransition } from 'react'

import { ConfirmButton } from '@/components/ConfirmButton'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { apiSend, apiUrl, toErrorMessage } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'
import { isImeConfirm } from '@/lib/hotkeys'

import { SOURCE_LABELS } from './ProfileList'
import { invalidateProfiles, type ProfileStatus } from './queries'

type Props = {
  item: ProfileItem
  isActive: boolean
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onDeleted: () => void
}

// 選んだプロファイルの中身（参照音声 + caption）と、名前・使用中・削除の操作
export function ProfileCard({ item, isActive, onPlay, onStatus, onDeleted }: Props) {
  const queryClient = useQueryClient()
  const [pending, startTransition] = useTransition()
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(item.name)
  // 1回の編集で確定か取り消しのどちらか1度だけ。入力欄が消えるときにブラウザが blur を
  // 送ってくるので、Enter の後に二重に保存し、Escape の後にも保存してしまう
  const editing = useRef(false)
  const path = `/profiles/${encodeURIComponent(item.id)}`

  function run(work: () => Promise<unknown>, done: string, after?: () => void) {
    startTransition(async () => {
      try {
        await work()
        await invalidateProfiles(queryClient)
        onStatus({ message: done, isError: false })
        after?.()
      } catch (err: unknown) {
        onStatus({ message: toErrorMessage(err), isError: true })
      }
    })
  }

  function startRename() {
    editing.current = true
    setRenaming(true)
  }

  function rename() {
    if (!editing.current) return
    editing.current = false
    const next = name.trim()
    setRenaming(false)
    if (next === '' || next === item.name) {
      setName(item.name)
      return
    }
    run(() => apiSend(path, 'PATCH', { name: next }), '名前を変えました')
  }

  return (
    <section aria-label="プロファイルの中身" className="rounded-lg border bg-card">
      <div className="flex min-h-12 items-center gap-2 border-b px-4 py-2">
        {renaming ? (
          <Input
            aria-label="プロファイルの名前"
            className="h-8 max-w-72"
            value={name}
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onBlur={rename}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !isImeConfirm(e.nativeEvent)) rename()
              if (e.key === 'Escape') {
                editing.current = false
                setName(item.name)
                setRenaming(false)
              }
            }}
          />
        ) : (
          <button
            type="button"
            className="text-base font-semibold hover:underline"
            title="クリックで名前を変える"
            onClick={startRename}
          >
            {item.name}
          </button>
        )}
        <Badge variant="outline">{SOURCE_LABELS[item.source]}</Badge>
        {isActive && <Badge>使用中</Badge>}
        {/* 使用中を消すと、次の読み上げで既定の声が黙って作り直されるので削除も出さない */}
        {!isActive && (
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={pending}
              onClick={() =>
                run(
                  () => apiSend('/profiles/active', 'PUT', { id: item.id }),
                  `「${item.name}」を使います（次の読み上げから効きます）`,
                )
              }
            >
              使う
            </Button>
            <ConfirmButton
              idle="削除"
              armed="本当に削除"
              disabled={pending}
              onConfirm={() =>
                run(() => apiSend(path, 'DELETE'), `「${item.name}」を削除しました`, onDeleted)
              }
            />
          </div>
        )}
      </div>
      <dl className="grid grid-cols-[5rem_minmax(0,1fr)] items-baseline gap-x-4 gap-y-2 px-4 py-3">
        <dt className="text-xs text-muted-foreground">参照音声</dt>
        <dd>
          <Button
            variant="outline"
            size="xs"
            aria-label={`${item.name} の参照音声を再生`}
            onClick={() => onPlay(apiUrl(`${path}/audio`))}
          >
            <Play />
            再生
          </Button>
        </dd>
        <dt className="text-xs text-muted-foreground">caption</dt>
        <dd className="text-sm whitespace-pre-wrap">
          {item.caption || <span className="text-muted-foreground">（なし）</span>}
        </dd>
      </dl>
    </section>
  )
}
