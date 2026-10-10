import { useQueryClient } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { useState, useTransition } from 'react'

import { ConfirmButton } from '@/components/ConfirmButton'
import { Button } from '@/components/ui/button'
import { apiSend, toErrorMessage } from '@/lib/api'
import { useTransientStatus } from '@/lib/useTransientStatus'

import { invalidateProfiles, type ProfileStatus } from './queries'
import { tomlSnippet } from './tomlSnippet'
import type { Take } from './useTakes'

const STATUS_LABEL: Record<Take['status'], string> = {
  waiting: '待機中',
  running: '生成中…',
  done: '',
  error: '失敗',
}

type Props = {
  take: Take
  // 採用先のプロファイルと、いま保存されている caption
  profileId: string
  adoptedCaption: string
  // 生成中や pairvoice が止まっているときは再生成を受け付けない
  canRetry: boolean
  onPlay: () => void
  onRetry: () => void
  onAdopted: (status: ProfileStatus) => void
}

export function TakeCard({
  take,
  profileId,
  adoptedCaption,
  canRetry,
  onPlay,
  onRetry,
  onAdopted,
}: Props) {
  const queryClient = useQueryClient()
  const [adopting, startAdopt] = useTransition()
  // 「コピーしました」は少しして元の文言に戻す（別のテイクを続けてコピーしたかが分かるように）
  const [copyStatus, setCopyStatus] = useState<{ isError: boolean } | null>(null)
  const copied = useTransientStatus(copyStatus) !== null
  const snippet = tomlSnippet(take.input.values)
  const captionChanged = take.input.caption !== adoptedCaption

  function adopt() {
    startAdopt(async () => {
      try {
        // 採用はプロファイルの caption の書き換え。常駐サーバーが合成のたびに読むので
        // 再起動は要らない
        await apiSend(`/profiles/${encodeURIComponent(profileId)}`, 'PATCH', {
          caption: take.input.caption,
        })
        await invalidateProfiles(queryClient)
        onAdopted({ message: '採用しました（次の読み上げから効きます）', isError: false })
      } catch (err: unknown) {
        onAdopted({ message: `採用に失敗しました: ${toErrorMessage(err)}`, isError: true })
      }
    })
  }

  function copy() {
    if (!snippet) return
    void navigator.clipboard?.writeText(snippet).then(
      () => setCopyStatus({ isError: false }),
      () => setCopyStatus(null),
    )
  }

  return (
    <li className="rounded-lg border bg-card p-3">
      <div className="flex items-start gap-3">
        <Button
          variant="ghost"
          size="icon"
          className="size-8 flex-none text-primary"
          aria-label={`テイク ${take.id} を再生`}
          disabled={!take.relativePath}
          onClick={onPlay}
        >
          <Play className="size-4" aria-hidden="true" />
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 text-sm">
            <span className="font-medium">#{take.id}</span>
            {/* どの seed の音かを常に出す。分からないと聞き比べの結論を持ち帰れない */}
            <span className="font-mono text-xs text-muted-foreground">seed {take.seed}</span>
            {take.duration !== undefined && (
              <span className="font-mono text-xs text-muted-foreground">
                {take.duration.toFixed(1)} 秒
              </span>
            )}
            {take.elapsedMs !== undefined && (
              <span className="font-mono text-xs text-muted-foreground">
                生成 {(take.elapsedMs / 1000).toFixed(1)}s
              </span>
            )}
            {STATUS_LABEL[take.status] && (
              <span className="text-xs text-muted-foreground">{STATUS_LABEL[take.status]}</span>
            )}
          </div>
          <p className="truncate text-xs text-muted-foreground" title={take.input.caption}>
            {captionChanged ? 'caption 変更あり: ' : 'caption 保存済み: '}
            {take.input.caption || '（なし）'}
          </p>
          {take.message && <p className="text-xs text-destructive">{take.message}</p>}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1 pl-11">
        <Button variant="ghost" size="sm" disabled={!canRetry} onClick={onRetry}>
          この seed で再生成
        </Button>
        <ConfirmButton
          variant="ghost"
          idle="この caption を採用"
          armed="採用してよいですか"
          disabled={adopting || !captionChanged}
          onConfirm={adopt}
        />
        <Button
          variant="ghost"
          size="sm"
          disabled={!snippet}
          title={snippet ? undefined : 'サンプラーはモデル既定のまま'}
          onClick={copy}
        >
          {copied ? 'コピーしました' : 'サンプラーを TOML でコピー'}
        </Button>
      </div>
    </li>
  )
}
