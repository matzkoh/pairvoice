import { cn } from 'cn'
import { Play, Square } from 'lucide-react'
import { useEffect, useRef } from 'react'

import type { CurateAnalysis, CurateCandidate } from '@/lib/api-types'

import {
  clipLabel,
  type CurateFile,
  DeviationBar,
  describeCandidate,
  drawEnvelope,
  formatClock,
} from './curateParts'
import { useExcerptPlayer } from './useExcerptPlayer'

type Props = {
  analysis: CurateAnalysis
  selected: ReadonlySet<number>
  transcripts: ReadonlyMap<number, string | null>
  onToggle: (index: number) => void
}

const WAVE_WIDTH = 96
const WAVE_HEIGHT = 28

// 候補の区間を流れの順に1行ずつ並べ、セリフを読んで選べるようにする（タイムラインを縦にしたもの）。
// 行の間には、候補にならなかった部分の長さを示す
export function CurateList({ analysis, selected, transcripts, onToggle }: Props) {
  const player = useExcerptPlayer(analysis.job_id)
  return (
    <ol className="max-h-[32rem] divide-y overflow-y-auto rounded-lg border bg-background">
      {analysis.candidates.map((c, i) => {
        const previous = analysis.candidates[i - 1]
        const gap = c.start - (previous?.end ?? 0)
        const file = analysis.files[c.file]
        const span = { start: c.start, end: c.end }
        const playing = player.isPlaying(span)
        return (
          <li key={c.index}>
            {previous && gap >= 1 && (
              <div className="bg-muted/40 px-3 py-0.5 text-[10px] text-muted-foreground tabular-nums">
                {formatGap(gap)}
              </div>
            )}
            <Row
              candidate={c}
              file={file}
              envelope={analysis.envelope}
              selected={selected.has(c.index)}
              playing={playing}
              transcript={transcripts.get(c.index)}
              onToggle={() => onToggle(c.index)}
              onPlay={() => (playing ? player.stop() : player.play(span))}
            />
          </li>
        )
      })}
    </ol>
  )
}

type RowProps = {
  candidate: CurateCandidate
  file: CurateFile | undefined
  envelope: CurateAnalysis['envelope']
  selected: boolean
  playing: boolean
  transcript: string | null | undefined
  onToggle: () => void
  onPlay: () => void
}

function Row({
  candidate: c,
  file,
  envelope,
  selected,
  playing,
  transcript,
  onToggle,
  onPlay,
}: RowProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const seconds = c.end - c.start

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const pps = WAVE_WIDTH / seconds
    drawEnvelope(canvas, envelope, { left: c.start * pps, width: WAVE_WIDTH }, pps, WAVE_HEIGHT)
  }, [envelope, c.start, seconds])

  return (
    <div
      className={cn(
        'flex items-center gap-3 px-3 py-2 text-sm',
        selected ? 'bg-primary/10' : c.deviation === null && 'opacity-60',
        playing && 'ring-2 ring-destructive ring-inset',
      )}
    >
      <span className="w-12 shrink-0 text-xs text-muted-foreground tabular-nums">
        {formatClock(c.start - (file?.offset ?? 0))}
      </span>
      <button
        type="button"
        aria-label="この区間を再生"
        className="flex size-7 shrink-0 items-center justify-center rounded-md hover:bg-accent"
        onClick={onPlay}
      >
        {playing ? <Square className="size-3.5" /> : <Play className="size-3.5" />}
      </button>
      <canvas
        ref={canvasRef}
        aria-hidden
        className="shrink-0 text-muted-foreground"
        style={{ width: WAVE_WIDTH, height: WAVE_HEIGHT }}
      />
      <button
        type="button"
        aria-pressed={selected}
        aria-label={clipLabel(c, file)}
        title={describeCandidate(c, file, transcript)}
        onClick={onToggle}
        className="flex min-w-0 flex-1 items-center gap-3 rounded-md px-2 py-1 text-left hover:bg-accent"
      >
        <span
          aria-hidden
          className={cn(
            'flex size-4 shrink-0 items-center justify-center rounded-sm border',
            selected
              ? 'border-primary bg-primary text-primary-foreground'
              : 'border-muted-foreground/50',
          )}
        >
          {selected && '✓'}
        </span>
        <span className="w-10 shrink-0 text-xs text-muted-foreground tabular-nums">
          {seconds.toFixed(1)}s
        </span>
        <span
          className={cn(
            'min-w-0 flex-1 break-words',
            transcript === undefined && 'text-muted-foreground',
          )}
        >
          {transcript === undefined ? '…' : (transcript ?? '（聞き取れませんでした）')}
        </span>
        <DeviationBar deviation={c.deviation} className="w-12 shrink-0 text-muted-foreground" />
      </button>
    </div>
  )
}

function formatGap(seconds: number): string {
  return seconds >= 60 ? `${formatClock(seconds)} 空き` : `${Math.round(seconds)} 秒空き`
}
