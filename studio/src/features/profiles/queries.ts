import { type QueryClient, queryOptions } from '@tanstack/react-query'

import type { HistorySource } from '@/features/history/queries'
import { apiGet } from '@/lib/api'
import type { ProfileItem, ProfilesResponse } from '@/lib/api-types'

// 画面上部に1行で出す操作の結果。プロファイル画面の部品（中身・試聴・作成）が共有する
export type ProfileStatus = { message: string; isError: boolean }

export function profilesQueryOptions() {
  return queryOptions({
    queryKey: ['profiles'] as const,
    queryFn: () => apiGet<ProfilesResponse>('/profiles'),
  })
}

// caption と口調の履歴はプロファイルの下のキーに置く。採用・復元で ['profiles'] を取り直せば含まれる
export function profileHistorySource(profileId: string, field: 'caption' | 'tone'): HistorySource {
  return {
    path: `/profiles/${encodeURIComponent(profileId)}/${field}`,
    key: ['profiles', profileId, field],
  }
}

// プロファイルを変えると /health の profile も変わる
export async function invalidateProfiles(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['profiles'] }),
    queryClient.invalidateQueries({ queryKey: ['health'] }),
  ])
}

// 作った声の知らせ。こもった声なら直し方を添える（保存は聞いた声のまま行い、差し替えない）
export function createdStatus(created: ProfileItem, verb: string): ProfileStatus {
  const done = `「${created.name}」を${verb}`
  return {
    message: created.muffled
      ? `${done}。こもった音です。声の画面の「こもりを取る」で直せます`
      : done,
    isError: false,
  }
}
