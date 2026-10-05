import { createRoute, createRouter, redirect } from '@tanstack/react-router'

import { Route as rootRoute } from './routes/__root'
import { Route as dictRoute } from './routes/dict'
import { Route as profilesRoute } from './routes/profiles'
import { Route as promptRoute } from './routes/prompt'
import { Route as reviewRoute } from './routes/review'

// いちばん使う画面なので、ここを入口にする
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/review' })
  },
})

// 声・caption・プレイグラウンドはプロファイルの画面に統合した。ブックマークを生かすため転送する
const legacyRedirects = ['/voice', '/caption', '/playground'].map((path) =>
  createRoute({
    getParentRoute: () => rootRoute,
    path,
    beforeLoad: () => {
      throw redirect({ to: '/profiles', search: {} })
    },
  }),
)

export const routeTree = rootRoute.addChildren([
  indexRoute,
  reviewRoute,
  promptRoute,
  dictRoute,
  profilesRoute,
  ...legacyRedirects,
])

export const router = createRouter({
  routeTree,
  // ローディング表示は main.tsx の <Suspense fallback> ではなくここに渡さないと出ない。
  // Router は Matches の内側でマッチツリー全体を自前の
  // <Suspense fallback={pendingElement}> で包んでおり（@tanstack/react-router の
  // Matches.tsx）、pendingElement は pendingComponent / defaultPendingComponent が
  // 無ければ null になる。つまり内側の境界が先に suspend を受け止めて空を描くので、
  // 外側の fallback には到達しない。main.tsx 側は router の外で起きる suspend の
  // 受け皿として残してあり、二重定義ではない。文言は両者で揃えている。
  defaultPendingComponent: () => <p className="p-6 text-muted-foreground">読み込み中…</p>,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
