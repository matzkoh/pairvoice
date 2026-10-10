import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState, useTransition } from 'react'

import { apiSend, toErrorMessage } from '@/lib/api'
import type { CorpusItem, CorpusResponse } from '@/lib/api-types'

import { CORPUS_COUNTS_KEY } from './queries'

export const BULK_ERROR_ID = '__bulk__'

type Kind = 'review' | 'archive'

function flightKey(kind: Kind, id: string) {
  return `${kind}:${id}`
}

// vote / toggleArchive は操作後の行の姿を返す。呼び出し側はそれを見て、行が今の
// 絞り込みから外れるなら選択を次の行へ移す（投稿の完了を待たずに動かすため）。
// 受け付けなかった（送らなかった）ときは null、saveIdeal は送ったかどうかを返す
type ReviewActions = {
  pending: boolean
  // message_id（一括は BULK_ERROR_ID）ごとの失敗理由。選択が次の行へ移っても、失敗した行に残す
  errors: Readonly<Record<string, string>>
  vote: (item: CorpusItem, clicked: 'good' | 'bad') => CorpusItem | null
  saveIdeal: (item: CorpusItem, text: string) => boolean
  toggleArchive: (item: CorpusItem) => CorpusItem | null
  bulkArchive: (targets: readonly CorpusItem[]) => void
}

// 投票・アーカイブはキャッシュを直接書き換えて即時に反映する（invalidate すると
// corpus の全件取り直しになる）。失敗は投げ直さず行に出す。投げると root の
// errorComponent まで飛んでレビュー画面ごと消える
export function useReviewActions(): ReviewActions {
  const queryClient = useQueryClient()
  const [pending, startTransition] = useTransition()
  const [errors, setErrors] = useState<Readonly<Record<string, string>>>({})
  // 投稿が返るまでは、その行の同じ種類の操作を受け付けない。ボタンは pending で止まるが
  // ホットキーは止まらず、連打すると投稿前の同じ行から同じ判定を二重に送る（取り消しにも
  // ならない）。種類は送り先ごと（判定と理想の出力はどちらも /reviews）で分け、
  // 投票の直後のアーカイブは止めない
  const inFlight = useRef(new Set<string>())

  function patchItems(messageIds: readonly string[], patch: Partial<CorpusItem>) {
    const ids = new Set(messageIds)
    queryClient.setQueryData<CorpusResponse>(['corpus'], (prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((r) => (ids.has(r.message_id) ? { ...r, ...patch } : r)),
          }
        : prev,
    )
    void queryClient.invalidateQueries({ queryKey: CORPUS_COUNTS_KEY })
  }

  function run(id: string, kind: Kind, label: string, work: () => Promise<void>) {
    setErrors((prev) => {
      if (!(id in prev)) return prev
      const next = { ...prev }
      delete next[id]
      return next
    })
    const key = flightKey(kind, id)
    inFlight.current.add(key)
    startTransition(async () => {
      try {
        await work()
      } catch (err: unknown) {
        const message = `${label}に失敗しました: ${toErrorMessage(err)}`
        setErrors((prev) => ({ ...prev, [id]: message }))
      } finally {
        inFlight.current.delete(key)
      }
    })
  }

  function submitVerdict(
    item: CorpusItem,
    verdict: 'good' | 'bad' | null,
    ideal: string,
  ): CorpusItem {
    const patch = { verdict, ideal: verdict === null ? null : ideal }
    run(item.message_id, 'review', '判定の投稿', async () => {
      await apiSend('/reviews', 'POST', {
        message_id: item.message_id,
        verdict: verdict ?? 'none',
        ideal,
      })
      patchItems([item.message_id], patch)
    })
    return { ...item, ...patch }
  }

  function vote(item: CorpusItem, clicked: 'good' | 'bad') {
    // 旧プロンプトの出力は、いまのプロンプトの挙動ではない。判定しても手がかりにならないので受け付けない
    if (item.stale || inFlight.current.has(flightKey('review', item.message_id))) return null
    // 同じ判定をもう一度押すと取り消し
    const verdict = item.verdict === clicked ? null : clicked
    return submitVerdict(item, verdict, verdict === 'bad' ? (item.ideal ?? '') : '')
  }

  function saveIdeal(item: CorpusItem, text: string) {
    if (item.verdict !== 'bad' || text === (item.ideal ?? '')) return false
    if (inFlight.current.has(flightKey('review', item.message_id))) return false
    submitVerdict(item, 'bad', text)
    return true
  }

  function toggleArchive(item: CorpusItem) {
    if (inFlight.current.has(flightKey('archive', item.message_id))) return null
    const patch = { archived: !item.archived }
    run(item.message_id, 'archive', 'アーカイブの操作', async () => {
      await apiSend('/archives', 'POST', { message_id: item.message_id, ...patch })
      patchItems([item.message_id], patch)
    })
    return { ...item, ...patch }
  }

  function bulkArchive(targets: readonly CorpusItem[]) {
    if (targets.length === 0) return
    const ids = targets.map((r) => r.message_id)
    run(BULK_ERROR_ID, 'archive', '一括アーカイブ', async () => {
      await apiSend('/archives/bulk', 'POST', { message_ids: ids, archived: true })
      patchItems(ids, { archived: true })
    })
  }

  return { pending, errors, vote, saveIdeal, toggleArchive, bulkArchive }
}
