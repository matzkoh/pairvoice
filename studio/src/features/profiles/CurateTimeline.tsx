import { cn } from 'cn'
import { Maximize2, Play, Square, ZoomIn, ZoomOut } from 'lucide-react'
import {
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
  type WheelEvent,
} from 'react'

import { Button } from '@/components/ui/button'
import type { CurateAnalysis, CurateCandidate } from '@/lib/api-types'

import { useExcerptPlayer, type ExcerptPlayer, type Span } from './useExcerptPlayer'

type Props = {
  analysis: CurateAnalysis
  selected: ReadonlySet<number>
  onToggle: (index: number) => void
}

const RULER = 30
const WAVE = 56
const SPEAKERS = 8
const CLIPS = 40
const HEIGHT = RULER + WAVE + SPEAKERS + CLIPS + 8
const MIN_ZOOM = 0.5
// 初めの倍率の下限。いちばん短い区間（3 秒）にも ▶ が収まる（PLAY_MIN_PX 以上になる）ように
const START_ZOOM = 6
const PLAY_MIN_PX = 16
const MAX_ZOOM = 200
const ZOOM_STEP = 1.5
// 波形をクリックしたときに鳴らす長さ
const SEEK_SECONDS = 20
// 目盛りの間隔の候補（秒）。目盛りどうしが TICK_MIN_PX 以上離れる最小のものを使う
const TICKS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]
const TICK_MIN_PX = 64
// 見えている範囲の外にも、これだけ描いておく（スクロールで端が欠けて見えないように）
const OVERSCAN_PX = 400

// 素材の流れを映像編集ソフトのタイムラインのように並べ、候補の区間をクリックで選ぶ。
// 波形をクリックするとその位置から鳴らし、区間の ▶ でその区間だけを鳴らす
export function CurateTimeline({ analysis, selected, onToggle }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const duration = streamSeconds(analysis)
  const [zoom, setZoom] = useState<number | null>(null)
  const [view, setView] = useState({ left: 0, width: 0 })
  const player = useExcerptPlayer(analysis.job_id)
  const chosenSpeaker = analysis.speakers.find((s) => s.chosen)?.speaker

  // 初めは全体が収まる倍率にする（幅は描いてみないと分からない）。ただし区間が細くなりすぎない所まで
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    setView({ left: el.scrollLeft, width: el.clientWidth })
    setZoom((z) => z ?? Math.max(START_ZOOM, fitZoom(el.clientWidth, duration)))
  }, [duration])

  useEffect(() => {
    const el = scrollRef.current
    const observer = new ResizeObserver(() => {
      if (el) setView({ left: el.scrollLeft, width: el.clientWidth })
    })
    if (el) observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const pps = zoom ?? 1
  const width = Math.max(view.width, duration * pps)

  // 波形は見えている幅だけのキャンバスに描く（長い素材を全体の幅で描くと、キャンバスの大きさの上限を超える）
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || view.width === 0) return
    drawEnvelope(canvas, analysis.envelope, view, pps)
  }, [analysis.envelope, view, pps])

  function rezoom(next: number, anchorX = view.width / 2) {
    const el = scrollRef.current
    if (!el) return
    const clamped = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next))
    // 指している時刻を、拡大の前後で同じ位置に保つ
    const at = (el.scrollLeft + anchorX) / pps
    setZoom(clamped)
    requestAnimationFrame(() => {
      el.scrollLeft = at * clamped - anchorX
    })
  }

  function onWheel(e: WheelEvent<HTMLDivElement>) {
    if (!(e.metaKey || e.ctrlKey)) return
    e.preventDefault()
    const rect = e.currentTarget.getBoundingClientRect()
    rezoom(pps * Math.exp(-e.deltaY / 200), e.clientX - rect.left)
  }

  const visible = (start: number, end: number) =>
    end * pps >= view.left - OVERSCAN_PX && start * pps <= view.left + view.width + OVERSCAN_PX

  const tick = TICKS.find((t) => t * pps >= TICK_MIN_PX) ?? TICKS[TICKS.length - 1]!
  const ticks: number[] = []
  for (
    let t = Math.max(0, Math.floor((view.left - OVERSCAN_PX) / pps / tick) * tick);
    t <= Math.min(duration, (view.left + view.width + OVERSCAN_PX) / pps);
    t += tick
  ) {
    ticks.push(t)
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="縮小"
          onClick={() => rezoom(pps / ZOOM_STEP)}
        >
          <ZoomOut />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="拡大"
          onClick={() => rezoom(pps * ZOOM_STEP)}
        >
          <ZoomIn />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="全体を表示"
          onClick={() => {
            setZoom(fitZoom(view.width, duration))
            if (scrollRef.current) scrollRef.current.scrollLeft = 0
          }}
        >
          <Maximize2 />
        </Button>
        {player.span && (
          <Button variant="ghost" size="sm" onClick={player.stop}>
            <Square />
            止める
          </Button>
        )}
        <span className="ml-auto truncate text-xs text-muted-foreground">
          区間のクリックで選ぶ・外す／波形のクリックで再生／⌘ + スクロールで拡大
        </span>
      </div>
      <div
        ref={scrollRef}
        className="overflow-x-auto overflow-y-hidden rounded-lg border bg-background"
        onScroll={(e) =>
          setView({ left: e.currentTarget.scrollLeft, width: e.currentTarget.clientWidth })
        }
        onWheel={onWheel}
      >
        {/* 上の余白は padding で取る（波形の margin は外へ抜けて、全体を押し下げる） */}
        <div className="relative" style={{ width, height: HEIGHT, paddingTop: RULER }}>
          {/* ファイルの境目 */}
          {analysis.files.map((file, i) => (
            <div
              key={`${file.name}-${file.offset}`}
              className={cn('absolute top-0 bottom-0 border-l', i % 2 === 1 && 'bg-muted/40')}
              style={{ left: file.offset * pps, width: file.seconds * pps }}
            >
              <span className="sticky left-0 block truncate px-1 text-[10px] leading-4 text-muted-foreground">
                {file.name}
              </span>
            </div>
          ))}
          {/* 目盛り */}
          {ticks.map((t) => (
            <span
              key={t}
              className="absolute border-l border-border pl-0.5 text-[10px] leading-3.5 text-muted-foreground tabular-nums"
              style={{ left: t * pps, top: 16, height: RULER - 16 }}
            >
              {formatClock(t)}
            </span>
          ))}
          {/* 波形。見えている所に貼りつけて描き直す */}
          <canvas
            ref={canvasRef}
            className="sticky left-0 block text-muted-foreground"
            style={{ width: view.width, height: WAVE }}
          />
          <button
            type="button"
            aria-label="この位置から再生"
            className="absolute left-0 cursor-pointer"
            style={{ top: RULER, width, height: WAVE }}
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect()
              const start = (e.clientX - rect.left) / pps
              player.play({ start, end: Math.min(duration, start + SEEK_SECONDS) })
            }}
          />
          {/* 話者分離。いちばん多く話す人を濃く */}
          {analysis.segments
            .filter((s) => visible(s.start, s.end))
            .map((s) => (
              <div
                key={`${s.speaker}-${s.start}`}
                title={`話者 ${s.speaker}`}
                className={cn(
                  'absolute rounded-sm',
                  s.speaker === chosenSpeaker ? 'bg-primary/50' : 'bg-muted-foreground/30',
                )}
                style={{
                  left: s.start * pps,
                  width: Math.max(1, (s.end - s.start) * pps),
                  top: RULER + WAVE + 2,
                  height: SPEAKERS - 3,
                }}
              />
            ))}
          {/* 候補の区間 */}
          {analysis.candidates
            .filter((c) => visible(c.start, c.end))
            .map((c) => (
              <Clip
                key={c.index}
                candidate={c}
                pps={pps}
                selected={selected.has(c.index)}
                playing={player.isPlaying(c)}
                file={analysis.files[c.file]}
                onToggle={() => onToggle(c.index)}
                onPlay={(span) => (player.isPlaying(span) ? player.stop() : player.play(span))}
              />
            ))}
          {player.span && <Playhead player={player} pps={pps} />}
        </div>
      </div>
    </div>
  )
}

// 再生位置の線。毎フレーム動くので、タイムライン全体を描き直さないよう自分で位置を書き換える
function Playhead({ player, pps }: { player: ExcerptPlayer; pps: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const move = useEffectEvent(() => {
    const at = player.position()
    if (ref.current && at !== null) ref.current.style.left = `${at * pps}px`
  })
  useEffect(() => {
    let frame = 0
    const tick = () => {
      move()
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [])
  return (
    <div
      ref={ref}
      aria-hidden
      className="pointer-events-none absolute top-0 bottom-0 w-px bg-destructive"
      style={{ left: (player.span?.start ?? 0) * pps }}
    />
  )
}

type ClipProps = {
  candidate: CurateCandidate
  pps: number
  selected: boolean
  playing: boolean
  file: CurateAnalysis['files'][number] | undefined
  onToggle: () => void
  onPlay: (span: Span) => void
}

function Clip({ candidate: c, pps, selected, playing, file, onToggle, onPlay }: ClipProps) {
  const seconds = c.end - c.start
  const px = seconds * pps
  const fileOffset = file?.offset ?? 0
  return (
    <div
      className="absolute"
      style={{
        left: c.start * pps,
        width: Math.max(4, px),
        top: RULER + WAVE + SPEAKERS + 4,
        height: CLIPS,
      }}
    >
      <button
        type="button"
        aria-pressed={selected}
        aria-label={`区間 ${formatClock(c.start - fileOffset)}〜${formatClock(c.end - fileOffset)}`}
        title={describeCandidate(c, file?.name ?? '', fileOffset)}
        onClick={onToggle}
        className={cn(
          'relative block size-full overflow-hidden rounded-md border px-1 py-0.5 text-left text-[10px] leading-tight tabular-nums transition-colors',
          selected
            ? 'border-primary bg-primary text-primary-foreground'
            : 'border-dashed border-muted-foreground/40 bg-muted text-muted-foreground hover:bg-accent',
          c.deviation === null && !selected && 'opacity-50',
          playing && 'ring-2 ring-destructive',
        )}
      >
        <span className={cn('truncate', px >= 40 ? 'pl-4' : 'sr-only')}>{seconds.toFixed(1)}s</span>
        <DeviationBar deviation={c.deviation} />
      </button>
      {px >= PLAY_MIN_PX && (
        <button
          type="button"
          aria-label="この区間を再生"
          className={cn(
            'absolute top-0.5 left-0.5 flex size-4 items-center justify-center rounded-sm',
            selected
              ? 'text-primary-foreground hover:bg-primary-foreground/20'
              : 'hover:bg-background',
          )}
          onClick={() => onPlay({ start: c.start, end: c.end })}
        >
          {playing ? <Square className="size-2.5" /> : <Play className="size-2.5" />}
        </button>
      )}
    </div>
  )
}

// ふだんの声からの離れ。0 で空、3 以上で満ちる。張り上げた声ほど長い
function DeviationBar({ deviation }: { deviation: number | null }) {
  if (deviation === null) return null
  const ratio = Math.min(1, deviation / 3)
  return (
    // 長さの表示を隠す細い区間でも同じ所に出るよう、区間の下端に置く
    <span aria-hidden className="absolute inset-x-1 bottom-1 h-1 rounded-full bg-current/15">
      <span
        className={cn('block h-full rounded-full', ratio > 0.5 ? 'bg-amber-500' : 'bg-current/60')}
        style={{ width: `${Math.max(4, ratio * 100)}%` }}
      />
    </span>
  )
}

function describeCandidate(c: CurateCandidate, fileName: string, fileOffset: number): string {
  const lines = [
    `${fileName} ${formatClock(c.start - fileOffset)}〜${formatClock(c.end - fileOffset)}（${(c.end - c.start).toFixed(1)} 秒）`,
    [
      `S/N ${c.snr_db} dB`,
      c.loudness_db !== null && `大きさ ${c.loudness_db} dB`,
      `高さ ${c.pitch_hz !== null ? `${c.pitch_hz} Hz` : '—'}`,
      c.brightness_hz !== null && `明るさ ${c.brightness_hz} Hz`,
    ]
      .filter(Boolean)
      .join('・'),
    c.deviation === null
      ? '雑音が多いので自動では使いません'
      : `ふだんの声からの離れ ${c.deviation}（大きいほど張り上げた声）`,
  ]
  return lines.join('\n')
}

function drawEnvelope(
  canvas: HTMLCanvasElement,
  envelope: CurateAnalysis['envelope'],
  view: { left: number; width: number },
  pps: number,
) {
  const ratio = window.devicePixelRatio || 1
  canvas.width = Math.round(view.width * ratio)
  canvas.height = Math.round(WAVE * ratio)
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.scale(ratio, ratio)
  ctx.clearRect(0, 0, view.width, WAVE)
  ctx.fillStyle = getComputedStyle(canvas).color
  const mid = WAVE / 2
  for (let x = 0; x < view.width; x++) {
    // この1px に入る点のいちばん大きい値
    const from = Math.floor((view.left + x) / pps / envelope.step)
    const to = Math.max(from + 1, Math.floor((view.left + x + 1) / pps / envelope.step))
    let level = 0
    for (let i = from; i < to && i < envelope.levels.length; i++) {
      level = Math.max(level, envelope.levels[i] ?? 0)
    }
    const h = (level / 100) * (mid - 1)
    if (h > 0) ctx.fillRect(x, mid - h, 1, h * 2)
  }
}

function streamSeconds(analysis: CurateAnalysis): number {
  const last = analysis.files[analysis.files.length - 1]
  return last ? last.offset + last.seconds : 0
}

function fitZoom(width: number, duration: number): number {
  if (width <= 0 || duration <= 0) return 1
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, width / duration))
}

export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  return `${h > 0 ? `${h}:` : ''}${mm}:${String(s).padStart(2, '0')}`
}
