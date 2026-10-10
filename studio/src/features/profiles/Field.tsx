import type { ReactNode } from 'react'

type Props = { label: string; htmlFor?: string; hint?: ReactNode; children: ReactNode }

// 作成フォームの1項目。プロファイルの中身（参照音声・caption・名前）を見出しで示す
export function Field({ label, htmlFor, hint, children }: Props) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-sm font-medium">
        {label}
      </label>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {children}
    </div>
  )
}
