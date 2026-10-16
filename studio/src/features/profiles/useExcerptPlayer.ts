import { useEffect, useRef, useState } from 'react'

import { apiUrl } from '@/lib/api'

export type Span = { start: number; end: number }

export type ExcerptPlayer = {
  span: Span | null
  play: (span: Span) => void
  stop: () => void
  isPlaying: (span: Span) => boolean
  // 鳴っている所（流れの秒）。毎フレーム読むので state にせず、読む側（Playhead）が自分で引く
  position: () => number | null
}

// 解析した素材の一部（流れの start〜end 秒）を鳴らす。
// 解析が替わったら呼び出し側で作り直す（key に job_id を渡す）
export function useExcerptPlayer(jobId: string): ExcerptPlayer {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [span, setSpan] = useState<Span | null>(null)

  function stop() {
    audioRef.current?.pause()
    setSpan(null)
  }

  function audio(): HTMLAudioElement {
    if (audioRef.current) return audioRef.current
    const created = new Audio()
    // 読み込みの失敗（解析が消えた等）も、鳴り終わりと同じく止まった扱いにする
    created.addEventListener('ended', stop)
    created.addEventListener('error', stop)
    audioRef.current = created
    return created
  }

  function play(next: Span) {
    const player = audio()
    player.src = apiUrl(`/curate/${jobId}/audio?start=${next.start}&end=${next.end}`)
    setSpan(next)
    // autoplay 拒否など。読み込みの失敗は error で拾う
    void player.play().catch(() => {})
  }

  useEffect(() => () => audioRef.current?.pause(), [])

  return {
    span,
    play,
    stop,
    isPlaying: (s) => span?.start === s.start && span.end === s.end,
    position: () => (span && audioRef.current ? span.start + audioRef.current.currentTime : null),
  }
}
