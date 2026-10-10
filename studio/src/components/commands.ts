export type Command = {
  id: string
  label: string
  group: string
  // 画面を移すコマンド。閉じたパレットがフォーカスを元の要素へ戻さないようにする
  navigates?: boolean
  run: () => void
}

export function filterCommands(commands: readonly Command[], query: string): Command[] {
  const q = query.trim().toLowerCase()
  if (!q) return [...commands]
  return commands.filter((c) => `${c.group} ${c.label}`.toLowerCase().includes(q))
}
