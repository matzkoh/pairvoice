import { createRoute } from '@tanstack/react-router'

import { ProfilesPage } from '@/features/profiles/ProfilesPage'

import { Route as rootRoute } from './__root'

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: '/profiles',
  // fold は「素材から作る」で追っている畳み込みの仕事の ID
  validateSearch: (search: Record<string, unknown>): { id?: string; fold?: string } => ({
    id: typeof search.id === 'string' ? search.id : undefined,
    fold: typeof search.fold === 'string' ? search.fold : undefined,
  }),
  component: ProfilesPage,
})
