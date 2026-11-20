import { Link } from '@tanstack/react-router'
import { Suspense } from 'react'

import { StatusIndicator } from '@/features/health/StatusIndicator'
import { useUnreviewedCount } from '@/features/review/queries'

import { CommandMenu } from './CommandMenu'
import { NAV_GROUPS } from './nav'
import { PLAYER_SLOT_ID } from './PlayerBar'

// suspense にすると corpus の全件取得が終わるまでヘッダーごと止まるので、
// 通常の useQuery で読み、読めるまでは何も出さない
function UnreviewedBadge() {
  const count = useUnreviewedCount()
  if (!count) return null
  return (
    <span
      className="rounded-full bg-mark px-1.5 text-[11px] leading-[18px] font-medium text-mark-foreground"
      aria-label={`未レビュー ${count} 件`}
    >
      {count}
    </span>
  )
}

// 画面の上の帯。横に置くと項目5つのために画面の幅を取り、一覧と中身を並べる画面
// （プロファイル）が、端末の隣に置いた半分幅の窓に収まらない
export function AppHeader() {
  return (
    <header className="flex h-12 flex-none items-center gap-2 border-b bg-card px-4 text-sm">
      <span className="font-semibold tracking-tight">pairvoice</span>
      <nav aria-label="メイン" className="flex flex-none items-center self-stretch">
        {NAV_GROUPS.map((group) => (
          <div
            key={group.label}
            role="group"
            aria-label={group.label}
            className="ml-3 flex items-center self-stretch border-l pl-3 first:border-l-0 first:pl-1 max-md:ml-1 max-md:pl-1"
          >
            <span
              aria-hidden="true"
              className="mr-1.5 text-[11px] text-muted-foreground max-lg:hidden"
            >
              {group.label}
            </span>
            {group.items.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className="relative flex items-center gap-1.5 self-stretch px-2.5 whitespace-nowrap text-muted-foreground hover:text-foreground"
                activeProps={{
                  className:
                    'font-medium text-foreground after:absolute after:inset-x-2.5 after:bottom-0 after:h-0.5 after:bg-foreground',
                }}
              >
                {item.label}
                {'badge' in item && <UnreviewedBadge />}
              </Link>
            ))}
          </div>
        ))}
      </nav>
      {/* 左に寄せる。中央に置くと、右の状態の文字の長さが変わるたびにコントローラーが動く */}
      <div id={PLAYER_SLOT_ID} className="flex min-w-0 flex-1 pl-3" />
      {/* 幅が足りないときは、移動の項目ではなく状態の文字のほうを詰める */}
      <div className="flex min-w-0 flex-none items-center justify-end gap-1 max-lg:shrink">
        <Suspense fallback={null}>
          <StatusIndicator />
        </Suspense>
        <CommandMenu />
      </div>
    </header>
  )
}
