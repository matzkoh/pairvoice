import { queryOptions } from '@tanstack/react-query'

import type { HistorySource } from '@/features/history/queries'
import { apiGet } from '@/lib/api'

import type { PromptResponse } from '../../../../shared/api-types'

// 要約のプロンプトは、共通の部分に声の口調を足して組む（pairvoice が読み上げのたびに組む）。
// この画面で直すのは共通の部分と、口調を持たない声が使う既定の口調。声の口調はプロファイル画面で直す
export type PromptTarget = 'common' | 'tone'

// URL の ?target= の値。無ければ共通
export function parseTarget(value: string | undefined): PromptTarget {
  return value === 'tone' ? 'tone' : 'common'
}

// 本文は path、履歴は `${path}/history`、復元は `${path}/restore`
export const TARGET_SOURCES: Record<PromptTarget, HistorySource> = {
  common: { path: '/prompt', key: ['prompt'] },
  tone: { path: '/tone', key: ['tone'] },
}

export function textQueryOptions(source: HistorySource) {
  return queryOptions({
    queryKey: source.key,
    queryFn: () => apiGet<PromptResponse>(source.path),
  })
}
