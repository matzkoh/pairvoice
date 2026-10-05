/** @vitest-environment jsdom */
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { ApiError, StaleServerError, apiGetBlob } from '@/lib/api'

import { usePlayer } from './usePlayer'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return { ...actual, apiGetBlob: vi.fn() }
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// audio 要素の実ロード（HTMLMediaElement.play/src）は jsdom がサポートしないので、
// onError の発火だけをボタンから直接シミュレートする。判定したいのは
// 「404 かどうかで文言を分けるロジック」であって、ブラウザの音声再生そのものではない。
function Host() {
  const player = usePlayer()
  return (
    <div>
      <button type="button" onClick={() => player.play('m1')}>
        再生
      </button>
      <button type="button" onClick={() => player.play('m2')}>
        次を再生
      </button>
      <button type="button" onClick={() => player.handleAudioError()}>
        onError発火
      </button>
      {player.error && <p>{player.error}</p>}
    </div>
  )
}

it('音声が404のときは「音声ファイルが見つかりません」と出す（StaleServerError にしない）', async () => {
  vi.mocked(apiGetBlob).mockRejectedValue(new ApiError(404, '{"error":"not_found"}'))
  render(<Host />)

  await act(async () => {
    screen.getByRole('button', { name: '再生' }).click()
  })
  await act(async () => {
    screen.getByRole('button', { name: 'onError発火' }).click()
  })

  expect(apiGetBlob).toHaveBeenCalledWith('/api/audio/m1', { allowNotFound: true })
  expect(screen.getByText('音声ファイルが見つかりません')).toBeTruthy()
})

it('404 以外の失敗は一般のエラー文言を出す', async () => {
  vi.mocked(apiGetBlob).mockRejectedValue(new Error('network down'))
  render(<Host />)

  await act(async () => {
    screen.getByRole('button', { name: '再生' }).click()
  })
  await act(async () => {
    screen.getByRole('button', { name: 'onError発火' }).click()
  })

  expect(screen.getByText(/再生に失敗しました: network down/)).toBeTruthy()
})

it('StaleServerError（404の ApiError のサブクラス）が来ても status で判定し「見つかりません」になる', async () => {
  // usePlayer.ts の分岐は `err instanceof ApiError && err.status === 404` で、
  // StaleServerError かどうかは見ていない（サブクラスかどうかで文言を変える理由が
  // 無いため）。apiGetBlob をモックしている以上「allowNotFound を渡しているか」は
  // このテストでは検出できない（それを検出するのは1本目の toHaveBeenCalledWith
  // アサーション）。ここで固定したいのは、たとえ StaleServerError のインスタンスが
  // 渡ってきても status===404 の判定に合致して同じ文言になる、という判定条件自体。
  vi.mocked(apiGetBlob).mockRejectedValue(new StaleServerError('{"error":"not_found"}'))
  render(<Host />)

  await act(async () => {
    screen.getByRole('button', { name: '再生' }).click()
  })
  await act(async () => {
    screen.getByRole('button', { name: 'onError発火' }).click()
  })

  expect(screen.getByText('音声ファイルが見つかりません')).toBeTruthy()
})

it('前の再生の失敗判定が遅れて返っても、いまの再生にエラーを出さない', async () => {
  const pending = Promise.withResolvers<Blob>()
  vi.mocked(apiGetBlob).mockReturnValue(pending.promise)
  render(<Host />)

  await act(async () => {
    screen.getByRole('button', { name: '再生' }).click()
  })
  await act(async () => {
    screen.getByRole('button', { name: 'onError発火' }).click()
  })
  await act(async () => {
    screen.getByRole('button', { name: '次を再生' }).click()
  })
  await act(async () => pending.reject(new ApiError(404, '{"error":"not_found"}')))

  expect(screen.queryByText('音声ファイルが見つかりません')).toBeNull()
})
