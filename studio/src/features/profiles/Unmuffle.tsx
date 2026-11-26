import { useQueryClient } from '@tanstack/react-query'
import { LoaderCircle } from 'lucide-react'
import { useState, useTransition } from 'react'

import { PlayButton } from '@/components/PlayButton'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { apiSend, toErrorMessage } from '@/lib/api'
import type { ProfileItem, UnmuffleResponse } from '@/lib/api-types'
import { audioFileUrl } from '@/lib/speak'

import { createdStatus, invalidateProfiles, type ProfileStatus } from './queries'

type Props = {
  item: ProfileItem
  // いまの合成モデルの、こもりの向きを同梱しているか
  available: boolean
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// こもった声（12kHz より上が欠けた声）の知らせと、直した声を別の声として保存する操作。
// 元の声は書き換えない（直した声が気に入らなければ、元の声のまま使い続けられる）
export function Unmuffle({ item, available, onPlay, onStatus, onCreated }: Props) {
  const queryClient = useQueryClient()
  const [found, setFound] = useState<UnmuffleResponse | null>(null)
  const [name, setName] = useState(`${item.name}（クリア）`)
  const [busy, startWork] = useTransition()
  const path = `/profiles/${encodeURIComponent(item.id)}`

  function find() {
    startWork(async () => {
      try {
        setFound(await apiSend<UnmuffleResponse>(`${path}/unmuffle`, 'POST'))
      } catch (err: unknown) {
        onStatus({ message: `直す量を探せませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  const fixed =
    found?.status === 'fixed' && found.after !== null && found.amount !== null
      ? { before: found.before, after: found.after, amount: found.amount }
      : null

  function save(amount: number) {
    if (name.trim() === '') return
    startWork(async () => {
      try {
        const created = await apiSend<ProfileItem>('/profiles/mix', 'POST', {
          name: name.trim(),
          caption: item.caption,
          voices: [{ voice: item.id, weight: 1 }],
          unmuffle: amount,
        })
        await invalidateProfiles(queryClient)
        onStatus(createdStatus(created, '保存しました'))
        onCreated(created.id)
      } catch (err: unknown) {
        onStatus({ message: `保存できませんでした: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  return (
    <div className="max-w-[52em] space-y-3 text-sm">
      <div>
        <p className="font-medium text-destructive">こもった音です</p>
        <p className="text-xs text-muted-foreground">
          12kHz
          より上が欠けています。こもりを取った声を別の声として保存できます（元の声はそのまま残ります）。
          {!available && 'いまの合成モデルでは直せません。'}
        </p>
      </div>
      {busy ? (
        <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
          <LoaderCircle className="size-3.5 animate-spin" />
          {fixed ? '保存しています' : '直す量を探しています（数十秒）'}
        </p>
      ) : (
        <Found found={found} available={available} onFind={find} />
      )}
      {!busy && fixed && (
        <div className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-2">
              <PlayButton
                aria-label="直す前を再生"
                onClick={() => onPlay(audioFileUrl(fixed.before.audio))}
              />
              前
            </span>
            <span className="flex items-center gap-2">
              <PlayButton
                voice={item.id}
                aria-label="直した後を再生"
                onClick={() => onPlay(audioFileUrl(fixed.after.audio))}
              />
              後
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="直した声の名前"
              className="h-8 max-w-72"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Button variant="ghost" size="sm" onClick={() => setFound(null)}>
              やめる
            </Button>
            <Button size="sm" disabled={name.trim() === ''} onClick={() => save(fixed.amount)}>
              別の声として保存
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

// 探す前のボタンと、直せなかった・直す必要がなかったときの知らせ
function Found({
  found,
  available,
  onFind,
}: {
  found: UnmuffleResponse | null
  available: boolean
  onFind: () => void
}) {
  if (found === null) {
    return available ? (
      <Button variant="outline" size="sm" onClick={onFind}>
        こもりを取る
      </Button>
    ) : null
  }
  if (found.status === 'not_needed') {
    return (
      <p className="text-xs text-muted-foreground">
        読ませてみたところ、こもっていませんでした。直す必要はありません。
      </p>
    )
  }
  if (found.status === 'unfixable') {
    return (
      <p className="text-xs text-muted-foreground">
        声を変えずに足せる量の上限でも、こもりが取れませんでした。別の作り方で作り直してください。
      </p>
    )
  }
  return null
}
