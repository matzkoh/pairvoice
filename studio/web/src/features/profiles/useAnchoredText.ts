import { useState } from 'react'

// 候補に読ませる文の入力欄。pairvoice が後から起きて文が届いたときに追従させるため、
// 書き換えるまでは申告された文をそのまま見せる
export function useAnchoredText(anchorText: string) {
  const [edited, setEdited] = useState<string | null>(null)
  return [edited ?? anchorText, setEdited] as const
}
