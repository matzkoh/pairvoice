import { queryOptions } from '@tanstack/react-query'

import { apiGet, UnreachableServerError } from '@/lib/api'
import type { PairvoiceHealth } from '@/lib/api-types'

// 5秒ごとに取り直す。モデルの読み込みが数秒で画面に出るようにするため。
// 画面は pairvoice が配っているので、繋がらないのは pairvoice が止まった（再起動中を含む）とき。
// 例外にせず null（停止）として返し、画面を消さずに止まっていることを出す
export function healthQueryOptions() {
  return queryOptions({
    queryKey: ['health'] as const,
    queryFn: async (): Promise<PairvoiceHealth | null> => {
      try {
        return await apiGet<PairvoiceHealth>('/health')
      } catch (err) {
        if (err instanceof UnreachableServerError) return null
        throw err
      }
    },
    refetchInterval: 5000,
  })
}
