---
name: config
description: pairvoice の設定（config.toml）を変えるときに使う。ポート、要約・合成・聞き取りのモデル、自動ミュートの条件と除外するアプリ、音量。声・プロンプト・辞書は api、要約の改善は tune。
---

# pairvoice の設定を変える

設定は常駐サーバーが起動時に1回だけ読む TOML。読んだファイルは `pairvoice status` の `config_path` にある（無ければ作る）。`pairvoice --config <そのパス> config` が、書ける項目すべてを今の値（書いていない項目は既定値）とともに出す。入れ子は TOML の表になる（`values.mute` は `[mute]`、`values.tts.sampler` は `[tts.sampler]`）。

## 手順

1. 今のファイルを読み、変える項目だけを書き足す・書き換える。利用者のコメントや他の項目はそのまま残す。
2. `pairvoice restart` で反映する。
3. `pairvoice status` で確かめる。`config_stale` が `false` なら新しい設定で動いている。設定が壊れていると常駐サーバーが立ち上がらないので、`~/Library/Logs/pairvoice.stderr.log` を見て直す。
4. 変えた項目と、変わる振る舞いを利用者に伝える。

## 自動ミュートからアプリを外す

自動ミュートは、どれかのアプリがマイクを開いている間（`microphone`）と、ほかのアプリが音を出している間（`audio_output`）に入る。アプリ側でマイクをオフにしても、通話に入っている間は入力が開いたままなので `microphone` になる。

- 外す名前は、その状況のさなかに `curl -sf "$(pairvoice url)/api/mute"` の `processes` で取る。アプリ名でなくプロセス名（`Slack Helper` など）で書く。
- `[mute]` の `microphone_ignore_processes`（マイク）と `audio_output_ignore_processes`（音）に書く。通話アプリは入力と出力を両方開くので、たいてい両方に書く。大文字小文字は区別しない。
- 外したアプリでは、本当に話すときも自動ミュートが入らない。そのときは `pairvoice mute 30m` で手で止めることを利用者に伝える。
- 反映後、同じ状況で `/api/mute` の `active` が `false`（ほかに理由が無ければ）になったことを確かめる。
