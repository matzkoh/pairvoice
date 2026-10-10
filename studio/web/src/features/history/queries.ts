import { queryOptions } from '@tanstack/react-query'

import { apiGet } from '@/lib/api'

import type { HistoryResponse } from '../../../../shared/api-types'

// プロンプトとプロファイルの caption で同じ形（{name, ts} と復元ボタン）なので1つにまとめる。
// path は `${path}/history`（一覧）と `${path}/restore`（復元）の元、key は本体のキャッシュの
// キー。履歴は本体のキーの下に置くので、本体を取り直せば履歴も取り直される
export type HistorySource = { path: string; key: readonly unknown[] }

export const PROMPT_HISTORY: HistorySource = { path: '/prompt', key: ['prompt'] }

export function historyQueryOptions(source: HistorySource) {
  return queryOptions({
    queryKey: [...source.key, 'history'],
    queryFn: () => apiGet<HistoryResponse>(`${source.path}/history`),
  })
}
