import { useSuspenseQuery } from '@tanstack/react-query'
import { createRoute } from '@tanstack/react-router'
import { Ellipsis } from 'lucide-react'
import { useState } from 'react'

import { EmptyState } from '@/components/EmptyState'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Kbd } from '@/components/ui/kbd'
import { AddToDict } from '@/features/review/AddToDict'
import { formatWhen } from '@/features/review/formatWhen'
import { corpusQueryOptions } from '@/features/review/queries'
import {
  type Filter,
  bulkArchiveTargets,
  computeReviewCounts,
  filterReviews,
  isFilter,
} from '@/features/review/reviewCounts'
import { ReviewRow, summaryElementId } from '@/features/review/ReviewRow'
import {
  REVIEW_PAGE_SIZE,
  type ReviewSearch,
  readReviewSearch,
  setFilter,
  setQuery,
  showMore,
} from '@/features/review/searchState'
import { nextSelection, selectionAfterRemoval } from '@/features/review/selection'
import { usePlayer } from '@/features/review/usePlayer'
import { BULK_ERROR_ID, useReviewActions } from '@/features/review/useReviewActions'
import type { CorpusItem } from '@/lib/api-types'
import { useHotkeys } from '@/lib/hotkeys'

import { Route as rootRoute } from './__root'

export const Route = createRoute({
  getParentRoute: () => rootRoute,
  path: '/review',
  validateSearch: (search: Record<string, unknown>): ReviewSearch => ({
    filter: isFilter(search.filter) ? search.filter : undefined,
    q: typeof search.q === 'string' && search.q !== '' ? search.q : undefined,
    visible: typeof search.visible === 'number' ? search.visible : undefined,
  }),
  component: ReviewPage,
})

const FILTER_ORDER: readonly Filter[] = ['unreviewed', 'bad', 'all', 'archived', 'stale']
const FILTER_LABELS: Record<Filter, string> = {
  unreviewed: '未レビュー',
  bad: '👎',
  all: 'すべて',
  archived: 'アーカイブ',
  stale: '旧プロンプト',
}

function ReviewPage() {
  const { data } = useSuspenseQuery(corpusQueryOptions())
  const search = readReviewSearch(Route.useSearch())
  const navigate = Route.useNavigate()
  const actions = useReviewActions()
  const { audioRef, error: playerError, play, handleAudioError } = usePlayer()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(true)
  const [confirmBulk, setConfirmBulk] = useState(false)

  // 件数・一括アーカイブの対象は全件が母集団。絞り込み済みの配列から数えない
  const counts = computeReviewCounts(data.items)
  const filtered = filterReviews(data.items, search.filter, search.q)
  const bulkTargets = confirmBulk ? bulkArchiveTargets(data.items) : []
  const shown = filtered.slice(0, search.visible)
  const ids = shown.map((r) => r.message_id)
  const selected = shown.find((r) => r.message_id === selectedId) ?? null

  // 操作で行が今の絞り込みから外れるなら、次の行へ選択を移す。「未レビュー」で
  // 1 を押し続けて順に片付けられるように。キーボードとクリックの両方がここを通る
  function follow(item: CorpusItem, after: CorpusItem | null) {
    if (!after || item.message_id !== selected?.message_id) return
    if (filterReviews([after], search.filter, search.q).length === 0) {
      setSelectedId(selectionAfterRemoval(ids, item.message_id))
    }
  }

  function vote(item: CorpusItem, clicked: 'good' | 'bad') {
    follow(item, actions.vote(item, clicked))
  }

  function archive(item: CorpusItem) {
    follow(item, actions.toggleArchive(item))
  }

  function move(delta: 1 | -1) {
    const next = nextSelection(ids, selected?.message_id ?? null, delta)
    if (next === null) return false
    setSelectedId(next)
    return true
  }

  // 選択中の行が無ければ false を返し、キーをブラウザに返す
  function onSelected(fn: (item: CorpusItem) => void) {
    return () => {
      if (!selected) return false
      fn(selected)
      return true
    }
  }

  useHotkeys({
    j: () => move(1),
    k: () => move(-1),
    enter: onSelected(() => setExpanded((v) => !v)),
    space: () => {
      if (!selected?.audio_path) return false
      play(selected.message_id)
      return true
    },
    '1': onSelected((item) => vote(item, 'good')),
    '2': onSelected((item) => vote(item, 'bad')),
    e: onSelected(archive),
  })

  const hint =
    data.prompt_changed_at === null
      ? undefined
      : `現行プロンプトは ${formatWhen(data.prompt_changed_at)} から。それ以前の ${counts.stale} 件は「旧プロンプト」にあり、レビューできません（いまのプロンプトはもうその出力を出さないため）。`
  const emptyMessage =
    search.filter !== 'archived' && search.filter !== 'stale' && counts.stale > 0 && !search.q
      ? `現行プロンプトでの読み上げはまだありません。差し替え前の ${counts.stale} 件は「旧プロンプト」にあります。`
      : '該当する読み上げがありません。'
  const bulkError = actions.errors[BULK_ERROR_ID] ?? ''

  return (
    <div>
      <PageHeader
        title="レビュー"
        description={hint}
        actions={
          <>
            <Input
              type="search"
              aria-label="テキスト検索"
              placeholder="検索"
              className="h-8 w-48"
              value={search.q}
              onChange={(e) =>
                navigate({ search: (prev) => setQuery(prev, e.target.value), replace: true })
              }
            />
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    aria-label="その他の操作"
                  />
                }
              >
                <Ellipsis className="size-4" aria-hidden="true" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  disabled={counts.reviewedUnarchived === 0}
                  onClick={() => setConfirmBulk(true)}
                >
                  レビュー済みを一括アーカイブ
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        }
      />
      <div className="mb-3 flex flex-wrap gap-1">
        {FILTER_ORDER.map((f) => (
          <Button
            key={f}
            variant={search.filter === f ? 'secondary' : 'ghost'}
            size="sm"
            aria-pressed={search.filter === f}
            onClick={() => navigate({ search: (prev) => setFilter(prev, f), replace: true })}
          >
            {FILTER_LABELS[f]}
            <span className="text-muted-foreground tabular-nums">{counts[f]}</span>
          </Button>
        ))}
      </div>
      {confirmBulk && (
        <div className="mb-3 flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2 text-sm">
          <span className="flex-1">レビュー済みの {bulkTargets.length} 件をアーカイブしますか</span>
          <Button
            size="sm"
            onClick={() => {
              actions.bulkArchive(bulkTargets)
              setConfirmBulk(false)
            }}
          >
            アーカイブ
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirmBulk(false)}>
            やめる
          </Button>
        </div>
      )}
      {bulkError && <p className="mb-2 text-xs text-destructive">{bulkError}</p>}
      {playerError && <p className="mb-2 text-xs text-destructive">{playerError}</p>}
      {shown.length === 0 ? (
        <EmptyState>{emptyMessage}</EmptyState>
      ) : (
        <ul className="overflow-hidden rounded-lg border bg-card">
          {shown.map((item) => (
            <ReviewRow
              key={item.message_id}
              item={item}
              selected={item.message_id === selected?.message_id}
              expanded={expanded}
              pending={actions.pending}
              error={actions.errors[item.message_id] ?? ''}
              onSelect={() => {
                setSelectedId(item.message_id)
                setExpanded(true)
              }}
              onPlay={() => play(item.message_id)}
              onVote={(clicked) => vote(item, clicked)}
              onToggleArchive={() => archive(item)}
              onSaveIdeal={(text) => actions.saveIdeal(item, text)}
              extra={
                <AddToDict summary={item.summary} summaryId={summaryElementId(item.message_id)} />
              }
            />
          ))}
        </ul>
      )}
      {filtered.length > shown.length && (
        <Button
          variant="ghost"
          className="mt-2 w-full"
          onClick={() => navigate({ search: (prev) => showMore(prev), replace: true })}
        >
          さらに {Math.min(REVIEW_PAGE_SIZE, filtered.length - shown.length)} 件を表示
        </Button>
      )}
      <p className="mt-4 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>
          <Kbd>j</Kbd> <Kbd>k</Kbd> 移動
        </span>
        <span>
          <Kbd>Space</Kbd> 再生
        </span>
        <span>
          <Kbd>1</Kbd> <Kbd>2</Kbd> 👍 👎
        </span>
        <span>
          <Kbd>e</Kbd> アーカイブ
        </span>
        <span>
          <Kbd>Enter</Kbd> 開閉
        </span>
      </p>
      <audio ref={audioRef} hidden onError={handleAudioError} />
    </div>
  )
}
