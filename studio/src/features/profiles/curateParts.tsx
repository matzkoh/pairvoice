import { cn } from 'cn'

import type { CurateAnalysis, CurateCandidate } from '@/lib/api-types'

// 横のタイムラインと縦の一覧が共に使う、候補の区間の描き方

export type CurateFile = CurateAnalysis['files'][number]

// 候補の区間の名前。ファイルの中の時刻で示す（流れの秒はファイルの間の無音を含む）
export function clipLabel(c: CurateCandidate, file: CurateFile | undefined): string {
  const offset = file?.offset ?? 0
  return `区間 ${formatClock(c.start - offset)}〜${formatClock(c.end - offset)}`
}

// ふだんの声からの離れ。0 で空、3 以上で満ちる。張り上げた声ほど長い
export function DeviationBar({
  deviation,
  className,
}: {
  deviation: number | null
  className?: string
}) {
  if (deviation === null) return null
  const ratio = Math.min(1, deviation / 3)
  return (
    <span aria-hidden className={cn('block h-1 rounded-full bg-current/15', className)}>
      <span
        className={cn('block h-full rounded-full', ratio > 0.5 ? 'bg-amber-500' : 'bg-current/60')}
        style={{ width: `${Math.max(4, ratio * 100)}%` }}
      />
    </span>
  )
}

export function describeCandidate(
  c: CurateCandidate,
  file: CurateFile | undefined,
  transcript?: string | null,
): string {
  const offset = file?.offset ?? 0
  const lines = [
    `${file?.name ?? ''} ${formatClock(c.start - offset)}〜${formatClock(c.end - offset)}（${(c.end - c.start).toFixed(1)} 秒）`,
    transcript ? `「${transcript}」` : null,
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
  return lines.filter(Boolean).join('\n')
}

// 素材の音の大きさを、流れの view.left〜view.left + view.width（px、1秒 = pps px）の範囲だけ描く
export function drawEnvelope(
  canvas: HTMLCanvasElement,
  envelope: CurateAnalysis['envelope'],
  view: { left: number; width: number },
  pps: number,
  height: number,
) {
  const ratio = window.devicePixelRatio || 1
  canvas.width = Math.round(view.width * ratio)
  canvas.height = Math.round(height * ratio)
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.scale(ratio, ratio)
  ctx.clearRect(0, 0, view.width, height)
  ctx.fillStyle = getComputedStyle(canvas).color
  const mid = height / 2
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

export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m)
  return `${h > 0 ? `${h}:` : ''}${mm}:${String(s).padStart(2, '0')}`
}
