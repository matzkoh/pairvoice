// pairvoice の API が返す形を、画面が使う分だけ写したもの。正本は pairvoice の /docs。
// ここに置く型は「HTTP を越える形」だけに限る。サーバー内部の都合は入れない。

// pairvoice の /health が申告するモデルの状態。
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

export type PromptResponse = { text: string }

// 辞書の1行。TSV の1行が from/to/memo の3列（pairvoice の reading.read_rows）。
export type DictRowData = { from: string; to: string; memo: string }
export type DictResponse = { rows: DictRowData[] }
// 話し方のスタイル。styles.json の1件で、常駐サーバー（styles.py）が /speak の style で読む。
// caption が null なら声のプロファイルの caption のまま読む。sampler は config.toml から
// 動かす項目だけを持つ
export type StyleData = { name: string; caption: string | null; sampler: SamplerOverrides }
export type StylesResponse = { items: StyleData[] }
// /api/dict/test のプレビュー結果。dict タブが試し打ちに使う。
export type DictTestResponse = { result: string }

// prompt と caption の履歴は同じ形。ts は ISO 文字列のコロンとピリオドを
// ハイフンに置換したファイル名由来の値で、日付として parse できない（表示専用）。
export type HistoryItem = { name: string; ts: string }
export type HistoryResponse = { items: HistoryItem[] }

// 声のプロファイル（参照音声 + caption）。design = caption や合成した声から作った、
// upload = 手持ちの wav を取り込んだ、curate = 雑多な音声ファイルから選り分けた、
// auto / import = pairvoice が初回に作った
// random は参照音声を持たず、読み上げのたびに members から1つを選ぶ
export type ProfileSource = 'design' | 'upload' | 'curate' | 'auto' | 'import' | 'random'
// random の声の選び方。shuffle は混ぜた順に、全員を一巡するまで同じ声を選ばない
export type ProfileOrder = 'random' | 'shuffle'
export type ProfileItem = {
  id: string
  name: string
  caption: string
  source: ProfileSource
  created_at: string
  // 要約の口調（口調の指示と入出力の例）。空なら既定の口調で読む
  tone: string
  // random の声が選ぶ声の ID。ほかの声は空
  members: string[]
  // random の声の選び方。ほかの声は random
  order: ProfileOrder
}
export type ProfilesResponse = { items: ProfileItem[]; active: string | null }

// POST /profiles/curate の応答。report はどの区間を参照音声に使ったか
export type CurateReport = {
  files: { name: string; seconds: number }[]
  // 長すぎるので後ろを使わなかった
  truncated: boolean
  // 話す長さの多い順。chosen がいちばん多く話す人
  speakers: { speaker: number; seconds: number; chosen: boolean }[]
  candidates: number
  seconds: number
  // index は CurateAnalysis の candidates の番号
  pieces: { index: number; file: string; start: number; end: number; snr_db: number }[]
}
export type CurateResponse = { profile: ProfileItem; report: CurateReport }

// POST /curate の候補。時刻はファイルを無音を挟んでつないだ流れの秒、file は files の番号
export type CurateCandidate = {
  index: number
  file: number
  start: number
  end: number
  snr_db: number
  loudness_db: number | null
  pitch_hz: number | null
  brightness_hz: number | null
  // ふだんの声からの離れ。null は S/N が低すぎて自動では使わない区間
  deviation: number | null
}

// POST /curate の応答。job_id で区間を聴き、選んで声を作る
export type CurateAnalysis = {
  job_id: string
  files: { name: string; offset: number; seconds: number }[]
  truncated: boolean
  speakers: { speaker: number; seconds: number; chosen: boolean }[]
  segments: { start: number; end: number; speaker: number }[]
  candidates: CurateCandidate[]
  preselected: number[]
  // levels は step 秒ごとの音の大きさ（0〜100）
  envelope: { step: number; levels: number[] }
  limits: { min_seconds: number; target_seconds: number; max_seconds: number }
}

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
  // 口調を使った声の ID（声ごとの口調より前の読み上げには無い）
  voice?: string | null
  // voice の声のいまの名前。消した声は voice の ID のまま
  voice_name?: string | null
  // 合成に使ったスタイルの名前。指定が無ければ null
  style?: string | null
  // 要約に使ったモデル（記録し始める前の読み上げには無い）
  summary_model?: string | null
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

// GET /corpus/counts。レビューの絞り込みごとの件数
export type CorpusCounts = {
  all: number
  unreviewed: number
  bad: number
  archived: number
  stale: number
}
