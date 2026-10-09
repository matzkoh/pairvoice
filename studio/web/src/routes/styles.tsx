import { createRoute } from '@tanstack/react-router'

import { StylesPage } from '@/features/styles/StylesPage'

import { Route as rootRoute } from './__root'

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: '/styles',
  component: StylesPage,
})
