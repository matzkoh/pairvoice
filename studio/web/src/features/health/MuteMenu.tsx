import { BellOff } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

import { MUTE_CHOICES, useMute } from './useMute'

// 誤クリック1回で読み上げが止まらないよう、押してもメニューが開くだけにする
export function MuteMenu({ disabled }: { disabled: boolean }) {
  const { pending, error, choose } = useMute()
  return (
    <div className="flex items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled || pending}
              className="-ml-2 h-7 px-2 text-muted-foreground"
            />
          }
        >
          <BellOff className="size-3.5" aria-hidden="true" />
          ミュート
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          {MUTE_CHOICES.map((choice) => (
            <DropdownMenuItem key={choice.label} onClick={() => choose(choice.minutes)}>
              {choice.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {error && (
        <span
          className="text-destructive"
          title={error}
          role="img"
          aria-label={`ミュートの操作に失敗しました: ${error}`}
        >
          ⚠
        </span>
      )}
    </div>
  )
}
