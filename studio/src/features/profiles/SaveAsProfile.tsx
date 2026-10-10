import { useQueryClient } from '@tanstack/react-query'
import { LoaderCircle, Save } from 'lucide-react'
import { useState, useTransition } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { apiSend, toErrorMessage } from '@/lib/api'
import type { ProfileItem } from '@/lib/api-types'
import { isImeConfirm } from '@/lib/hotkeys'

import { ProgressBar } from './ProgressBar'
import { invalidateProfiles, type ProfileStatus } from './queries'

type Props = {
  // 名前欄の読み上げ名（「候補 #1」など）
  label: string
  caption: string
  // 参照音声のテイクを作る（同じ声で別の文も読ませ、サーバーでつないで1本にする）。
  // 作った wav（データの置き場所からの相対パス）を返す
  record: (progress: (done: number, total: number) => void) => Promise<string[]>
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
  caption,
  record,
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
  // 参照音声を作っている間の進み具合（作り終えた本数と全体の本数）。ボタンのそばに出す
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)

  function save() {
    if (name.trim() === '') return
    startSave(async () => {
      try {
        const takes = await record((done, total) => setProgress({ done, total }))
        setProgress({ done: takes.length, total: takes.length })
        const created = await apiSend<ProfileItem>('/profiles', 'POST', {
          name: name.trim(),
          caption,
          takes,
        })
        await invalidateProfiles(queryClient)
        setSaved(true)
        onStatus({ message: `「${created.name}」を保存しました`, isError: false })
        onCreated(created.id)
      } catch (err: unknown) {
        onStatus({ message: `保存に失敗しました: ${toErrorMessage(err)}`, isError: true })
      } finally {
        setProgress(null)
      }
    })
  }

  if (saved) return <span className="text-xs text-muted-foreground">保存済み</span>
  if (saving) {
    const making = progress !== null && progress.done < progress.total
    return (
      <div role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
        <LoaderCircle className="size-3.5 animate-spin" />
        <span className="tabular-nums">
          {making ? `参照音声を作っています ${progress.done}/${progress.total}` : '保存しています…'}
        </span>
        {progress && (
          <div className="flex w-24">
            <ProgressBar value={progress.done / progress.total} />
          </div>
        )}
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
