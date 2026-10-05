import { cn } from 'cn'
import { Play } from 'lucide-react'
import { type MouseEvent, type ReactNode, useEffect, useRef } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Kbd } from '@/components/ui/kbd'
import { isImeConfirm } from '@/lib/hotkeys'
import { useDraft } from '@/lib/useDraft'

import type { CorpusItem } from '../../../../shared/api-types'
import { formatWhen } from './formatWhen'

const STALE_TITLE = '旧プロンプトでの出力なので、評価しても現行の改善には使われません'

// 操作は画面側から受け取る。キーボードと同じ経路を通して、操作で行が絞り込みから
// 外れたときの選択の追従をクリックでも効かせるため
type Props = {
  item: CorpusItem
  selected: boolean
  expanded: boolean
  pending: boolean
  error: string
  onSelect: () => void
  onPlay: () => void
  onVote: (clicked: 'good' | 'bad') => void
  onToggleArchive: () => void
  // 送ったかどうかを返す（同じ行の判定の投稿中は送られない）
  onSaveIdeal: (text: string) => boolean
  // 展開時の操作列の末尾に差し込む部品
  extra?: ReactNode
}

// 差し込む部品が要約の中の選択を読めるように、要約の要素を message_id から引けるようにする
export function summaryElementId(messageId: string): string {
  return `summary-${messageId}`
}

export function ReviewRow({
  item,
  selected,
  expanded,
  pending,
  error,
  onSelect,
  onPlay,
  onVote,
  onToggleArchive,
  onSaveIdeal,
  extra,
}: Props) {
  const rowRef = useRef<HTMLLIElement>(null)
  const open = selected && expanded

  // j / k や投票で選択が画面外の行へ移っても、見失わないように
  useEffect(() => {
    if (selected) rowRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [selected])

  return (
    <li
      ref={rowRef}
      data-selected={selected || undefined}
      aria-current={selected || undefined}
      className={cn(
        'border-b last:border-b-0',
        selected && 'bg-primary/5 shadow-[inset_2px_0_0_var(--primary)]',
      )}
    >
      {/* キーボードでは j / k で選ぶ */}
      {/* oxlint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions */}
      <div className="flex cursor-default items-center gap-3 px-3 py-2" onClick={onSelect}>
        <Button
          variant="ghost"
          size="icon-sm"
          className="flex-none text-primary"
          aria-label="再生"
          title={item.audio_path ? '再生' : '音声ファイルなし'}
          disabled={!item.audio_path}
          onClick={(e) => {
            e.stopPropagation()
            onPlay()
          }}
        >
          <Play className="size-3.5" aria-hidden="true" />
        </Button>
        <span
          id={summaryElementId(item.message_id)}
          className={cn('min-w-0 flex-1 text-sm', !open && 'truncate')}
        >
          {item.summary}
        </span>
        {item.verdict && (
          <span
            className="flex-none text-xs"
            aria-label={item.verdict === 'good' ? '良い' : '悪い'}
          >
            {item.verdict === 'good' ? '👍' : '👎'}
          </span>
        )}
        <time
          className="flex-none text-xs text-muted-foreground tabular-nums"
          title={`message_id: ${item.message_id}`}
        >
          {formatWhen(item.ts)}
        </time>
      </div>
      {open && (
        <RowDetail
          item={item}
          pending={pending}
          error={error}
          onVote={onVote}
          onToggleArchive={onToggleArchive}
          onSaveIdeal={onSaveIdeal}
          extra={extra}
        />
      )}
      {!open && error && <p className="px-3 pb-2 pl-13 text-xs text-destructive">{error}</p>}
    </li>
  )
}

type DetailProps = Pick<
  Props,
  'item' | 'pending' | 'error' | 'onVote' | 'onToggleArchive' | 'onSaveIdeal'
> & { extra: ReactNode }

function RowDetail({
  item,
  pending,
  error,
  onVote,
  onToggleArchive,
  onSaveIdeal,
  extra,
}: DetailProps) {
  // 他の場所で理想の出力が変わったら入力欄を取り直す
  const { draft: idealText, setDraft: setIdealText, markSubmitted } = useDraft(item.ideal ?? '')
  function save() {
    // 送った値だけを「送った」と覚える。送っていない値を覚えると、変わっていなくて送らな
    // かったときは後で別の場所から同じ値に変わっても取り直さず、同じ行の判定の投稿中で
    // 送れなかったときは、その投稿が返ったところで打った内容を上書きする
    if (idealText === (item.ideal ?? '')) return
    if (onSaveIdeal(idealText)) markSubmitted(idealText)
  }
  return (
    <div className="space-y-3 px-3 pb-3 pl-13">
      <div className="flex flex-wrap items-center gap-2">
        {/* 差し込み部品（辞書に追加）は外す。ポップオーバーはトリガーのフォーカスが外れると閉じる */}
        <div className="contents" onClickCapture={releaseAfterClick}>
          <VerdictButton item={item} kind="good" pending={pending} onVote={onVote} />
          <VerdictButton item={item} kind="bad" pending={pending} onVote={onVote} />
          <Button variant="outline" size="sm" disabled={pending} onClick={onToggleArchive}>
            {item.archived ? 'アーカイブから戻す' : 'アーカイブ'} <Kbd>e</Kbd>
          </Button>
        </div>
        {extra}
      </div>
      {item.verdict === 'bad' && (
        <div>
          <label
            htmlFor={`ideal-${item.message_id}`}
            className="mb-1 block text-xs text-muted-foreground"
          >
            理想の出力
          </label>
          <Input
            id={`ideal-${item.message_id}`}
            value={idealText}
            onChange={(e) => setIdealText(e.target.value)}
            onBlur={save}
            // 画面を移るとアンマウントで blur が起きず入力が失われるので、Enter でも保存する。
            // 変わっていなければ送らないので二重送信にはならない
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !isImeConfirm(e.nativeEvent)) save()
            }}
            placeholder="こう読み上げてほしかった、という一文"
          />
        </div>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      <div>
        <div className="mb-1 text-xs text-muted-foreground">生成元テキスト</div>
        <div className="max-h-45 overflow-y-auto rounded-md bg-muted px-3 py-2 text-xs whitespace-pre-wrap text-muted-foreground">
          {item.input}
        </div>
      </div>
    </div>
  )
}

// マウスで押したボタンにフォーカスが残ると、再生のつもりの Space がそのボタンを押し直して
// 投票を取り消してしまう（hotkeys.ts はフォーカス中のボタンの上の Space / Enter をブラウザに任せる）。
// キーボードで押したとき（detail が 0）はフォーカスを動かさない
function releaseAfterClick(e: MouseEvent<HTMLElement>) {
  if (e.detail === 0 || !(e.target instanceof Element)) return
  const button = e.target.closest('button')
  if (button instanceof HTMLElement) button.blur()
}

type VerdictProps = Pick<Props, 'item' | 'pending' | 'onVote'> & { kind: 'good' | 'bad' }

function VerdictButton({ item, kind, pending, onVote }: VerdictProps) {
  const on = item.verdict === kind
  return (
    <Button
      variant="outline"
      size="sm"
      aria-label={kind === 'good' ? '良い' : '悪い'}
      aria-pressed={on}
      title={item.stale ? STALE_TITLE : undefined}
      disabled={item.stale || pending}
      onClick={() => onVote(kind)}
      className={cn(
        on && kind === 'good' && 'border-success/50 bg-success/10',
        on && kind === 'bad' && 'border-destructive/50 bg-destructive/10',
      )}
    >
      {kind === 'good' ? '👍' : '👎'} <Kbd>{kind === 'good' ? '1' : '2'}</Kbd>
    </Button>
  )
}
