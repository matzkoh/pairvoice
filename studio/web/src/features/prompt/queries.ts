import { queryOptions } from '@tanstack/react-query'

import { apiGet } from '@/lib/api'

import type { PromptResponse } from '../../../../shared/api-types'

export function promptQueryOptions() {
  return queryOptions({
    queryKey: ['prompt'] as const,
    queryFn: () => apiGet<PromptResponse>('/prompt'),
  })
}
