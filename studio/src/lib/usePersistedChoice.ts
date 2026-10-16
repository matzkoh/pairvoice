import { useState } from 'react'

// 見る人ごとの好み（並べ方など）を localStorage に覚える。保存は消えることも、読み書きが
// 断られることもある（プライベートウィンドウなど）ので、そのときは既定の選択肢で動かす
export function usePersistedChoice<T extends string>(
  key: string,
  choices: readonly T[],
  fallback: T,
): [T, (next: T) => void] {
  const [choice, setChoice] = useState<T>(() => {
    try {
      return choices.find((c) => c === localStorage.getItem(key)) ?? fallback
    } catch {
      return fallback
    }
  })

  function choose(next: T) {
    setChoice(next)
    try {
      localStorage.setItem(key, next)
    } catch {
      // 保存できなくても、この画面の間は切り替わる
    }
  }

  return [choice, choose]
}
