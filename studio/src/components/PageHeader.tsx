import type { ReactNode } from 'react'

type Props = { title: string; description?: ReactNode; actions?: ReactNode }

export function PageHeader({ title, description, actions }: Props) {
  return (
    <header className="mb-5 flex items-start gap-4">
      <div className="min-w-0 flex-1">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-none items-center gap-2">{actions}</div>}
    </header>
  )
}

export function SectionHeading({ children }: { children: ReactNode }) {
  return <h2 className="mt-8 mb-2 text-xs font-medium text-muted-foreground">{children}</h2>
}
