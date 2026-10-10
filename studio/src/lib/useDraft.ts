import { useState } from 'react'

// サーバーの値を手元で書き換える下書き。サーバーの値が変わったら取り直す（React 公式の
// 「props が変わったら render 中に state を調整する」パターン）。ただし自分が送った値が
// 戻ってきただけなら、送った後に打ち足した分を上書きしない
export function useDraft(saved: string) {
  const [draft, setDraft] = useState(saved)
  const [seen, setSeen] = useState(saved)
  const [submitted, setSubmitted] = useState<string | null>(null)
  if (saved !== seen) {
    setSeen(saved)
    if (saved !== submitted) setDraft(saved)
    setSubmitted(null)
  }
  // 送るときに markSubmitted(送った値)、失敗したら markSubmitted(null)
  return { draft, setDraft, markSubmitted: setSubmitted }
}
