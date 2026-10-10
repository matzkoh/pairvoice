import { useQueryErrorResetBoundary } from '@tanstack/react-query'
import { type ErrorComponentProps, Outlet, createRootRoute } from '@tanstack/react-router'

import { AppSidebar } from '@/components/AppSidebar'
import { CommandMenu } from '@/components/CommandMenu'
import { Button } from '@/components/ui/button'
import { toErrorMessage } from '@/lib/api'

export const Route = createRootRoute({
  component: RootLayout,
  // 想定外の例外はここを唯一の受け皿にする。個々の画面に書き忘れる余地を作らない
  errorComponent: RootError,
})

function RootError({ error, reset }: ErrorComponentProps) {
  const queryErrorResetBoundary = useQueryErrorResetBoundary()
  return (
    <div className="p-8">
      <p className="text-sm text-destructive">エラーが発生しました: {toErrorMessage(error)}</p>
      <Button
        variant="outline"
        size="sm"
        className="mt-3"
        onClick={() => {
          // router の reset() だけでは、キャッシュに残ったエラーのクエリが再マウント時に
          // 同じエラーを投げ直す。先に query 側の境界を reset して取り直させる
          queryErrorResetBoundary.reset()
          reset()
        }}
      >
        やり直す
      </Button>
    </div>
  )
}

function RootLayout() {
  return (
    <div className="flex h-dvh overflow-hidden">
      <AppSidebar />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-8 pt-6 pb-16">
          <Outlet />
        </div>
      </main>
      <CommandMenu />
    </div>
  )
}
