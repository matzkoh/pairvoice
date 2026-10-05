#!/bin/bash
# MessageDisplay hook: アシスタントのテキスト出力を pairvoice で要約・音声化して読み上げる。
# 失敗しても Claude Code 本体の動作に影響しないよう、常に exit 0 で終わる。
#
# 直列化・ミュート判定・モデルの常駐・再生は pairvoice 側の責務。このスクリプトは
# 要約と音声化（play: true で、鳴らすのも pairvoice）を1回ずつ叩くだけ。
# 要約と音声化には数秒かかるので、パイプライン全体をバックグラウンドに
# 逃がしてから即座に終了する（Claude Code の画面更新を待たせない）。

set -u

# 既定は本番の置き場。テストから別の場所に差し替えられるようにしてある。
# ログをスクリプトの隣に置かないのは、プラグインとして入れるとこのディレクトリが
# 更新のたびに差し替わるため。常駐サーバーのログ（pairvoice.log）と並べる。
# 重複排除のマーカーはユーザーごとの一時ディレクトリ（macOS の $TMPDIR）に置く。共有の /tmp だと
# 別のユーザーが同じ名前のディレクトリを先に作れてしまう
TMP_ROOT="${TMPDIR:-/tmp}"
STATE_DIR="${CLAUDE_SPEAK_STATE_DIR:-${TMP_ROOT%/}/claude-speak-state}"
LOG_FILE="${CLAUDE_SPEAK_LOG_FILE:-$HOME/Library/Logs/speak-summary.log}"
PAIRVOICE_BASE="http://127.0.0.1:17495"
LLM_URL="$PAIRVOICE_BASE/llm"
SPEAK_URL="$PAIRVOICE_BASE/speak"
# 実行時に読み書きするデータの置き場所。studio も同じディレクトリを見る。
# git 作業ツリーに置くと、ブランチを切り替えたときに読み上げの挙動が変わってしまう。
DATA_DIR="${PAIRVOICE_DATA_ROOT:-$HOME/Library/Application Support/pairvoice}"
CORPUS_FILE="$DATA_DIR/corpus.jsonl" # プロンプト改善用の入力・出力ペア記録

# マジックナンバー
readonly SUMMARY_MAX_RETRIES=3
# 要約モデルのコールドロードは実測9.23秒（pairvoiceのidle_unload_secondsは600秒＝
# 10分でモデルを解放するため、10分以上間隔が空いた次の1回目は必ずこれを踏む）。
# 5秒などの短いタイムアウトだとロード中に打ち切られ、サーバーは生きているのに
# 「server down」という誤った理由でスキップされてしまう。実測値に十分な余裕を
# 持たせて30秒にする。000（本当にサーバーが応答しない場合）はリトライせず
# 即座にreturnする方針は変えない。タイムアウトさえ十分なら000は正真正銘の
# サーバー停止時にしか発生しないため、この判断で問題ない。
readonly LLM_TIMEOUT_SECONDS=30
readonly SPEAK_TIMEOUT_SECONDS=60 # 音声合成の所要時間の実測値に余裕を持たせる
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

# 音声合成が誤読しがちな語をひらがなに開く読み辞書。
# エントリは $DATA_DIR/dict.tsv（タブ区切り: 置換元・置換先・メモ）で管理する
# （例えば「通り」を足すと「予定通り（どおり）」まで「とおり」に化けるので、広げすぎない）。
normalize_reading() {
  local text="$1" dict_file="$DATA_DIR/dict.tsv" line from to rest
  [ -f "$dict_file" ] || { printf '%s' "$text"; return 0; }
  # bash 5.2 から置換文字列の & が一致した部分に化ける。辞書は字句どおりに置き換えたい
  shopt -u patsub_replacement 2>/dev/null
  # あえて `IFS=$'\t' read -r from to _memo` を使わない。tab は bash の
  # 「IFS whitespace」扱いになり、連続する区切り文字が1つに畳み込まれて
  # 空フィールド（置換先を空文字にしたいエントリ）が消える罠があるため、
  # パラメータ展開で手動分割する。
  while IFS= read -r line || [ -n "$line" ]; do
    # CRLF で保存された辞書の行末の \r を落とす（studio の parseDictTsv と同じ扱い）
    line="${line%$'\r'}"
    # タブを含まない行（空行・見出しメモ等）はスキップ
    case "$line" in *$'\t'*) ;; *) continue ;; esac
    from="${line%%$'\t'*}"
    rest="${line#*$'\t'}"
    if [[ "$rest" == *$'\t'* ]]; then
      to="${rest%%$'\t'*}"   # 3列目（メモ）は捨てる
    else
      to="$rest"             # メモ列なしの2列行
    fi
    [ -n "$from" ] || continue
    # 置換元をクォートするのが必須。素の # は ${var//#...} の
    # 先頭一致アンカーに化けて no-op になる既知の罠があるため。
    # 置換先はクォートしない。macOS の /bin/bash（3.2）はクォートを字句として残す
    text="${text//"$from"/$to}"
  done < "$dict_file"
  printf '%s' "$text"
}

# corpus.jsonlへの追記を一箇所に集約する。生成が失敗した場合はaudio_pathを
# 空文字にして記録する（プロンプト改善の材料としては、音声化できなかった
# ケースも「入力→要約」のペアとして価値があるため記録自体は続ける）。
write_corpus() {
  local message_id="$1" input="$2" summary="$3" audio_path="$4"
  # データの置き場所はスクリプトのディレクトリの外にあるので、新規マシンや
  # PAIRVOICE_DATA_ROOT を新しい場所に向けた直後には存在しない。作らずに追記すると
  # リダイレクトが失敗し、レビュー資産であるコーパスが黙って失われる（ログには
  # QUEUED が出るので気づけない）ため、追記の前に必ず作り、失敗は WARN で残す。
  if ! mkdir -p "$DATA_DIR" 2>/dev/null; then
    log WARN "corpus not recorded (cannot create data dir): $DATA_DIR"
    return 1
  fi
  # input は要約と同じく長くなりうるので、標準入力で渡す
  if ! printf '%s' "$input" | jq -cRs --arg ts "$(date '+%Y-%m-%d %H:%M:%S')" \
    --arg message_id "$message_id" --arg summary "$summary" --arg audio_path "$audio_path" \
    '{ts: $ts, message_id: $message_id, input: ., summary: $summary, audio_path: $audio_path}' \
    >>"$CORPUS_FILE" 2>/dev/null; then
    log WARN "corpus not recorded (append failed): $CORPUS_FILE"
    return 1
  fi
}

# 日本語文字（ひらがな/カタカナ/漢字）を含むかどうか
contains_japanese() {
  # -0777 で全体を1度に読む。行ごとだと1行目だけで判定して終わる
  printf '%s' "$1" | perl -0777 -CSD -ne 'exit(!(/\p{Hiragana}|\p{Katakana}|\p{Han}/))' 2>/dev/null
}

# 読み上げる実体があるかどうか。MessageDisplay の delta には、閉じタグだけの断片
# （`</invoke>` `</result>` `</task-notification>` `</teammate-message>`）や
# コードフェンスだけの行が単独で流れてくることがある。非空なので長さのガードは
# 通り抜けるが、要約させると「入力がありません。要約するログを入力してください」を
# そのまま読み上げるか、入力と無関係な要約を幻覚して読み上げてしまう。
# タグとバッククォートを落として、文字（かな・漢字・英数）が残らなければ捨てる。
# 判定に使うのは剥がした後の文字列だけで、要約に渡すのは元のテキストのまま。
has_speakable_content() {
  printf '%s' "$1" | perl -CSD -0777 -ne '
    s{</?[A-Za-z][^<>]*>}{}g;
    s{`+}{}g;
    exit(!(/[\p{Hiragana}\p{Katakana}\p{Han}\p{Alnum}]/));
  ' 2>/dev/null
}

# 要求に応答が無かった（curl の status が 000）理由を /health から絞る。
# 初回のモデルのダウンロード中は要求が待たされてタイムアウトするが、サーバーは生きているので
# 「server down」と書くと原因を取り違える。/health はキューを通らないので、その間も答える
unanswered_reason() {
  local states
  states=$(curl -s --max-time 3 "$PAIRVOICE_BASE/health" 2>/dev/null \
    | jq -r '"\(.llm.state) \(.tts.state)"' 2>/dev/null)
  if [ -z "$states" ]; then
    echo "server down"
    return
  fi
  case " $states " in
    *" downloading "*) echo "model downloading" ;;
    *" loading "*) echo "model loading" ;;
    *) echo "timeout" ;;
  esac
}

# ローカルLLMで読み上げ用の短い日本語要約を取得する。
# 戻り値は SUMMARY_TEXT / SUMMARY_STATUS のグローバル変数で渡す（stdoutではない）。
# `summary=$(get_summary ...)` のようにコマンド置換で呼ぶとサブシェルに閉じ込められ、
# ここで代入したグローバル変数が呼び出し元から見えなくなるため、あえて素の関数呼び出し
# （`get_summary ...; ` のように $() を使わない形）で呼ぶ前提にしている。
get_summary() {
  local text="$1" payload response attempt
  SUMMARY_TEXT=""
  SUMMARY_STATUS=""
  if [ ! -s "$DATA_DIR/prompt.txt" ]; then
    SUMMARY_STATUS="prompt.txt missing or empty"
    return 1
  fi
  # 本文は標準入力で渡す。引数に載せると長い出力で ARG_MAX を超えて jq も curl も動かない
  payload=$(printf '%s' "$text" | jq -Rs --rawfile system "$DATA_DIR/prompt.txt" \
    '{prompt: ., system: $system}')

  for attempt in $(seq 1 "$SUMMARY_MAX_RETRIES"); do
    response=$(printf '%s' "$payload" | curl -s --max-time "$LLM_TIMEOUT_SECONDS" -w '\n%{http_code}' \
      -X POST "$LLM_URL" -H 'Content-Type: application/json' --data-binary @- 2>&1)
    local status="${response##*$'\n'}" body="${response%$'\n'*}"

    case "$status" in
      409) SUMMARY_STATUS="superseded"; return 1 ;;
      503)
        # サーバーのエラーコードをログの文言に直す
        case "$(printf '%s' "$body" | jq -r '.error // "unknown"' 2>/dev/null)" in
          model_load_failed) SUMMARY_STATUS="model load failed" ;;
          generation_failed) SUMMARY_STATUS="generation failed" ;;
          profile_missing) SUMMARY_STATUS="profile missing" ;;
          *) SUMMARY_STATUS="unavailable" ;;
        esac
        return 1
        ;;
      000) SUMMARY_STATUS="$(unanswered_reason)"; return 1 ;;
    esac

    if [ "$(printf '%s' "$body" | jq -r '.muted // false' 2>/dev/null)" = "true" ]; then
      SUMMARY_STATUS="muted: $(printf '%s' "$body" | jq -r '.reason // "unknown"' 2>/dev/null)"
      return 1
    fi

    local candidate
    candidate=$(printf '%s' "$body" | jq -r '.text // empty' 2>/dev/null)
    if [ -n "$candidate" ] && contains_japanese "$candidate"; then
      SUMMARY_TEXT="$candidate"
      return 0
    fi
    log WARN "summary retry ${attempt}/${SUMMARY_MAX_RETRIES} (non-Japanese or failed): $body"
  done
  SUMMARY_STATUS="no Japanese summary after retries"
  return 1
}

# 音声を生成して pairvoice に鳴らしてもらい、データディレクトリからの相対パスを返す。
# 鳴り終わるのは待たない（再生の順番待ちとミュートの見張りは pairvoice がする）
request_speech() {
  local text="$1" payload response status body
  payload=$(jq -n --arg text "$text" '{text: $text, play: true}')
  response=$(curl -s --max-time "$SPEAK_TIMEOUT_SECONDS" -w '\n%{http_code}' \
    -X POST "$SPEAK_URL" -H 'Content-Type: application/json' -d "$payload" 2>&1)
  status="${response##*$'\n'}"
  body="${response%$'\n'*}"

  if [ "$status" = "000" ]; then
    log INFO "SKIP ($(unanswered_reason))"
    return 1
  fi
  if [ "$status" != "200" ]; then
    case "$(printf '%s' "$body" | jq -r '.error // "unknown"' 2>/dev/null)" in
      model_load_failed) log INFO "SKIP (model load failed)" ;;
      generation_failed) log INFO "SKIP (generation failed)" ;;
      profile_missing) log INFO "SKIP (profile missing)" ;;
      *) log WARN "SKIP (speak failed: http $status): $body" ;;
    esac
    return 1
  fi
  if [ "$(printf '%s' "$body" | jq -r '.muted // false' 2>/dev/null)" = "true" ]; then
    log INFO "SKIP (muted: $(printf '%s' "$body" | jq -r '.reason' 2>/dev/null))"
    return 1
  fi
  printf '%s' "$(printf '%s' "$body" | jq -r '.relative_path // empty' 2>/dev/null)"
}

main() {
  mkdir -p "$STATE_DIR"
  cleanup_old_seen_markers

  local input="$1"
  parse_input "$input"
  [ "$PARSED_FINAL" = "true" ] || return 0
  [ -n "$PARSED_TEXT" ] && [ -n "$PARSED_MESSAGE_ID" ] || return 0

  local seen_marker="$STATE_DIR/seen-${PARSED_MESSAGE_ID}"
  mkdir "$seen_marker" 2>/dev/null || return 0

  has_speakable_content "$PARSED_TEXT" || {
    log INFO "SKIP (no speakable text)"
    return 0
  }

  get_summary "$PARSED_TEXT" || {
    log INFO "SKIP (${SUMMARY_STATUS:-unknown})"
    return 0
  }

  local raw_summary="$SUMMARY_TEXT"
  local summary
  summary=$(normalize_reading "$raw_summary")

  local relative_path
  relative_path=$(request_speech "$summary") || {
    write_corpus "$PARSED_MESSAGE_ID" "$PARSED_TEXT" "$raw_summary" ""
    return 0
  }

  write_corpus "$PARSED_MESSAGE_ID" "$PARSED_TEXT" "$raw_summary" "$relative_path"
  # 鳴ったか（ミュートや「止める」で鳴らなかったか）は pairvoice のログに残る
  log INFO "QUEUED (message_id=${PARSED_MESSAGE_ID}): $summary"
  return 0
}

# Claude Code はフックプロセスの終了を待つため、要約〜生成〜再生を前面で行うと
# 再生完了まで画面更新がブロックされる。stdin だけ読み取ったら、パイプライン全体を
# fd を切り離したバックグラウンドに逃がして即座に終了する。
# テストから関数だけを source できるよう、実行はこのファイルを直接叩いたときに限る。
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  INPUT=$(cat)
  main "$INPUT" </dev/null >>"$LOG_FILE" 2>&1 &
  exit 0
fi
