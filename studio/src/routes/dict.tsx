import { createRoute } from '@tanstack/react-router'

import { DictTable } from '@/features/dict/DictTable'

import { Route as rootRoute } from './__root'

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: '/dict',
  component: DictTable,
})
