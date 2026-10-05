import { queryOptions } from '@tanstack/react-query'

import { apiGet } from '@/lib/api'

import type { StudioHealth } from '../../../../shared/api-types'

// 5秒ごとに取り直す。モデルの読み込みが数秒で画面に出るようにするため。
export function healthQueryOptions() {
  return queryOptions({
    queryKey: ['health'] as const,
    queryFn: () => apiGet<StudioHealth>('/api/health'),
    refetchInterval: 5000,
  })
}
