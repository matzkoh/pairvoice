/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { PLAYER_SLOT_ID, PlayerBar } from './PlayerBar'

afterEach(cleanup)

function Page() {
  const audioRef = useRef<HTMLAudioElement>(null)
  return (
    <>
      <div id={PLAYER_SLOT_ID} />
      <audio ref={audioRef} data-testid="audio" />
      <PlayerBar audioRef={audioRef} />
    </>
  )
}

// jsdom の <audio> は音を読み込まないので、長さ・位置・停止中かどうかを差し替えて知らせる
function load(state: { duration: number; currentTime: number; paused: boolean }) {
  const audio = screen.getByTestId<HTMLAudioElement>('audio')
  let time = state.currentTime
  Object.defineProperties(audio, {
    duration: { configurable: true, get: () => state.duration },
    paused: { configurable: true, get: () => state.paused },
    currentTime: {
      configurable: true,
      get: () => time,
      set: (next: number) => {
        time = next
      },
    },
  })
  const play = vi.fn(async () => {})
  const pause = vi.fn()
  audio.play = play
  audio.pause = pause
  act(() => {
    fireEvent(audio, new Event('timeupdate'))
  })
  return { audio, play, pause }
}

it('音を読み込むまでは出さない', () => {
  render(<Page />)
  expect(screen.queryByRole('group', { name: '再生のコントローラー' })).toBeNull()
})

it('再生中は位置と長さを出し、ボタンで一時停止する', () => {
  render(<Page />)
  const { pause } = load({ duration: 65, currentTime: 3.4, paused: false })
  expect(screen.getByText('0:03 / 1:05')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '一時停止' }))
  expect(pause).toHaveBeenCalled()
})

it('止まっているときは、ボタンで続きを再生する', () => {
  render(<Page />)
  const { play } = load({ duration: 6, currentTime: 2, paused: true })
  fireEvent.click(screen.getByRole('button', { name: '続きを再生' }))
  expect(play).toHaveBeenCalled()
})

it('バーを動かすと再生位置が移る', () => {
  render(<Page />)
  const { audio } = load({ duration: 6, currentTime: 0, paused: true })
  fireEvent.change(screen.getByRole('slider', { name: '再生位置' }), { target: { value: '4' } })
  expect(audio.currentTime).toBe(4)
})
