import { useEffect, useRef, useState } from 'react'

import { toErrorMessage } from '@/lib/api'
import { createIdGenerator } from '@/lib/ids'
import { speakOnce } from '@/lib/speak'

import type { SamplerOverrides } from '../../../../shared/api-types'
import type { SamplerValues } from './samplerKnobs'

// values はフォームの生の値（TOML に書き出すため）、sampler は送信用に変換した値。
// design はプロファイル作成の候補づくりで、参照音声を使わず caption だけで声を作る。
// profileId は試聴で鳴らすプロファイル（その参照音声でクローンする）
export type TakeInput = {
  text: string
  caption: string
  values: SamplerValues
  sampler: SamplerOverrides
  design?: boolean
  profileId?: string
}

export type Take = {
  id: number
  seed: number
  status: 'waiting' | 'running' | 'done' | 'error'
  input: TakeInput
  relativePath?: string
  duration?: number
  elapsedMs?: number
  message?: string
}

export type TakeRequest = { input: TakeInput; count: number; seedBase: number | null }

const SEED_MAX = 2 ** 31 - 1

// seed を埋めてあれば連番にする。同じ設定で「隣の seed も聞く」という使い方が多く、
// 何を聞いたかを後から辿れる。空欄なら候補ごとに乱数を振る。
export function seedsFor(
  base: number | null,
  count: number,
  random: () => number = Math.random,
): number[] {
  return Array.from({ length: count }, (_, i) =>
    // + i の分だけ範囲を狭めておかないと、乱数が SEED_MAX 近くを引いたときに
    // 最後の候補（i = count - 1）の seed が SEED_MAX を超えうる
    base === null ? Math.floor(random() * (SEED_MAX - count + 1)) + i : base + i,
  )
}

// テイクは生成した時点の条件を抱えて残る。左の入力欄を書き換えても、過去のテイクの
// 再生成や caption の採用はそのテイクの条件で行う。
// 合成は MLX の1本のキューを通るので、1件ずつ順に投げる。まとめて1リクエストにすると
// Runner を数分占有してフックの読み上げが待たされ、全件揃うまで何も聞けない。
type Options = {
  // 鳴らせるようになったテイクの音声。1回の生成では最初の1件、再生成では差し替えた1件だけ
  // 通知する。聞き比べは「押したら鳴る」が前提だが、2件目以降まで鳴らすと聞いている最中の
  // 音を遮ってしまう
  onReady?: (relativePath: string) => void
}

export function useTakes({ onReady }: Options = {}) {
  const [takes, setTakes] = useState<Take[]>([])
  const [running, setRunning] = useState(false)
  // 「いま生成ループが動いているか」の正本。state の running はレンダー用の派生値で、
  // 同一 tick 内に start/retry が連続で呼ばれても反映は次のレンダーからになり、二重発火を
  // 防げない。ref を正本にして入口で弾き、/api/speak を常に1件ずつ叩く不変条件を守る。
  const busy = useRef(false)
  const stopped = useRef(false)
  // 画面を離れた（プロファイルを切り替えた）後に残りを投げると、Runner を塞いでフックの
  // 読み上げを待たせ、前のプロファイルの音が鳴る
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  // 番号（#1, #2…）はこのフックごとに 1 から振る
  const [nextId] = useState(() => createIdGenerator())

  function patch(id: number, changes: Partial<Take>) {
    setTakes((prev) => prev.map((t) => (t.id === id ? { ...t, ...changes } : t)))
  }

  // 打ち切ったあとの待機テイクは、鳴らない枠として残しても読めない
  function dropWaiting() {
    setTakes((prev) => prev.filter((t) => t.status !== 'waiting'))
  }

  // 排他は start と retry が守るべき同じ不変条件なので、入口をひとつにする
  function runExclusive(work: () => Promise<void>) {
    if (busy.current) return
    busy.current = true
    setRunning(true)
    void work().finally(() => {
      busy.current = false
      setRunning(false)
    })
  }

  // 成功なら音声のパス、失敗なら null を返す。呼び出し側は失敗でループを打ち切る
  async function generate(take: Pick<Take, 'id' | 'seed' | 'input'>): Promise<string | null> {
    // retry は同じ枠を差し替える。前回成功時の秒数を残すと、失敗した再生成に
    // 古い値が生き残って見えるため、開始時に必ず捨てる
    patch(take.id, {
      status: 'running',
      relativePath: undefined,
      duration: undefined,
      elapsedMs: undefined,
      message: undefined,
    })
    const startedAt = performance.now()
    try {
      const result = await speakOnce({
        text: take.input.text,
        caption: take.input.caption,
        sampler: { ...take.input.sampler, rng_seed: take.seed },
        design: take.input.design,
        profile_id: take.input.profileId,
      })
      patch(take.id, {
        status: 'done',
        relativePath: result.relative_path,
        duration: result.duration,
        elapsedMs: performance.now() - startedAt,
      })
      return result.relative_path
    } catch (err: unknown) {
      patch(take.id, { status: 'error', message: toErrorMessage(err) })
      return null
    }
  }

  function start(req: TakeRequest) {
    // 実行中に積むと、待機テイクだけが増えて誰も走らせない状態になる
    if (busy.current) return
    stopped.current = false
    const created: Take[] = seedsFor(req.seedBase, req.count).map((seed) => ({
      id: nextId(),
      seed,
      status: 'waiting',
      input: req.input,
    }))
    setTakes((prev) => [...created, ...prev])
    runExclusive(async () => {
      let notified = false
      for (const take of created) {
        // 中止は「以降を投げない」の意味で、進行中の1件は完走させる。1件失敗したら
        // 残りは同じ理由で落ちる見込みが高い（ミュート・設定不備）ので打ち切る
        const relativePath = stopped.current || !mounted.current ? null : await generate(take)
        if (relativePath === null) {
          dropWaiting()
          break
        }
        if (!mounted.current) break
        if (!notified) {
          notified = true
          onReady?.(relativePath)
        }
      }
    })
  }

  function stop() {
    stopped.current = true
  }

  function retry(id: number) {
    const target = takes.find((t) => t.id === id)
    if (!target) return
    runExclusive(async () => {
      const relativePath = await generate(target)
      if (relativePath !== null && mounted.current) onReady?.(relativePath)
    })
  }

  function clear() {
    if (busy.current) return
    setTakes([])
  }

  return { takes, running, start, stop, retry, clear }
}
