import { Link } from '@tanstack/react-router'
import { Suspense } from 'react'

import { StatusFooter } from '@/features/health/StatusFooter'
import { useUnreviewedCount } from '@/features/review/queries'

import { NAV_GROUPS } from './nav'

// suspense にすると corpus の全件取得が終わるまでサイドバーごと止まるので、
// 通常の useQuery で読み、読めるまでは何も出さない
function UnreviewedBadge() {
  const count = useUnreviewedCount()
  if (!count) return null
  return (
    <span
      className="rounded-full bg-primary px-1.5 text-[11px] leading-5 text-primary-foreground tabular-nums"
      aria-label={`未レビュー ${count} 件`}
    >
      {count}
    </span>
  )
}

export function AppSidebar() {
  return (
    <nav
      aria-label="メイン"
      className="flex w-52 flex-none flex-col border-r border-sidebar-border bg-sidebar px-2 py-3 text-sm text-sidebar-foreground"
    >
      <div className="mb-4 px-2 text-[13px] font-semibold">
        pairvoice <span className="font-normal text-muted-foreground">studio</span>
      </div>
      {NAV_GROUPS.map((group) => (
        <div key={group.label} role="group" aria-label={group.label} className="mb-3">
          <div className="px-2 pb-1 text-[11px] text-muted-foreground">{group.label}</div>
          {group.items.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              activeProps={{
                className: 'bg-sidebar-accent font-medium text-sidebar-accent-foreground',
              }}
            >
              <item.icon className="size-4 flex-none" aria-hidden="true" />
              <span className="flex-1">{item.label}</span>
              {'badge' in item && <UnreviewedBadge />}
            </Link>
          ))}
        </div>
      ))}
      <div className="mt-auto">
        <Suspense fallback={null}>
          <StatusFooter />
        </Suspense>
      </div>
    </nav>
  )
}
