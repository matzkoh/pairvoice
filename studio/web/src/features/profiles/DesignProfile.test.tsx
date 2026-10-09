/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { apiSend } from '@/lib/api'

import { DesignProfile } from './DesignProfile'
import { REFERENCE_TEXTS } from './mixSynth'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, apiSend: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function renderDesign() {
  const onPlay = vi.fn()
  const onStatus = vi.fn()
  const onCreated = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DesignProfile
        initialCaption="やわらかい声。"
        anchorText="pairvoice が申告した文です。"
        pairvoiceDown={false}
        onPlay={onPlay}
        onStatus={onStatus}
        onCreated={onCreated}
      />
    </QueryClientProvider>,
  )
  return { onPlay, onStatus, onCreated }
}

it('候補は参照音声を使わない design で作り、選んだ候補の音声からプロファイルを作る', async () => {
  vi.mocked(apiSend).mockImplementation(async (path: string) =>
    path === '/api/speak'
      ? { relative_path: 'generations/candidate.wav', duration: 9.5 }
      : { id: 'p-new', name: '作った声' },
  )
  const { onPlay, onStatus, onCreated } = renderDesign()

  fireEvent.change(screen.getByRole('slider'), { target: { value: '1' } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '候補を作る' }))
  })

  const [path, method, body] = vi.mocked(apiSend).mock.calls[0]!
  expect([path, method]).toEqual(['/api/speak', 'POST'])
  expect(body).toMatchObject({
    text: 'pairvoice が申告した文です。',
    caption: 'やわらかい声。',
    design: true,
  })
  // 最初に鳴らせるようになった候補はすぐ鳴らす
  expect(onPlay).toHaveBeenCalledWith('/api/audio-file?path=generations%2Fcandidate.wav')

  // 名前の欄は、保存すると決めてから開く
  expect(screen.queryByRole('textbox', { name: '候補 #1 の名前' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '候補 #1 を保存' }))
  fireEvent.change(screen.getByRole('textbox', { name: '候補 #1 の名前' }), {
    target: { value: '作った声' },
  })
  // 変換を確定する Enter では作らない
  fireEvent.keyDown(screen.getByRole('textbox', { name: '候補 #1 の名前' }), {
    key: 'Enter',
    isComposing: true,
  })
  expect(vi.mocked(apiSend).mock.calls.some(([p]) => p === '/api/profiles')).toBe(false)
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'この声で作る' }))
  })

  // 選んだ候補の声のまま別の文も読ませ、候補と合わせて参照音声にする（つなぐのはサーバー）
  const extra = vi
    .mocked(apiSend)
    .mock.calls.slice(1, -1)
    .map(([, , sent]) => sent)
  expect(extra).toHaveLength(REFERENCE_TEXTS.length)
  extra.forEach((sent, i) =>
    expect(sent).toMatchObject({
      text: REFERENCE_TEXTS[i],
      caption: 'やわらかい声。',
      mix: [{ audio: 'generations/candidate.wav', weight: 1 }],
      sampler: { num_steps: 80 },
    }),
  )
  expect(apiSend).toHaveBeenLastCalledWith('/api/profiles', 'POST', {
    name: '作った声',
    caption: 'やわらかい声。',
    takes: Array.from({ length: REFERENCE_TEXTS.length + 1 }, () => 'generations/candidate.wav'),
  })
  expect(onStatus).toHaveBeenCalledWith(expect.objectContaining({ isError: false }))
  expect(screen.getByText('保存済み')).toBeTruthy()
  // 作ったプロファイルを開く
  expect(onCreated).toHaveBeenCalledWith('p-new')
})
