import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'

import './index.css'
import { router } from './router'

// 既定の staleTime を 0 のままにしておく。このツールの値は外（フック・pairvoice）
// から変わるので、タブを切り替えたら取り直すほうが正しい。
const queryClient = new QueryClient()

const root = document.getElementById('root')
if (!root) throw new Error('#root が無い')

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <Suspense fallback={<p className="p-6 text-muted-foreground">読み込み中…</p>}>
        <RouterProvider router={router} />
      </Suspense>
    </QueryClientProvider>
  </StrictMode>,
)
