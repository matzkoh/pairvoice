import type { ReactNode } from 'react'

import { StatusText } from './StatusText'

type Props = {
  title: string
  description?: ReactNode
  actions?: ReactNode
  // 操作の結果の知らせ。見出しの横に置くので、出入りしても下がずれない（ずれると
  // 押そうとしたボタンの隣を押してしまう）
  status?: { message: string; isError: boolean } | null
}

export function PageHeader({ title, description, actions, status }: Props) {
  return (
    <header className="mb-4 flex flex-wrap items-start gap-x-6 gap-y-3">
      <div className="min-w-0 flex-1 basis-80">
        <h1 className="text-xl leading-tight font-semibold tracking-tight">{title}</h1>
        {description && (
          <p className="mt-1.5 max-w-[48em] text-[13px] text-muted-foreground">{description}</p>
        )}
      </div>
      {status !== undefined && (
        <div className="max-w-sm text-right" aria-live="polite">
          {status && <StatusText message={status.message} isError={status.isError} />}
        </div>
      )}
      {actions && <div className="flex flex-none items-center gap-2">{actions}</div>}
    </header>
  )
}

export function SectionHeading({ children }: { children: ReactNode }) {
  return <h2 className="mb-2 text-xs font-medium text-muted-foreground">{children}</h2>
}
