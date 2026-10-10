import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { cn } from 'cn'
import { Play, Volume2 } from 'lucide-react'
import { useEffect, useEffectEvent, useRef } from 'react'

import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { toErrorMessage } from '@/lib/api'
import { useHotkeys } from '@/lib/hotkeys'
import { audioFileUrl } from '@/lib/speak'

import { mixQueryOptions } from './mixSynth'
import type { ProfileStatus } from './queries'
import { RetryNote } from './RetryNote'
import type { Choice, MixPart, Side } from './voiceRegion'

type Props = {
  // A と B の混ぜ具合
  mix: Record<Side, readonly MixPart[]>
  index: number
  text: string
  // 混ぜた声に読ませる caption（事前の質問で答えた軸だけ）。声の個体は混ぜた表現が決める
  caption: string
  seed: number
  onAnswer: (choice: Choice) => void
  // A と B が両方できた。聞いている間に、手持ちの声を増やし始める
  onReady: () => void
  // 鳴っている側。地図の印も脈打たせるので、持ち主は2択の外（問いを移ると作り直される）
  playing: Side | null
  onPlayingChange: (side: Side | null) => void
  onUndo?: () => void
  onReset: () => void
  onStatus: (status: ProfileStatus) => void
}

export function PickRound({
  mix,
  index,
  text,
  caption,
  seed,
  onAnswer,
  onReady,
  playing,
  onPlayingChange,
  onUndo,
  onReset,
  onStatus,
}: Props) {
  // A と B は1件ずつ順に合成する（まとめると Runner を塞ぎ、フックの読み上げが待たされる）。
  // A ができたらすぐ鳴らし、聞いている間に B を作る
  const a = useQuery(mixQueryOptions(text, caption, seed, mix.a))
  const b = useQuery({
    ...mixQueryOptions(text, caption, seed, mix.b),
    enabled: a.isSuccess,
  })
  const audio: Record<Side, UseQueryResult<{ relative_path: string }>> = { a, b }
  const ready = a.isSuccess && b.isSuccess
  const failed = a.isError ? a : b.isError ? b : null

  // 聞き比べは A と B を続けて聞くのが基本なので、2択の中に専用の再生を持つ。
  // queue は今の音の後に続けて鳴らす側
  const player = useRef<HTMLAudioElement>(null)
  const queue = useRef<Side[]>([])
  const starting = useRef<Side | null>(null)

  function start(side: Side) {
    const path = audio[side].data?.relative_path
    const el = player.current
    if (!path || !el) return
    starting.current = side
    el.src = audioFileUrl(path)
    // autoplay 拒否など。読み込みの失敗は <audio> の onError で拾う
    void el.play().catch(() => {})
  }

  function play(...sides: Side[]) {
    queue.current = sides.slice(1)
    start(sides[0]!)
  }

  // A ができたら鳴らし、B ができたら A の後に続ける（A を聞き終えていればすぐ鳴らす）
  const pathA = a.data?.relative_path
  const pathB = b.data?.relative_path
  const onReadyA = useEffectEvent(() => play('a'))
  const onReadyB = useEffectEvent(() => {
    const el = player.current
    if (el && !el.paused && starting.current === 'a') queue.current = ['b']
    else play('b')
  })
  useEffect(() => {
    if (pathA) onReadyA()
  }, [pathA])
  useEffect(() => {
    if (pathB) onReadyB()
  }, [pathB])
  const notifyReady = useEffectEvent(() => onReady())
  useEffect(() => {
    if (pathA && pathB) notifyReady()
  }, [pathA, pathB])
  // 問いを移ったら（答えても戻っても作り直される）、鳴っている音と続けて鳴らす予定を止める
  useEffect(() => {
    const [el, pending] = [player.current, queue]
    return () => {
      pending.current = []
      el?.pause()
    }
  }, [])

  useHotkeys({
    '1': () => play('a'),
    '2': () => play('b'),
    space: () => {
      if (!ready) return false
      play('a', 'b')
      return true
    },
  })

  return (
    <div className="max-w-2xl space-y-5">
      <h3 className="text-sm font-medium">第{index}問</h3>
      <div className="grid grid-cols-2 gap-3">
        {(['a', 'b'] as const).map((side, i) => {
          const path = audio[side].data?.relative_path
          const name = side.toUpperCase()
          return (
            <div
              key={side}
              aria-current={playing === side}
              className={cn(
                'space-y-3 rounded-lg border bg-background p-3 transition-shadow',
                playing === side && 'border-primary ring-2 ring-primary/40',
              )}
            >
              <Button
                variant="outline"
                className="h-14 w-full justify-start gap-3 text-base"
                aria-label={`${name} を再生`}
                disabled={!path}
                onClick={() => play(side)}
              >
                <Play className="size-5" />
                <span className="font-semibold">{name}</span>
                <Kbd>{i + 1}</Kbd>
                {audio[side].isFetching && (
                  <span className="text-xs font-normal text-muted-foreground">生成中…</span>
                )}
                {playing === side && (
                  <span className="ml-auto flex items-center gap-1 text-xs font-medium text-primary">
                    <Volume2 className="size-3.5 animate-pulse" />
                    再生中
                  </span>
                )}
              </Button>
              <Button size="sm" disabled={!ready} onClick={() => onAnswer(side)}>
                {name} が近い
              </Button>
            </div>
          )
        })}
      </div>
      {failed && (
        <RetryNote
          message={`合成に失敗しました: ${toErrorMessage(failed.error)}`}
          onRetry={() => void failed.refetch()}
        />
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" disabled={!ready} onClick={() => play('a', 'b')}>
          <Play />
          AB を続けて再生
          <Kbd>Space</Kbd>
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!ready && failed === null}
          // 違いが聞き取れない切り方だった。同じ候補を別の方向で切り直す
          onClick={() => onAnswer('skip')}
        >
          どちらとも言えない
        </Button>
        {onUndo && (
          <Button variant="ghost" size="sm" disabled={!ready && failed === null} onClick={onUndo}>
            ひとつ戻る
          </Button>
        )}
        <Button variant="ghost" size="sm" className="ml-auto" onClick={onReset}>
          最初から
        </Button>
      </div>
      <audio
        ref={player}
        hidden
        // play() を呼んだ時点ではなく、実際に鳴り始めてから示す（読み込み中や、裏のタブで
        // 再生が保留されている間に「再生中」と出さない）
        onPlaying={() => onPlayingChange(starting.current)}
        onPause={() => onPlayingChange(null)}
        onEnded={() => {
          const next = queue.current.shift()
          if (next) start(next)
        }}
        // 合成は成功しても、ファイルが消えていれば再生は無言で終わる。鳴らない理由を出す
        onError={() => onStatus({ message: '音声を再生できませんでした', isError: true })}
      />
    </div>
  )
}
