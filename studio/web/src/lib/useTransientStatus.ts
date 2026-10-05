import { useEffect, useState } from 'react'

// 成功メッセージだけ自動で消す。渡された値は書き換えず「いま表示すべき値」だけを返すので、
// useState 由来の値（声・辞書）にも、useActionState 由来で書き換えられない値（プロンプト）にも使える
const TIMEOUT_MS = 2000

export function useTransientStatus<T extends { isError: boolean } | null>(status: T): T | null {
  // 「消した」を真偽値でなく「どの値を消したか」で持つ。status が差し替われば
  // 比較が外れて自然に再表示されるので、effect の中で同期的に戻す必要がない。
  const [dismissed, setDismissed] = useState<T | null>(null)
  useEffect(() => {
    if (status && !status.isError) {
      const timer = setTimeout(() => setDismissed(status), TIMEOUT_MS)
      return () => clearTimeout(timer)
    }
    return undefined
  }, [status])
  return dismissed === status ? null : status
}
