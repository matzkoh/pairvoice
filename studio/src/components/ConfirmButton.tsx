import { type ComponentProps, type ReactNode, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'

const ARM_TIMEOUT_MS = 5000

type Props = Omit<ComponentProps<typeof Button>, 'onClick' | 'children'> & {
  idle: ReactNode
  armed: ReactNode
  onConfirm: () => void
}

// 取り消しにくい操作を1クリックで効かせない。ネイティブの confirm() はブラウザ操作を
// 止めてしまうので使わず、ボタン自身の文言を切り替えて2回目の押下で確定させる。
export function ConfirmButton({
  idle,
  armed,
  onConfirm,
  variant = 'outline',
  size = 'sm',
  onBlur,
  ...rest
}: Props) {
  const [isArmed, setIsArmed] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  function reset() {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    setIsArmed(false)
  }

  function handleClick() {
    if (isArmed) {
      reset()
      onConfirm()
      return
    }
    setIsArmed(true)
    timer.current = setTimeout(reset, ARM_TIMEOUT_MS)
  }

  return (
    <Button
      {...rest}
      variant={isArmed ? 'destructive' : variant}
      size={size}
      onClick={handleClick}
      onBlur={(e) => {
        reset()
        onBlur?.(e)
      }}
    >
      {isArmed ? armed : idle}
    </Button>
  )
}
