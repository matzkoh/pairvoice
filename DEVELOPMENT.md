# pairvoice の開発

使う側の説明は [README](README.md)。開発の決まりごと（不変条件と落とし穴）は [CLAUDE.md](CLAUDE.md) にある。

## 構成

| 部分 | 場所 | 中身 |
| --- | --- | --- |
| 常駐サーバー | `src/pairvoice/` | Python / FastAPI。`127.0.0.1:17495`。要約（mlx-lm）と音声合成（mlx-audio / Irodori-TTS） |
| studio | `studio/` | `server.ts`（Node の標準ライブラリだけ、`127.0.0.1:17494`）と React 19 + Vite のフロント（`web/`） |
| 読み上げフック | `plugin/` | Claude Code プラグイン（フックと、要約を改善する `tune` スキル）。マーケットプレイスはリポジトリ直下の `.claude-plugin/marketplace.json` |
| メニューバー | `src/pairvoice/menubar.py` | 常駐サーバーの子として起きる |

## 手元で動かす

常駐は、利用者と同じ形（uv tool）で入れた wheel で動かす。
wheel でしか起きない壊れ方（同梱物の漏れ、依存の抜け）に、ふだん使いの中で気づけるようにするためである。

```bash
(cd studio && pnpm install && pnpm build)   # uv sync より先に。studio/web/dist が無いと uv sync が失敗する
uv sync
rm -rf dist && (cd studio && pnpm build) && uv build --wheel
uv tool install --reinstall dist/pairvoice-*.whl
pairvoice install                 # 初回だけ。LaunchAgent を tool 側の Python に向ける
```

LaunchAgent は tool 側の Python を指すので、リポジトリのコードを変えても常駐サーバーには効かない。
反映するときは、wheel のビルドと `uv tool install --reinstall` をやり直す。
入れ直したあとの立て直し方は、変えた場所で選ぶ。
サーバーを立て直すとモデルを読み込み直すので、要らないときは避ける。

| 変えた場所 | 立て直すもの |
| --- | --- |
| `studio/` だけ | `pairvoice studio --restart`（studio だけ。モデルは載ったまま） |
| `src/pairvoice/` | メニューの「再起動」（studio とサーバーの両方。`pairvoice restart` はサーバーだけで、studio は古いコードのまま残る） |
| `prompt.txt`、辞書 | 要らない（フックとサーバーが読み上げのたびに読む） |

作業ツリーのコードをその場で動かしたいときは、メニューの「pairvoice を終了」で常駐を止めてから手で起動する（ポート `:17495` を取り合うので同時には動かせない）。

```bash
uv run pairvoice serve            # Ctrl-C でメニューバーごと降りる
launchctl kickstart gui/$(id -u)/local.pairvoice   # 常駐に戻す

# studio のフロントを開発する（:17493。/api は :17494 へ proxy する）
node studio/server.ts --port 17494 &
(cd studio && pnpm dev)
```

プラグインは、clone したディレクトリをそのままマーケットプレイスとして入れられる
（`claude plugin marketplace add <clone のパス>`）。この場合キャッシュに写されず作業ツリーから読まれるので、
ブランチを切り替えるとふだんの読み上げのフックも変わる。

## テスト

```bash
uv run pytest                                   # 実モデルは載らない
uv run ruff check && uv run ruff format --check && uv run ty check
cd studio && node --test server.test.ts
cd studio && pnpm test:web && pnpm typecheck && pnpm lint && pnpm format:check
```

studio のテストのうち1本は、`uv run python` で `pairvoice.reading` を呼び、
辞書置換の Python 実装と JS 実装が一致することを確かめる。

## 公開用のビルド

```bash
(cd studio && pnpm build) && uv build
```

wheel には studio（ビルド済みの `web/dist` を含む）・`examples/` を同梱する（`pyproject.toml` の
`force-include`）。`web/dist` は git で追跡しないので、ビルドを忘れると wheel のビルドが失敗する。

## 要約の評価（`pairvoice eval`）

```bash
pairvoice eval                                      # 使用中の prompt.txt を同梱のケースで評価
pairvoice eval --prompt 候補.txt --reviews --out 結果.jsonl
pairvoice eval --model mlx-community/<モデル>       # 別のモデルをこのプロセスに読み込んで評価
```

`src/pairvoice/checker.py` が次の7つの規則で判定し、`evaluation.py` が集計する。同梱のケースは
`src/pairvoice/eval_cases.tsv`。`--reviews` は studio のレビュー（アーカイブしたものを除く）をケースに加える。

1. 句点・感嘆符・疑問符が1〜2個（二文まで）
2. コード識別子（ファイル名・関数名・パスなど）を含まない
3. 命令・急かし口調を含まない
4. 名詞・形容詞に直接「ね」を付けて終わらない
5. 10〜45文字程度で、絵文字を含まない
6. 一人称（俺・僕など）を含まない
7. です・ます調の文末を含まない（`config.toml` の `[eval] style = "casual"` のときだけ判定する）

改善の作業は利用者のエージェントがプラグインの `tune` スキルに沿って行い、pairvoice は測る道具だけを持つ。

## データの置き場所を変える

`PAIRVOICE_DATA_ROOT` で置き場を変えられる。シェルに設定してから `pairvoice install` を実行し直す
（launchd から起きる常駐サーバーにはシェルの環境変数が届かないので、LaunchAgent に焼き込む）。
`config.toml` で `tts.output_dir` を明示している場合は、その親もデータの置き場所に揃える（ずれていると
`install` が警告する。片方だけだと studio の再生が黙って失敗する）。

## 詳しい状態の見方

`pairvoice status` は常駐サーバーの `/health` をそのまま表示する。

| 項目 | 意味 |
| --- | --- |
| `llm.state` / `tts.state` | `unloaded` / `downloading` / `loading` / `loaded` / `failed` / `misconfigured` |
| `tts.profile` | 使用中の声のプロファイル（まだ一度も読み上げていなければ `null`） |
| `mute` | ミュート中か、その理由（`manual` / `microphone` / `audio_output`）と期限 |
| `playback` | 鳴っているか（`playing`）と、順番待ちの数（`waiting`） |
| `dropped_recent` | 直近で後続に追い越されて捨てた要約の数。3件以上ならキューが詰まっている |
| `config_stale` | 設定ファイルが起動後に変わった（`pairvoice restart` が要る） |

ログは、常駐サーバーが `~/Library/Logs/pairvoice.log`、フックが `~/Library/Logs/speak-summary.log`。
常駐サーバーのログは `serve --log-file` が 10MB × 3世代で回しながら書き、成功した `/health` は書かない。
メニューバーの警告は `~/Library/Logs/pairvoice-menubar.log` に同じく回しながら書く。
logging を通らない出力（落ちたときの Traceback、ネイティブ層の出力）は、launchd が `~/Library/Logs/pairvoice.stderr.log` に書く。
フックのログの `SKIP (...)` の理由は [plugin/README.md](plugin/README.md) にまとめてある。
