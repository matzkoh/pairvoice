// corpus.jsonl の ts は
// "YYYY-MM-DD HH:mm:ss"（空白区切り、オフセット無し＝ローカル時刻として解釈される）。
// prompt_changed_at は ISO 文字列（T区切り、Z付き）なので replace(' ', 'T') は
// no-op のまま素通りする。どちらの形式でも動く。
export function formatWhen(ts: string): string {
  const d = new Date(ts.replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return ts
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`
}
