import { createRoute } from '@tanstack/react-router'

import { ProfilesPage } from '@/features/profiles/ProfilesPage'

import { Route as rootRoute } from './__root'

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: '/profiles',
  // curate は「素材から作る」で開く解析の ID（API の studio_url で渡される）
  validateSearch: (search: Record<string, unknown>): { id?: string; curate?: string } => ({
    id: typeof search.id === 'string' ? search.id : undefined,
    curate: typeof search.curate === 'string' ? search.curate : undefined,
  }),
  component: ProfilesPage,
})
