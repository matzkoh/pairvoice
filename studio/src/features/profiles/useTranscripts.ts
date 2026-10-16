import { useEffect, useState } from 'react'

import { ApiError, apiSend } from '@/lib/api'
import type { CurateAnalysis } from '@/lib/api-types'

type Texts = ReadonlyMap<number, string | null>
type Filled = { jobId: string; texts: Texts }

const EMPTY: Texts = new Map()

// 候補のセリフを、流れの順に1つずつ聞き取って埋める。まとめて頼むと聞き取りが列を占めて
// 読み上げが待たされるので、前の答えを待ってから次を頼む。聞き取れなかった候補は null
export function useTranscripts(analysis: CurateAnalysis | null): Texts {
  // どの解析のセリフかも持つ。解析が替わったら、前の解析の分は見せない
  const [filled, setFilled] = useState<Filled | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    if (analysis) {
      void fill(analysis, controller.signal, (index, text) =>
        setFilled((current) => ({
          jobId: analysis.job_id,
          texts: new Map(current?.jobId === analysis.job_id ? current.texts : EMPTY).set(
            index,
            text,
          ),
        })),
      )
    }
    return () => controller.abort()
  }, [analysis])

  return filled && filled.jobId === analysis?.job_id ? filled.texts : EMPTY
}

async function fill(
  analysis: CurateAnalysis,
  signal: AbortSignal,
  add: (index: number, text: string | null) => void,
) {
  for (const c of analysis.candidates) {
    let text: string | null
    try {
      const response = await apiSend<{ text: string }>(
        `/curate/${analysis.job_id}/candidates/${c.index}/transcript`,
        'POST',
        undefined,
        { signal, allowNotFound: true },
      )
      text = response.text
    } catch (err: unknown) {
      // 画面を離れた・解析が消えたら、残りも聞けない
      if (signal.aborted || (err instanceof ApiError && err.status === 404)) return
      text = null
    }
    if (signal.aborted) return
    add(c.index, text)
  }
}
