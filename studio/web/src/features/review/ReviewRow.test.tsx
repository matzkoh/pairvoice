/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import type { CorpusItem } from '@/lib/api-types'

import { ReviewRow, summaryElementId } from './ReviewRow'

afterEach(cleanup)

function item(overrides: Partial<CorpusItem>): CorpusItem {
  return {
    ts: '2026-09-29 10:00:00',
    message_id: 'm1',
    input: '生成元の固有テキスト',
    summary: '要約テキスト',
    audio_path: 'x.wav',
    verdict: null,
    ideal: null,
    archived: false,
    stale: false,
    ...overrides,
  }
}

type Extra = { selected?: boolean; expanded?: boolean; error?: string }

function renderRow(row: CorpusItem, props: Extra = {}) {
  const handlers = {
    onSelect: vi.fn(),
    onPlay: vi.fn(),
    onVote: vi.fn(),
    onToggleArchive: vi.fn(),
    onSaveIdeal: vi.fn(),
  }
  render(
    <ul>
      <ReviewRow
        item={row}
        selected={props.selected ?? false}
        expanded={props.expanded ?? false}
        pending={false}
        error={props.error ?? ''}
        {...handlers}
        extra={<button type="button">差し込み</button>}
      />
    </ul>,
  )
  return handlers
}

it('閉じた行は1行で、生成元テキストは出さない', () => {
  renderRow(item({}))
  expect(screen.getByText('要約テキスト')).toBeTruthy()
  expect(screen.queryByText('生成元の固有テキスト')).toBeNull()
  expect(screen.queryByRole('button', { name: '差し込み' })).toBeNull()
})

it('行を押すと選択し、再生ボタンは選択せずに再生だけする', () => {
  const { onSelect, onPlay } = renderRow(item({}))
  fireEvent.click(screen.getByText('要約テキスト'))
  expect(onSelect).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: '再生' }))
  expect(onPlay).toHaveBeenCalledTimes(1)
  expect(onSelect).toHaveBeenCalledTimes(1)
})

it('音声が無い行は再生できない', () => {
  renderRow(item({ audio_path: undefined }))
  expect(screen.getByRole('button', { name: '再生' })).toHaveProperty('disabled', true)
})

it('選択中で展開していれば、評価・アーカイブ・生成元テキストを出す', () => {
  const { onVote, onToggleArchive } = renderRow(item({}), { selected: true, expanded: true })
  expect(screen.getByText('生成元の固有テキスト')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '良い' }))
  expect(onVote).toHaveBeenCalledWith('good')
  fireEvent.click(screen.getByRole('button', { name: '悪い' }))
  expect(onVote).toHaveBeenCalledWith('bad')
  fireEvent.click(screen.getByRole('button', { name: /アーカイブ/ }))
  expect(onToggleArchive).toHaveBeenCalledTimes(1)
})

it('展開中の操作ボタンを押しても行の選択（onSelect）は呼ばない', () => {
  const { onSelect } = renderRow(item({}), { selected: true, expanded: true })
  fireEvent.click(screen.getByRole('button', { name: '良い' }))
  expect(onSelect).not.toHaveBeenCalled()
})

it('👎 の行だけ理想の出力欄を出し、フォーカスを外すと保存する', () => {
  const { onSaveIdeal } = renderRow(item({ verdict: 'bad' }), { selected: true, expanded: true })
  const input = screen.getByLabelText('理想の出力')
  fireEvent.change(input, { target: { value: 'こう読んで' } })
  fireEvent.blur(input)
  expect(onSaveIdeal).toHaveBeenCalledWith('こう読んで')
})

it('旧プロンプトの行は評価ボタンを押せない', () => {
  renderRow(item({ stale: true }), { selected: true, expanded: true })
  expect(screen.getByRole('button', { name: '良い' })).toHaveProperty('disabled', true)
})

it('差し込み口は展開時の操作列に出し、要約の要素は message_id から引ける', () => {
  renderRow(item({}), { selected: true, expanded: true })
  expect(screen.getByRole('button', { name: '差し込み' })).toBeTruthy()
  expect(document.getElementById(summaryElementId('m1'))?.textContent).toBe('要約テキスト')
})

it('行のエラーは閉じていても出す', () => {
  renderRow(item({}), { error: '判定の投稿に失敗しました: boom' })
  expect(screen.getByText('判定の投稿に失敗しました: boom')).toBeTruthy()
})

it('選ばれた行は aria-current を持ち、画面内へスクロールする', () => {
  const scrollIntoView = vi.fn()
  Element.prototype.scrollIntoView = scrollIntoView
  try {
    renderRow(item({}), { selected: true })
    const row = screen.getByRole('listitem')
    expect(row.getAttribute('aria-current')).toBe('true')
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
  } finally {
    // @ts-expect-error jsdom には無いので元に戻す
    delete Element.prototype.scrollIntoView
  }
})

it('マウスで押した判定ボタンからはフォーカスを外す（Space で押し直さないように）', () => {
  const { onVote } = renderRow(item({}), { selected: true, expanded: true })
  const good = screen.getByRole('button', { name: '良い' })
  good.focus()
  fireEvent.click(good, { detail: 1 })
  expect(onVote).toHaveBeenCalledWith('good')
  expect(document.activeElement).not.toBe(good)
})

it('キーボードで押した判定ボタンにはフォーカスを残す', () => {
  renderRow(item({}), { selected: true, expanded: true })
  const good = screen.getByRole('button', { name: '良い' })
  good.focus()
  fireEvent.click(good, { detail: 0 })
  expect(document.activeElement).toBe(good)
})

it('理想の出力は Enter で保存するが、IME の変換を確定する Enter では保存しない', () => {
  const { onSaveIdeal } = renderRow(item({ verdict: 'bad' }), { selected: true, expanded: true })
  const input = screen.getByLabelText('理想の出力')
  fireEvent.change(input, { target: { value: 'こう読んで' } })
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
  fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })
  expect(onSaveIdeal).not.toHaveBeenCalled()
  fireEvent.keyDown(input, { key: 'Enter' })
  expect(onSaveIdeal).toHaveBeenCalledWith('こう読んで')
})

it('マウスで押しても差し込み部品からはフォーカスを外さない', () => {
  renderRow(item({}), { selected: true, expanded: true })
  const extra = screen.getByRole('button', { name: '差し込み' })
  extra.focus()
  fireEvent.click(extra, { detail: 1 })
  expect(document.activeElement).toBe(extra)
})

it('理想の出力を送れなかったときは、先の投稿が返っても打った内容を残す', () => {
  const onSaveIdeal = vi.fn((text: string) => text === 'A')
  const props = {
    selected: true,
    expanded: true,
    pending: false,
    error: '',
    onSelect: vi.fn(),
    onPlay: vi.fn(),
    onVote: vi.fn(),
    onToggleArchive: vi.fn(),
    onSaveIdeal,
  }
  const { rerender } = render(
    <ul>
      <ReviewRow item={item({ verdict: 'bad', ideal: '' })} {...props} />
    </ul>,
  )
  const input = screen.getByLabelText('理想の出力')
  fireEvent.change(input, { target: { value: 'A' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  // 'A' の投稿が返る前に打ち足して保存しようとしたが、送られなかった
  fireEvent.change(input, { target: { value: 'AB' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  expect(onSaveIdeal).toHaveBeenLastCalledWith('AB')
  // 'A' の投稿が返った
  rerender(
    <ul>
      <ReviewRow item={item({ verdict: 'bad', ideal: 'A' })} {...props} />
    </ul>,
  )
  expect(screen.getByLabelText<HTMLInputElement>('理想の出力').value).toBe('AB')
})
