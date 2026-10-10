# pairvoice

**Claude Code の返事を、ひと言の日本語にして読み上げる。**

Claude Code に長い作業を任せて別の画面を見ていると、とうに終わって入力を待っていたことに、あとから気づく。
返事をそのまま読み上げれば済みそうに思える。
ところが返事にはコミットのハッシュやテストの件数が混ざっていて、耳で聞いても何が起きたのか分からない。

> 入力: Task 2 実装完了（`45f2238`、135 passed）。レビュアーが走っている。<br>
> 読み上げ: 「実装が終わり、テストはすべて通っています。いまレビューを待っています。」

pairvoice は、返事をこのように2文ほどの日本語に縮めてから読む。
画面から目を離していても、いま何が終わって次に何をするのかが耳で分かる。

## できること

- **識別子を読まない。** ファイル名や関数名はそのまま読まず、何をしたのかに言い換える
- **手元だけで動く。** 要約（mlx-lm）と音声合成（Irodori-TTS）を Apple Silicon の上で動かし、返事の中身を外に送らない。その分メモリを使う（下の「動作要件」を参照）
- **会議中や動画・音楽の再生中は黙る。** マイクが使われている間と、ほかのアプリが音を出している間は自動でミュートする。メニューバーから時間を区切ってミュートもできる
- **声を選べる。** 声の描写（「落ち着いた低い声。ゆっくり話す。」など）から声を作るか、手持ちの音声を取り込んで使う
- **読み方を育てられる。** ブラウザの画面（studio）で、読み上げの履歴に 👍👎 を付け、要約の指示や読み替えの辞書を直せる

日本語話者向けのツールで、要約の指示も読み辞書も音声合成のモデルも、日本語を前提にしている。

## しくみ

```
Claude Code ──（プラグインのフック）──▶ 常駐サーバー ──▶ 要約 ──▶ 音声合成 ──▶ 再生
                                          │
                                          └─ studio（ブラウザ）で指示と辞書と声を育てる
```

Claude Code に入れるプラグインが返事を受け取り、手元で常駐するサーバーに要約と音声合成を頼んで再生する。
サーバーは `127.0.0.1` にだけ待ち受ける。

## はじめかた

**1. 必要なものをそろえる**

- Apple Silicon の Mac（ユニファイドメモリ 32GB 以上を推奨。下の「動作要件」を参照）
- [uv](https://docs.astral.sh/uv/)、`jq`（`brew install jq`）

**2. 入れる**

```bash
uv tool install pairvoice
pairvoice install
pairvoice warmup
```

`pairvoice` が見つからないと言われたら、`uv tool update-shell` を実行して端末を開き直す（uv を Homebrew で入れたときなど、`~/.local/bin` に PATH が通っていない）。

`pairvoice install` は、常駐サーバーをログイン時に起きるよう登録し、要約の指示と読み辞書の雛形を置く。
メニューバーにアイコンが出る。

`pairvoice warmup` は、モデルをダウンロード（初回だけ、約 15GB）して読み込み、終わるまで待つ。
終わる前に来た Claude Code の返事は読み上げない。

**3. Claude Code にプラグインを入れて、Claude Code を起動し直す**

```
/plugin marketplace add matzkoh/pairvoice
/plugin install pairvoice@pairvoice
```

モデルの準備の進み具合は `pairvoice status` で見られる。

## 動作要件

- macOS（Apple Silicon）
- メモリ: 既定のモデル（要約 `gemma-4-e4b-it-8bit`、音声合成 `Irodori-TTS-v4.1-Small-8bit`）を両方載せると、常駐で約 9.5GB、生成中のピークで約 12GB を使う（48GB の機種で実測）。
  どちらも10分使わなければ自動で解放する。
  メモリが少ないときは、下の「設定」で要約のモデルを小さくできる
- モデルは同梱しない。初回に Hugging Face から手元へダウンロードする。各モデルのライセンスはそれぞれの配布元に従う
  - 要約: [Gemma 4](https://huggingface.co/google/gemma-4-E4B-it)（[Gemma Terms of Use](https://ai.google.dev/gemma/terms)）
  - 音声合成: [Irodori-TTS](https://huggingface.co/Aratako/Irodori-TTS-v4.1-Small)（MIT）

## 読み上げた内容の扱い

返事の中身は外に出ないが、手元には残る。

- 返事の全文と要約は、手元のデータの置き場所（`~/Library/Application Support/pairvoice/`）の `corpus.jsonl` に書きためる。
  studio のレビューと要約の指示の改善に使う。
  作業の中身がそのまま入るので、要らなくなったら消す
- 外と通信するのは、モデルの初回ダウンロードだけ

## 声を変える

読み上げの声は**プロファイル**（参照音声と声の描写の組）で決まる。
studio の「プロファイル」画面で、

- 声の描写から候補をいくつか合成し、気に入ったものを選んで保存する
- 2つの声を聞き比べて近い方を選ぶのを繰り返し、望みの声に絞り込む（声を言葉で描写しなくてよい）
- 手持ちの wav（数秒〜十数秒）を取り込む

のどれかで作り、「使う」で切り替える。
API なら、声の描写を渡すだけで1回でプロファイルにできる（`POST /api/profiles/design`）。
何も作らなくても、最初の読み上げのときに既定の声が1つ作られる。
切り替えは次の読み上げから効き、再起動は要らない。

### 声とスタイルを読み上げごとに選ぶ

使用中の声とは別に、読み上げ1回ごとに声とスタイル（話し方）を選べる。

- **声**（`voice`）: プロファイルの名前か ID。同じ名前が複数あれば、いちばん新しく作ったものが選ばれる
- **スタイル**（`style`）: studio の「スタイル」画面で作る、caption とサンプラーの組の名前。プロファイルの caption の代わりに使う。変えられるのは速さ・テンション・抑揚の傾きまでで、声質（ささやき声など）は参照音声で決まるので、声質を変えたいときは `voice` で別のプロファイルを選ぶ

```bash
curl -s http://127.0.0.1:17495/api/speak -H 'Content-Type: application/json' \
  -d '{"text": "テスト", "voice": "落ち着いた声", "style": "ささやき"}'
pairvoice say --voice 落ち着いた声 --style ささやき "テスト"
```

`caption` はスタイルより優先する。鳴らさずに wav だけ作る `/api/synthesize` は、ミュートを見ず、`sampler` も受け付ける。
選べる名前は `GET /api/profiles` と `GET /api/styles` で引ける。
API はすべて `/api` の下にあり、エンドポイントと項目は、常駐サーバーが動いている間 http://127.0.0.1:17495/docs で引ける。
studio の画面でできること（プロンプト・辞書・スタイル・声の編集、読み上げのレビュー）と、CLI でできること（再起動・終了・studio の起動・`eval`）も、同じ API で行える。
Claude Code の読み上げでは、環境変数 `PAIRVOICE_VOICE` と `PAIRVOICE_STYLE` で選ぶ（プロジェクトの `.claude/settings.json` の `env` に書けば、プロジェクトごとに変えられる）。

## 読み方を育てる

要約は、いつも思いどおりに縮まるとは限らない。
識別子を読み上げてしまったり、知りたかった結果を落としたりすることがある。
読み上げの履歴に印を付けておくと、それを材料に要約の指示と辞書を直せる。

studio は常駐サーバーが `http://127.0.0.1:17495/` で配っている。メニューの「studio を開く」か `pairvoice studio` で開く。

| 画面 | できること |
| --- | --- |
| レビュー | 読み上げの履歴を聞き直し、👍👎 と「こう読んでほしかった」を残す |
| プロンプト | 要約の指示（`prompt.txt`）を直す。前の版に戻せる |
| 辞書 | 読み間違える言葉を、読み方に置き換える |
| 声 | 声の描写とサンプラーを変えて試聴し、気に入った描写を使用中の声に当てる |
| プロファイル | 声を作る、取り込む、切り替える |

要約の指示の改善は、Claude Code で `/pairvoice:tune` を実行して頼む。
レビューを材料に改善案を作り、`pairvoice eval` で今の指示と比べてから提案する。当てるかは人が決める。

## 使い方

メニューバーのアイコンから、読み上げを止める、ミュート（15分、30分、60分）、studio を開く、再起動（サーバーと studio）、終了ができる。
同じことはコマンドでもできる。

| コマンド | すること |
| --- | --- |
| `pairvoice status` | 状態（モデル、ミュート、声）を表示する |
| `pairvoice mute 30m` / `unmute` | 30分ミュートする（1〜480分）／解除する |
| `pairvoice say "テスト"` | 読み上げてみる（ミュート中でも鳴る。`--voice` `--style` `--caption` で声とスタイルを選ぶ） |
| `pairvoice stop` | 鳴っている読み上げと、順番待ちの読み上げを止める |
| `pairvoice warmup` | モデルを読み込み、終わるまで待つ（初回はダウンロードも） |
| `pairvoice restart` | 常駐サーバーを再起動する（設定を変えたら要る） |
| `pairvoice studio` | studio をブラウザで開く |
| `pairvoice eval` | 要約の指示やモデルを規則で採点する（`--reviews` でレビューも使う、`--model` で別のモデルを試す） |

「終了」で止めたサーバーは、ログインし直すか `launchctl kickstart gui/$(id -u)/local.pairvoice` で戻る。

## ミュート

手動のミュート（期限付き）と、自動の条件のどちらかが当てはまれば黙る。
自動の条件は、マイクの使用中（会議アプリを問わない）と、ほかのアプリが音を出している間（YouTube や Spotify の再生中など）。
ほかのアプリの音は鳴らす直前まで確かめ、通知音のように5秒以内に止めば、止んだ後に読み上げる。
読み上げている最中も見張る。ミュートやマイクが入ったらその場で止め、ほかのアプリが音を出したら音量を下げ、5秒続いたら止める。

## 設定

`~/.config/pairvoice/config.toml`。
無くても既定値で動く。
変えたら `pairvoice restart` する。

| 表 | よく触る項目 |
| --- | --- |
| `[llm]` | `model`（要約のモデル） |
| `[tts]` | `model`（音声合成のモデル）、`output_max_age_days`（合成した wav を消すまでの日数。既定 7、0 で消さない） |
| `[tts.profile]` | 最初の声を作るときだけ使う。`ref_audio`（取り込む wav）、`caption`（声の描写） |
| `[tts.sampler]` | 音声合成の細かい調整。studio の「声」画面で試して、表示される設定を写す |
| `[mute]` | `auto_microphone`（既定 true）、`auto_audio_output`（既定 true）、`audio_output_wait_seconds`（既定 5） |
| `[playback]` | `volume`（既定 1.0）、`duck_volume`（ほかのアプリの音が鳴っている間の音量。既定 0.3）、`max_wait_seconds`（順番待ちがこれを超えたら捨てる。既定 60） |
| `[eval]` | `style = "casual"` で、`pairvoice eval` がです/ます調を違反にする（タメ口に育てるとき） |

メモリが少ない機種では、要約のモデルを小さくすると要約側のピークが約 8GB から約 4.7GB に下がる。
ただしコード識別子をそのまま読み上げやすく、既定のモデルより崩れやすい。

```toml
[llm]
model = "mlx-community/gemma-4-e4b-it-4bit"
```

## 更新する

常駐サーバーとプラグインは別々に更新されるので、両方を上げる。
版がずれると API の道筋が合わず、読み上げが止まる（メニューバーに「版が違う」と出る）。

```bash
uv tool upgrade pairvoice && pairvoice restart
claude plugin marketplace update pairvoice
claude plugin update pairvoice@pairvoice
```

プラグインの更新は、Claude Code を起動し直すと効く。
プラグインを自動で上げるには、Claude Code の `/plugin` の Marketplaces で pairvoice を選び、「Enable auto-update」にする（Anthropic 以外のマーケットプレイスは、既定では自動で更新されない）。

## うまく鳴らないとき

- **何も鳴らない**: `pairvoice status` を見る
  - `mute.active` が `true` ならミュート中（`mute.reason` が `microphone` なら会議中の自動ミュート）
  - `state` が `downloading` / `loading` ならモデルの準備中。初回は時間がかかる
  - `state` が `failed` / `misconfigured` なら、`~/Library/Logs/pairvoice.log` を見る
  - サーバーが起動しては落ちるなら、落ちたときの出力が `~/Library/Logs/pairvoice.stderr.log` に残る
  - サーバーに届かなければ、メニューの「再起動」かログインし直しで戻す
- **サーバーは元気なのに鳴らない**: `~/Library/Logs/speak-summary.log` を見る。
  何も書かれていなければプラグインが読まれていないので、Claude Code を起動し直す。
  書かれていれば理由が `SKIP (...)` に出る（一覧は [plugin/README.md](https://github.com/matzkoh/pairvoice/blob/main/plugin/README.md)）
- **更新したら鳴らなくなった**: 常駐サーバーとプラグインの版がずれている。「更新する」の手順で両方を上げる
- **設定を変えたのに効かない**: `pairvoice restart`
- **辞書を直したのに効かない**: studio の「保存」を押すまでは書き換わらない

## やめるとき

Claude Code で `/plugin uninstall pairvoice@pairvoice`、続けて次を実行する。

```bash
pairvoice uninstall          # 常駐の登録を外す
uv tool uninstall pairvoice
```

要約の指示、辞書、声、読み上げの記録は `~/Library/Application Support/pairvoice/` に残る。
要らなければ消す。

## 開発に参加する

手元での動かし方、テスト、評価ハーネスは [DEVELOPMENT.md](https://github.com/matzkoh/pairvoice/blob/main/DEVELOPMENT.md) にある。

## ライセンス

[MIT](https://github.com/matzkoh/pairvoice/blob/main/LICENSE)
