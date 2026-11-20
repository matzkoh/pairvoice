import { Pause, Play } from 'lucide-react'
import { type RefObject, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

// ヘッダーの中の、コントローラーを出す場所（AppHeader が置く）
export const PLAYER_SLOT_ID = 'player-slot'

const EVENTS = ['durationchange', 'timeupdate', 'play', 'pause', 'ended', 'emptied']

function clock(seconds: number): string {
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

type Snapshot = { duration: number; time: number; paused: boolean }

// 画面が持つ <audio> のコントローラー。止める・続ける・位置を動かすだけ。
// 画面の中に置くと、下に付く帯（保存・キーの案内）や表に重なるので、どの画面でも同じ
// ヘッダーの中に出す。音を読み込むまでは何も出さない
export function PlayerBar({ audioRef }: { audioRef: RefObject<HTMLAudioElement | null> }) {
  const [state, setState] = useState<Snapshot | null>(null)

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return undefined
    const read = () =>
      setState(
        Number.isFinite(audio.duration) && audio.duration > 0
          ? { duration: audio.duration, time: audio.currentTime, paused: audio.paused }
          : null,
      )
    for (const name of EVENTS) audio.addEventListener(name, read)
    return () => {
      for (const name of EVENTS) audio.removeEventListener(name, read)
    }
  }, [audioRef])

  // 音が読み込まれるのは操作の後なので、そのときにはヘッダーはもう描かれている
  const slot = state ? document.getElementById(PLAYER_SLOT_ID) : null
  if (!slot || !state) return null
  const audio = audioRef.current
  return createPortal(
    <div
      role="group"
      aria-label="再生のコントローラー"
      className="flex w-full max-w-72 items-center gap-2 rounded-full bg-background py-1 pr-3 pl-1 text-xs text-muted-foreground"
    >
      <button
        type="button"
        aria-label={state.paused ? '続きを再生' : '一時停止'}
        className="grid size-6 flex-none place-items-center rounded-full bg-primary text-primary-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50 [&_svg]:size-3"
        onClick={() => {
          if (!audio) return
          // autoplay 拒否など。読み込みの失敗は画面側の <audio> の onError が拾う
          if (audio.paused) void audio.play().catch(() => {})
          else audio.pause()
        }}
      >
        {state.paused ? (
          <Play fill="currentColor" strokeWidth={0} aria-hidden="true" />
        ) : (
          <Pause fill="currentColor" strokeWidth={0} aria-hidden="true" />
        )}
      </button>
      <input
        type="range"
        aria-label="再生位置"
        min={0}
        max={state.duration}
        step={0.05}
        value={state.time}
        className="h-1 min-w-0 flex-1"
        onChange={(e) => {
          if (audio) audio.currentTime = e.currentTarget.valueAsNumber
        }}
      />
      <span className="flex-none">
        {clock(state.time)} / {clock(state.duration)}
      </span>
    </div>,
    slot,
  )
}
