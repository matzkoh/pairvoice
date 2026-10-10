import { type QueryClient, queryOptions } from '@tanstack/react-query'

import type { HistorySource } from '@/features/history/queries'
import { apiGet } from '@/lib/api'

import type { ProfilesResponse } from '../../../../shared/api-types'

// 画面上部に1行で出す操作の結果。プロファイル画面の部品（中身・試聴・作成）が共有する
export type ProfileStatus = { message: string; isError: boolean }

export function profilesQueryOptions() {
  return queryOptions({
    queryKey: ['profiles'] as const,
    queryFn: () => apiGet<ProfilesResponse>('/profiles'),
  })
}

// caption の履歴はプロファイルの下のキーに置く。採用・復元で ['profiles'] を取り直せば含まれる
export function captionHistorySource(profileId: string): HistorySource {
  return {
    path: `/profiles/${encodeURIComponent(profileId)}/caption`,
    key: ['profiles', profileId, 'caption'],
  }
}

// プロファイルを変えると /health の profile も変わる
export async function invalidateProfiles(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['profiles'] }),
    queryClient.invalidateQueries({ queryKey: ['health'] }),
  ])
}
