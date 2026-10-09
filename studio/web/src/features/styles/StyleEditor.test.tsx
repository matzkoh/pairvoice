/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'

import { StyleEditor } from './StyleEditor'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

beforeEach(() => {
  vi.mocked(apiSend).mockImplementation(async (path: string) => {
    if (path === '/api/speak') return { relative_path: 'generations/x.wav', duration: 1.5 }
    return { ok: true }
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

type Props = ComponentProps<typeof StyleEditor>

const WHISPER = { name: 'ささやき', caption: 'ささやく。', sampler: { duration_scale: 1.2 } }
const BASE: Props = {
  style: WHISPER,
  styles: [WHISPER, { name: 'ゆっくり', caption: null, sampler: {} }],
  profiles: {
    active: 'p-a',
    items: [
      { id: 'p-a', name: '声A', caption: '', source: 'design', created_at: '' },
      { id: 'p-b', name: '声B', caption: '', source: 'design', created_at: '' },
    ],
  },
  baseSampler: { cfg_scale_speaker: 3 },
  pairvoiceDown: false,
  onPlay: vi.fn(),
  onStatus: vi.fn(),
  onSaved: vi.fn(),
  onDeleted: vi.fn(),
}

function renderEditor(props: Partial<Props> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <StyleEditor {...BASE} {...props} />
    </QueryClientProvider>,
  )
}

function putBody() {
  const call = vi.mocked(apiSend).mock.calls.find(([path]) => path === '/api/styles')
  return call?.[2]
}

it('保存は同じ名前の1件だけを差し替え、config.toml と違う値の項目だけを書く', async () => {
  renderEditor()

  fireEvent.change(screen.getByLabelText('caption'), { target: { value: '小声で。' } })
  fireEvent.click(screen.getByRole('button', { name: '保存' }))

  await waitFor(() => expect(BASE.onSaved).toHaveBeenCalledWith('ささやき'))
  expect(putBody()).toEqual({
    styles: [
      { name: 'ささやき', caption: '小声で。', sampler: { duration_scale: 1.2 } },
      { name: 'ゆっくり', caption: null, sampler: {} },
    ],
  })
})

it('新しいスタイルはプロファイルの caption のまま読む。caption を null で保存し、試聴でも送らない', async () => {
  renderEditor({ style: null })

  fireEvent.change(screen.getByLabelText(/^名前/), { target: { value: '速く' } })
  fireEvent.change(screen.getByLabelText('声'), { target: { value: 'p-b' } })
  fireEvent.click(screen.getByRole('button', { name: /試聴/ }))

  await waitFor(() => expect(BASE.onPlay).toHaveBeenCalled())
  const speak = vi.mocked(apiSend).mock.calls.find(([path]) => path === '/api/speak')?.[2]
  expect(speak).toMatchObject({ profile_id: 'p-b' })
  expect(speak).not.toHaveProperty('caption')

  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(BASE.onSaved).toHaveBeenCalledWith('速く'))
  expect(putBody()).toEqual({
    styles: [...BASE.styles, { name: '速く', caption: null, sampler: {} }],
  })
})

it('ほかのスタイルと同じ名前では保存できない', () => {
  renderEditor()

  fireEvent.change(screen.getByLabelText(/^名前/), { target: { value: 'ゆっくり' } })

  expect(screen.getByText('同じ名前のスタイルがあります')).toBeTruthy()
  expect(screen.getByRole('button', { name: '保存' })).toHaveProperty('disabled', true)
})
