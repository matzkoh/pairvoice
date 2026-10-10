import { queryOptions } from '@tanstack/react-query'

import { apiGet } from '@/lib/api'
import type { DictResponse } from '@/lib/api-types'

export function dictQueryOptions() {
  return queryOptions({
    queryKey: ['dict'] as const,
    queryFn: () => apiGet<DictResponse>('/dict'),
  })
}
