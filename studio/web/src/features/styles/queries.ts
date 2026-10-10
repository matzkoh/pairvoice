import { type QueryClient, queryOptions } from '@tanstack/react-query'

import { apiGet } from '@/lib/api'
import type { StylesResponse } from '@/lib/api-types'

export function stylesQueryOptions() {
  return queryOptions({
    queryKey: ['styles'] as const,
    queryFn: () => apiGet<StylesResponse>('/styles'),
  })
}

export async function invalidateStyles(queryClient: QueryClient) {
  await queryClient.invalidateQueries({ queryKey: stylesQueryOptions().queryKey })
}
