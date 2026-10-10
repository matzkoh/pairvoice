import { createRoute } from '@tanstack/react-router'

import { PromptEditor } from '@/features/prompt/PromptEditor'

import { Route as rootRoute } from './__root'

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: '/prompt',
  // 編集する対象（無ければ共通、tone は既定の口調）
  validateSearch: (search: Record<string, unknown>): { target?: string } => ({
    target: typeof search.target === 'string' ? search.target : undefined,
  }),
  component: PromptEditor,
})
