#!/bin/bash
# MessageDisplay hook: アシスタントのテキスト出力を pairvoice で要約・音声化して読み上げる。
# 失敗しても Claude Code 本体の動作に影響しないよう、常に exit 0 で終わる。
#
# 要約・音声化・再生・記録（corpus.jsonl）は pairvoice の /narrate がまとめて行う。
# このスクリプトは返事1つにつき /narrate を1回叩くだけ。数秒かかるので、
# バックグラウンドに逃がしてから即座に終了する（Claude Code の画面更新を待たせない）。

set -u

# 既定は本番の置き場。テストから別の場所に差し替えられるようにしてある。
# ログをスクリプトの隣に置かないのは、プラグインとして入れるとこのディレクトリが
# 更新のたびに差し替わるため。常駐サーバーのログ（pairvoice.log）と並べる。
# 重複排除のマーカーはユーザーごとの一時ディレクトリ（macOS の $TMPDIR）に置く。共有の /tmp だと
# 別のユーザーが同じ名前のディレクトリを先に作れてしまう
TMP_ROOT="${TMPDIR:-/tmp}"
STATE_DIR="${CLAUDE_SPEAK_STATE_DIR:-${TMP_ROOT%/}/claude-speak-state}"
LOG_FILE="${CLAUDE_SPEAK_LOG_FILE:-$HOME/Library/Logs/speak-summary.log}"
# 常駐サーバーと別々に更新されるので、版を毎回名乗る。食い違うとメニューバーが知らせる
PLUGIN_VERSION=$(jq -r '.version' "${0%/*}/../.claude-plugin/plugin.json" 2>/dev/null)
PLUGIN_VERSION="${PLUGIN_VERSION:-unknown}"
VERSION_HEADER="X-Pairvoice-Plugin: $PLUGIN_VERSION"
# 読み上げの声とスタイル。空なら使用中のプロファイルの声で、そのプロファイルの caption のまま読む。
# Claude Code の settings.json の env に書けば、プロジェクトごとに声を変えられる
VOICE="${PAIRVOICE_VOICE:-}"
STYLE="${PAIRVOICE_STYLE:-}"

# マジックナンバー
# 要約と合成を1回で待つ。要約モデルのコールドロード（実測9.23秒。10分使わないと解放される）、
# 日本語にならなかった要約のやり直し、長い返事の合成まで含めて余裕を持たせる。
# 短いとサーバーは生きているのに打ち切り、誤った理由でスキップする
readonly NARRATE_TIMEOUT_SECONDS=150
readonly SEEN_MARKER_MAX_AGE_DAYS=1 # 重複排除マーカーの保持期間

log() {
  local level="$1" message="$2"
  printf '[%s] %s: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$message" >>"$LOG_FILE"
}

# 古い重複排除マーカーが無限に溜まらないよう掃除する（実行のついでに毎回試みる）
cleanup_old_seen_markers() {
  find "$STATE_DIR" -maxdepth 1 -name 'seen-*' -mtime "+${SEEN_MARKER_MAX_AGE_DAYS}" -delete 2>/dev/null
}

# 入力JSONを1回だけパースし、final / message_id / delta(text) をグローバル変数にセットする。
# final・message_id は改行を含まない前提で1行目にタブ区切りで出力し、
# delta は改行を含み得るテキストなので2行目以降（残り全体）として扱う。
# コマンド置換は末尾改行を除去するため、delta が空（＝末尾の"\n"が消えて改行自体が
# 無くなる）場合は header 行しか残らない。その場合 PARSED_TEXT は空文字にする。
parse_input() {
  local raw="$1" full header
  full=$(printf '%s' "$raw" | jq -r '
    (.final | tostring) + "\t" + (.message_id // "") + "\n" + (.delta // "")
  ' 2>/dev/null)
  header="${full%%$'\n'*}"
  if [[ "$full" == *$'\n'* ]]; then
    PARSED_TEXT="${full#*$'\n'}"
  else
    PARSED_TEXT=""
  fi
  IFS=$'\t' read -r PARSED_FINAL PARSED_MESSAGE_ID <<<"$header"
}

# 要求に応答が無かった（curl の status が 000）理由を /health から絞る。
# ダウンロード中は wait_download: false で 503 が返るが、読み込みや生成が長引いて
# タイムアウトしてもサーバーは生きているので、「server down」と書くと原因を取り違える。
# /health はキューを通らないので、その間も答える
unanswered_reason() {
  local states
  states=$(curl -s --max-time 3 "$PAIRVOICE_API/health" 2>/dev/null \
    | jq -r '"\(.llm.state) \(.tts.state)"' 2>/dev/null)
  if [ -z "$states" ]; then
    echo "server down"
    return
  fi
  case " $states " in
    # 古い常駐サーバーは wait_download を知らず、ダウンロードを待ってタイムアウトする
    *" downloading "*) echo "model downloading" ;;
    *" loading "*) echo "model loading" ;;
    *) echo "timeout" ;;
  esac
}

# 返事を pairvoice に読み上げてもらう。読めなかった理由は NARRATE_STATUS に入れる。
# 読み辞書（dict.tsv）は pairvoice が合成の直前にかける
narrate() {
  local text="$1" message_id="$2" response status body
  NARRATE_SUMMARY=""
  NARRATE_STATUS=""
  # 本文は標準入力で渡す。引数に載せると長い出力で ARG_MAX を超えて jq も curl も動かない
  response=$(printf '%s' "$text" | jq -Rs --arg message_id "$message_id" \
    --arg voice "$VOICE" --arg style "$STYLE" \
    '{text: ., message_id: $message_id, wait_download: false}
      + (if $voice == "" then {} else {voice: $voice} end)
      + (if $style == "" then {} else {style: $style} end)' \
    | curl -s --max-time "$NARRATE_TIMEOUT_SECONDS" -w '\n%{http_code}' \
    -X POST "$PAIRVOICE_API/narrate" -H 'Content-Type: application/json' -H "$VERSION_HEADER" --data-binary @- 2>&1)
  status="${response##*$'\n'}"
  body="${response%$'\n'*}"

  if [ "$status" = "200" ]; then
    NARRATE_SUMMARY=$(printf '%s' "$body" | jq -r '.summary // empty' 2>/dev/null)
    return 0
  fi
  if [ "$status" = "000" ]; then
    NARRATE_STATUS="$(unanswered_reason)"
    return 1
  fi
  # サーバーのエラーコードをログの文言に直す
  case "$(printf '%s' "$body" | jq -r '.error // ""' 2>/dev/null)" in
    muted) NARRATE_STATUS="muted: $(printf '%s' "$body" | jq -r '.reason // "unknown"' 2>/dev/null)" ;;
    dropped) NARRATE_STATUS="superseded" ;;
    no_speakable_text) NARRATE_STATUS="no speakable text" ;;
    model_load_failed) NARRATE_STATUS="model load failed" ;;
    model_downloading) NARRATE_STATUS="model downloading" ;;
    generation_failed) NARRATE_STATUS="generation failed" ;;
    profile_missing) NARRATE_STATUS="profile missing" ;;
    prompt_missing) NARRATE_STATUS="prompt.txt missing or empty" ;;
    no_japanese_summary) NARRATE_STATUS="no Japanese summary after retries" ;;
    profile_not_found) NARRATE_STATUS="voice not found: PAIRVOICE_VOICE=$VOICE" ;;
    style_not_found) NARRATE_STATUS="style not found: PAIRVOICE_STYLE=$STYLE" ;;
    *)
      # 道筋が無い。常駐サーバーとプラグインの版が食い違っている（何度試しても同じ）
      case "$status" in
        404 | 405) NARRATE_STATUS="version mismatch: plugin ${PLUGIN_VERSION}" ;;
        *) NARRATE_STATUS="narrate failed: http $status: $body" ;;
      esac
      ;;
  esac
  return 1
}

main() {
  # 接続先は設定の port で変わるので pairvoice に訊く。url を持たない 0.11 以前の pairvoice は既定のポートで待つ
  PAIRVOICE_API="$(pairvoice url 2>/dev/null || echo http://127.0.0.1:17495)/api"
  mkdir -p "$STATE_DIR"
  cleanup_old_seen_markers

  local input="$1"
  parse_input "$input"
  [ "$PARSED_FINAL" = "true" ] || return 0
  [ -n "$PARSED_TEXT" ] && [ -n "$PARSED_MESSAGE_ID" ] || return 0

  local seen_marker="$STATE_DIR/seen-${PARSED_MESSAGE_ID}"
  mkdir "$seen_marker" 2>/dev/null || return 0

  narrate "$PARSED_TEXT" "$PARSED_MESSAGE_ID" || {
    log INFO "SKIP (${NARRATE_STATUS:-unknown})"
    return 0
  }
  # 鳴ったか（ミュートや「止める」で鳴らなかったか）は pairvoice のログに残る
  log INFO "QUEUED (message_id=${PARSED_MESSAGE_ID}): $NARRATE_SUMMARY"
  return 0
}

# Claude Code はフックプロセスの終了を待つため、/narrate を前面で待つと
# 再生完了まで画面更新がブロックされる。stdin だけ読み取ったら、パイプライン全体を
# fd を切り離したバックグラウンドに逃がして即座に終了する。
# テストから関数だけを source できるよう、実行はこのファイルを直接叩いたときに限る。
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  INPUT=$(cat)
  main "$INPUT" </dev/null >>"$LOG_FILE" 2>&1 &
  exit 0
fi
