import { useQuery } from '@tanstack/react-query'
import { cn } from 'cn'
import { Play } from 'lucide-react'
import { useEffect, useEffectEvent, useState } from 'react'

import { Button } from '@/components/ui/button'
import { toErrorMessage } from '@/lib/api'
import { audioFileUrl } from '@/lib/speak'

import { MASTER_STEPS, mixQueryOptions, recordTakes, REFERENCE_TEXTS, synth } from './mixSynth'
import { ProgressBar } from './ProgressBar'
import type { ProfileStatus } from './queries'
import { RetryNote } from './RetryNote'
import { SaveAsProfile } from './SaveAsProfile'
import type { MixPart } from './voiceRegion'

// 範囲がこれだけ狭まったら目立たせ始め、ここまで狭まったら決めどき
const WARM = 0.3
const READY = 0.5

type Props = {
  mix: readonly MixPart[]
  text: string
  caption: string
  seed: number
  // 候補の範囲がどれだけ狭まったか（0〜1）。狭まるほど、この声で決める段に来たと目立たせる
  narrowed: number
  // 鳴らす（プロファイルの画面の再生を使う）
  onPlay: (url: string) => void
  onStatus: (status: ProfileStatus) => void
  onCreated: (id: string) => void
}

// 見つけた声（候補の範囲の真ん中、これまでの答えを積み上げた声）を聴いて、気に入ったら
// この声に決める。2択を終わらせる出口なので、範囲が狭まるほど目立たせる。
// 2択を聞いている間は Runner を空けておきたいので、聴くと決めたときだけ合成する
export function BestVoice({
  mix,
  text,
  caption,
  seed,
  narrowed,
  onPlay,
  onStatus,
  onCreated,
}: Props) {
  // 聴くと決めた声。範囲が動いたら、また聴くと決めるまで合成しない
  const [wantedFor, setWantedFor] = useState<readonly MixPart[] | null>(null)
  const wanted = wantedFor === mix
  const take = useQuery({ ...mixQueryOptions(text, caption, seed, mix), enabled: wanted })
  const path = take.data?.relative_path

  function play() {
    if (path) onPlay(audioFileUrl(path))
  }

  // 聴くと決めて合成を待っていたなら、できたところで鳴らす
  const onReady = useEffectEvent(() => {
    if (wanted) play()
  })
  useEffect(() => {
    if (path) onReady()
  }, [path])

  const stage = narrowed >= READY ? 'ready' : narrowed >= WARM ? 'warm' : 'early'

  return (
    <section
      aria-label="見つけた声"
      className={cn(
        'space-y-3 rounded-xl border-2 p-4 transition-colors',
        stage === 'ready' && 'border-primary bg-primary/5 shadow-sm',
        stage === 'warm' && 'border-primary/40 bg-background',
        stage === 'early' && 'border-border bg-muted/30',
      )}
    >
      <div className="flex items-center gap-3">
        <h3 className="text-base font-semibold">見つけた声</h3>
        <span className="text-xs text-muted-foreground tabular-nums">
          絞り込み {Math.round(narrowed * 100)}%
        </span>
        <ProgressBar value={narrowed} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={take.isFetching}
          onClick={() => (path ? play() : setWantedFor(mix))}
        >
          <Play />
          {take.isFetching ? '生成中…' : '聴く'}
        </Button>
        <SaveAsProfile
          label="見つけた声"
          // 声は参照音声が決めるので、プロファイルの caption は空にする（事前の質問から作った
          // 「女性の声。」のような caption は声を言い直すだけ。話し方を足したければ後で書ける）
          caption=""
          openLabel="この声に決める"
          prominent={stage === 'ready'}
          record={(progress) =>
            recordTakes(
              [text, ...REFERENCE_TEXTS],
              (t) => synth(t, caption, seed, mix, MASTER_STEPS),
              progress,
            )
          }
          onStatus={onStatus}
          onCreated={onCreated}
        />
      </div>
      {take.isError && (
        <RetryNote
          message={`合成に失敗しました: ${toErrorMessage(take.error)}`}
          onRetry={() => void take.refetch()}
        />
      )}
    </section>
  )
}
