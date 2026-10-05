import { useQueryClient } from '@tanstack/react-query'
import { useState, useTransition } from 'react'

import { apiSend, toErrorMessage } from '@/lib/api'

// minutes が null なら解除。サーバーは minutes の有無で /mute と /unmute を分けるので、
// 解除は null を明示して送る。
export const MUTE_CHOICES = [
  { label: '30分ミュート', minutes: 30 },
  { label: '1時間ミュート', minutes: 60 },
  { label: 'ミュートを解除', minutes: null },
] as const

export function useMute() {
  const queryClient = useQueryClient()
  const [pending, startTransition] = useTransition()
  const [error, setError] = useState('')

  // 失敗を投げ直さない。サイドバーとコマンドパレットには局所的な error boundary が無く、
  // 投げると root の errorComponent まで飛んで画面ごと消える。
  function choose(minutes: number | null) {
    setError('')
    startTransition(async () => {
      try {
        await apiSend('/api/mute', 'POST', { minutes })
        await queryClient.invalidateQueries({ queryKey: ['health'] })
      } catch (err: unknown) {
        setError(toErrorMessage(err))
      }
    })
  }

  return { pending, error, choose }
}
