import { createRoute } from '@tanstack/react-router'

import { ProfilesPage } from '@/features/profiles/ProfilesPage'

import { Route as rootRoute } from './__root'

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: '/profiles',
  validateSearch: (search: Record<string, unknown>): { id?: string } => ({
    id: typeof search.id === 'string' ? search.id : undefined,
  }),
  component: ProfilesPage,
})
