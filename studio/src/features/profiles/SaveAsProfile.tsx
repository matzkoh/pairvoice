import { useQueryClient } from '@tanstack/react-query'
import { LoaderCircle, Save } from 'lucide-react'
import { useState, useTransition } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { toErrorMessage } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'
import { isImeConfirm } from '@/lib/hotkeys'

import { createdStatus, invalidateProfiles, type ProfileStatus } from './queries'

type Props = {
  // 名前欄の読み上げ名（「候補 #1」など）
  label: string
  // 名前を付けた声を作る（サーバーが参照音声を読ませるので 30 秒ほどかかる）
  create: (name: string) => Promise<ProfileItem>
  // 名前の欄を開くボタンの文言。prominent なら塗りつぶしの主ボタンにする
  openLabel?: string
  prominent?: boolean
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// 選んだ声に名前を付けて、プロファイルにする。名前の欄は保存すると
// 決めたときだけ開く
export function SaveAsProfile({
  label,
  create,
  openLabel = '保存',
  prominent = false,
  onStatus,
  onCreated,
}: Props) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [saving, startSave] = useTransition()
  const [saved, setSaved] = useState(false)

  function save() {
    if (name.trim() === '') return
    startSave(async () => {
      try {
        const created = await create(name.trim())
        await invalidateProfiles(queryClient)
        setSaved(true)
        onStatus(createdStatus(created, '保存しました'))
        onCreated(created.id)
      } catch (err: unknown) {
        onStatus({ message: `保存に失敗しました: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  if (saved) return <span className="text-xs text-muted-foreground">保存済み</span>
  if (saving) {
    return (
      <div role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
        <LoaderCircle className="size-3.5 animate-spin" />
        参照音声を作っています（30 秒ほど）…
      </div>
    )
  }
  if (!open) {
    return (
      <Button
        variant={prominent ? 'default' : 'ghost'}
        size="sm"
        aria-label={`${label} を保存`}
        onClick={() => setOpen(true)}
      >
        <Save />
        {openLabel}
      </Button>
    )
  }
  return (
    <div className="flex items-center gap-2">
      <Input
        aria-label={`${label} の名前`}
        className="h-7 w-40"
        placeholder="プロファイルの名前"
        value={name}
        onChange={(e) => setName(e.target.value)}
        autoFocus
        onKeyDown={(e) => {
          // 変換を確定する Enter では作らない
          if (e.key === 'Enter' && !isImeConfirm(e.nativeEvent)) save()
          if (e.key === 'Escape') setOpen(false)
        }}
      />
      <Button size="sm" disabled={saving || name.trim() === ''} onClick={save}>
        この声で作る
      </Button>
    </div>
  )
}
