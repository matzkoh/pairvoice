import { useRef, useState } from 'react'

import { ApiError, apiGetBlob, toErrorMessage } from '@/lib/api'

export type PlayerState = {
  audioRef: React.RefObject<HTMLAudioElement | null>
  error: string
  play: (messageId: string) => void
  handleAudioError: () => void
}

// レビュー画面の再生。<audio> は id ではなく ref で画面ごとに持つ。
//
// <audio src> への直接代入で再生する。成功時に余計なフェッチを増やさないため。
// 404 かどうかは onError が起きたときだけ、apiGetBlob を allowNotFound で叩き直して判定する。
export function usePlayer(): PlayerState {
  const audioRef = useRef<HTMLAudioElement>(null)
  // onError はどの再生が失敗したかを引数で教えてくれないので、直近に再生を試みた
  // message_id を控えておく。
  const playingIdRef = useRef<string | null>(null)
  const [error, setError] = useState('')

  function play(messageId: string) {
    setError('')
    playingIdRef.current = messageId
    const player = audioRef.current
    if (!player) return
    player.src = `/api/corpus/${encodeURIComponent(messageId)}/audio`
    void player.play().catch(() => {
      // autoplay 拒否等。実際のロード失敗（404 等）は <audio> の onError
      // （handleAudioError）側で拾う。
    })
  }

  async function handleAudioError() {
    const messageId = playingIdRef.current
    if (!messageId) return
    try {
      // 404 かどうかを判別するためだけに投げ直す（body は使わない）。応答からは
      // ルート未一致と区別できない（サーバーはどちらも同じ { error: 'not_found' }）
      // ため、このエンドポイントに 404 が起こり得ることを知っている呼び出し側で
      // 判定する。
      await apiGetBlob(`/api/corpus/${encodeURIComponent(messageId)}/audio`, {
        allowNotFound: true,
      })
      // 判定を待つ間に別の行を再生していたら、その再生のエラーではない
      if (playingIdRef.current !== messageId) return
      // ここに来るのは 200 なのに <audio> がエラーを起こした場合（デコード失敗等）。
      setError('再生できませんでした')
    } catch (err: unknown) {
      if (playingIdRef.current !== messageId) return
      if (err instanceof ApiError && err.status === 404) {
        setError('音声ファイルが見つかりません')
      } else {
        const message = toErrorMessage(err)
        setError(`再生に失敗しました: ${message}`)
      }
    }
  }

  return { audioRef, error, play, handleAudioError }
}
