import { type QueryClient, queryOptions } from '@tanstack/react-query'

import { apiGet } from '@/lib/api'

import type { StylesResponse } from '../../../../shared/api-types'

export function stylesQueryOptions() {
  return queryOptions({
    queryKey: ['styles'] as const,
    queryFn: () => apiGet<StylesResponse>('/api/styles'),
  })
}

export async function invalidateStyles(queryClient: QueryClient) {
  await queryClient.invalidateQueries({ queryKey: stylesQueryOptions().queryKey })
}
