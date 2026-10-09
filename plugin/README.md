# pairvoice プラグイン

Claude Code の `MessageDisplay` フック。アシスタントの出力を pairvoice の常駐サーバー
（`127.0.0.1:17495`）で要約・音声化し、常駐サーバーが再生する。サーバー本体はこのプラグインに含まれないので、
先にリポジトリの README に沿って起動しておく。

フック本体は `hooks/speak-summary.sh`。`bash` `curl` `jq` `perl` を使う
（`jq` 以外は macOS に最初からある）。

## インストール

```
/plugin marketplace add matzkoh/pairvoice
/plugin install pairvoice@pairvoice
```

手元の clone から入れるなら、`matzkoh/pairvoice` の代わりにリポジトリのパスを渡す。

以前 `~/.claude/settings.json` の `hooks.MessageDisplay` に `speak-summary.sh` を直接登録
していた場合は、そちらを外す（両方あると二重に読み上げる）。

## 要約を改善する

`/pairvoice:tune` で、studio のレビューを材料に要約の指示（`prompt.txt`）の改善案を作らせる。
エージェントは `pairvoice eval` で今の指示と比べてから提案し、了承を得たら studio の API で書き込む。
要約のモデルを比べるときにも使う。

## 声とスタイルを選ぶ

フックは環境変数 `PAIRVOICE_VOICE`（プロファイルの名前か ID）と `PAIRVOICE_STYLE`（studio の「スタイル」画面で作ったスタイルの名前）を読む。
空なら使用中のプロファイルの声で、その caption のまま読む。
プロジェクトの `.claude/settings.json` の `env` に書けば、プロジェクトごとに声を変えられる。

```json
{ "env": { "PAIRVOICE_VOICE": "落ち着いた声", "PAIRVOICE_STYLE": "ささやき" } }
```
## データとログ

| | 場所 |
|---|---|
| 要約プロンプト・読み辞書・コーパス（`prompt.txt` `dict.tsv` `corpus.jsonl`） | `~/Library/Application Support/pairvoice`（`PAIRVOICE_DATA_ROOT`） |
| ログ | `~/Library/Logs/speak-summary.log` |
| 重複排除マーカー | `$TMPDIR/claude-speak-state` |

プラグインのディレクトリは更新のたびに差し替わるので、実行時に生まれるものはすべて外に置く。

## トラブルシューティング

読み上げが来ないときは、まずログを見る。`SKIP` の理由がそのまま原因を指す。

```bash
tail -30 ~/Library/Logs/speak-summary.log
```

| ログの理由 | 原因 |
|---|---|
| `prompt.txt missing or empty` | データの置き場所に `prompt.txt` が無い |
| `muted: <reason>` | ミュート中（`microphone` なら会議中の自動ミュート、`manual` なら手動） |
| `server down` | pairvoice の常駐サーバーが応答しない |
| `model downloading` | 初回のモデルのダウンロード中（フックは待たない）。`pairvoice warmup` で先に済ませておく |
| `model loading` | モデルの読み込みが長引き、待ちきれなかった |
| `timeout` | サーバーは生きているが、要約か音声合成が時間内に終わらなかった |
| `no speakable text` | 閉じタグやコードフェンスだけの断片で、読み上げる中身が無い |
| `model load failed` | モデルのロードに失敗、または設定不備 |
| `profile missing` | `ref_audio` に指定した参照音声が無い |
| `voice not found` / `style not found` | `PAIRVOICE_VOICE` / `PAIRVOICE_STYLE` の名前のプロファイル・スタイルが無い |

`QUEUED` は音声を作って常駐サーバーに渡したところまで。鳴らなかった（ミュートが入った、止めた、
順番待ちが長すぎた）理由は常駐サーバーのログ（`~/Library/Logs/pairvoice.log`）に出る。

理由がログから絞れないときは `pairvoice status` で常駐サーバーの状態を見る。

同じ応答が二度読まれないよう `$TMPDIR/claude-speak-state/seen-<message_id>` にマークを置く。
**手で動作確認するときは `message_id` を毎回変える**（同じ ID の2回目は黙って何もしない）。

```bash
jq -n --arg id "manual-$(date +%s)" \
  '{final: true, message_id: $id, delta: "動作確認です。"}' \
  | bash plugin/hooks/speak-summary.sh
```
