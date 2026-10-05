import { createRoute } from '@tanstack/react-router'

import { PromptEditor } from '@/features/prompt/PromptEditor'

import { Route as rootRoute } from './__root'

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: '/prompt',
  component: PromptEditor,
})
