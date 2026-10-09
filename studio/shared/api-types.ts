// studio の /api/* が返す形。server.ts と web/src の両方が参照する。
// ここに置く型は「HTTP を越える形」だけに限る。サーバー内部の都合は入れない。

// pairvoice の /health が申告するモデルの状態。enum ではなく union で書く
// （Node の type stripping は enum を通さない）。
export type ModelState =
  | 'unloaded'
  | 'downloading'
  | 'loading'
  | 'loaded'
  | 'failed'
  | 'misconfigured'

export type ModelHealth = {
  model: string
  state: ModelState
  detail: string
  last_used: string | null
}

export type CfgGuidanceMode = 'independent' | 'joint' | 'alternating'
export type TScheduleMode = 'linear' | 'sway'

// pairvoice が generate() に渡すサンプラー。キー名は mlx-audio の generate() の
// 引数名そのままで、途中で変換しない（名前がずれた値は黙って捨てられる）。
// 未設定は「config.toml とモデル既定に任せる」の意味。
export type SamplerOverrides = {
  num_steps?: number
  cfg_guidance_mode?: CfgGuidanceMode
  cfg_scale_text?: number
  cfg_scale_caption?: number
  cfg_scale_speaker?: number
  t_schedule_mode?: TScheduleMode
  sway_coeff?: number
  duration_scale?: number
  seconds?: number
  rng_seed?: number
  cfg_min_t?: number
  cfg_max_t?: number
  context_kv_cache?: boolean
  speaker_kv_scale?: number
  truncation_factor?: number
  rescale_k?: number
  rescale_sigma?: number
}

// tts だけが sampler を申告する。停止中の pairvoice からは health 自体が取れない。
// profile は使用中のプロファイル。まだ一度も合成していない（bootstrap 前）なら null
export type TtsHealth = ModelHealth & {
  sampler?: SamplerOverrides
  profile?: { id: string; name: string; source: ProfileSource } | null
  // プロファイル作成で候補に読ませる文（既定の声を自動で作るときと同じ）
  anchor_text?: string
}

export type PairvoiceHealth = {
  ok: boolean
  llm: ModelHealth
  tts: TtsHealth
  mute: { active: boolean; reason: string | null; until: string | null }
  queue: { running: number; waiting: number }
  dropped_recent: number
  config_stale: boolean
}

// pairvoice が停止していれば pairvoice は null。UI 側は「停止」として扱う。
export type StudioHealth = {
  pairvoice: PairvoiceHealth | null
}

export type PromptResponse = { text: string }

// 辞書の1行。TSV の1行が from/to/memo の3列（server.ts の parseDictTsv）。
export type DictRowData = { from: string; to: string; memo: string }
export type DictResponse = { rows: DictRowData[] }
// 話し方のスタイル。styles.json の1件で、常駐サーバー（styles.py）が /speak の style で読む。
// caption が null なら声のプロファイルの caption のまま読む。sampler は config.toml から
// 動かす項目だけを持つ
export type StyleData = { name: string; caption: string | null; sampler: SamplerOverrides }
export type StylesResponse = { styles: StyleData[] }
// /api/dict/test のプレビュー結果。dict タブが試し打ちに使う（旧 app.js:1139 の data.result）。
export type DictTestResponse = { result: string }

// prompt と caption の履歴は同じ形。ts は ISO 文字列のコロンとピリオドを
// ハイフンに置換したファイル名由来の値で、日付として parse できない（表示専用）。
export type HistoryItem = { name: string; ts: string }
export type HistoryResponse = { items: HistoryItem[] }
export type OkResponse = { ok: true }

// 声のプロファイル（参照音声 + caption）。design = studio で caption から作った、
// upload = 手持ちの wav を取り込んだ、auto / import = pairvoice が初回に作った
export type ProfileSource = 'design' | 'upload' | 'auto' | 'import'
export type ProfileItem = {
  id: string
  name: string
  caption: string
  source: ProfileSource
  created_at: string
}
export type ProfilesResponse = { items: ProfileItem[]; active: string | null }

// /synthesize が wav を作れたときの応答。鳴らさないのでミュートでは断られない
export type SpeakResponse = { relative_path: string; duration: number }

// レビューの判定。'none' は「取り消し」を送るときだけ使う値で、POST /api/reviews は
// この3値を受け付ける。保存された状態としての判定（CorpusItem.verdict）は 'none' を
// 持たない — サーバーが 'none' のレビューを null に潰して返すため（review タブが
// この潰しをフロントで再現しないよう、型でも Exclude<Verdict, 'none'> にしてある）。
export type Verdict = 'good' | 'bad' | 'none'

// /api/corpus の1件。caption タブは summary だけを使うが、review タブが全項目を使う。
export type CorpusItem = {
  ts: string
  message_id: string
  input: string
  summary: string
  audio_path?: string
  verdict: Exclude<Verdict, 'none'> | null
  ideal: string | null
  archived: boolean
  stale: boolean
}
export type CorpusResponse = {
  total: number
  items: CorpusItem[]
  prompt_changed_at: string | null
}
