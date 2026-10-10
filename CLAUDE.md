# CLAUDE.md

常駐サーバー（`src/pairvoice/`）・studio（`studio/`）・読み上げフック（`plugin/`）・メニューバー（常駐サーバーの子）の4つは、データの置き場所と HTTP だけを共有する。

## 横断する規則

- 実行して生まれるもの（プロンプト・辞書・コーパス・声のプロファイル・レビュー）は `PAIRVOICE_DATA_ROOT` に置き、作業ツリーや同梱物には置かない。常駐サーバーは読み上げのたびに `prompt.txt` を読むので作業ツリーに置くとブランチの切り替えで読み上げが変わり、wheel の同梱物は更新で消える
- フックは `plugin/` の中だけで完結させる。プラグインはインストール時に `plugin/` だけがキャッシュへ写される
- MLX の処理は `Runner` のキューを通し、専用の1本のスレッドで実行する。別のスレッドで生成すると `There is no Stream(gpu, N)` で落ちる
- 試聴は保存済みの caption を書き換えず、`/api/synthesize` の引数で渡す（書き換えると、その最中のフックの読み上げが試行中の声で鳴る）。複数候補は seed だけ変えて1件ずつ叩く（まとめると `Runner` を占有し、フックの読み上げが待たされる）
- 要約の改善はプラグインの `tune` スキルが利用者のエージェントで行う。pairvoice は測る道具とデータだけを持つ
- データの置き場所を読み書きするのは常駐サーバーの API だけにする。studio の画面は API を呼ぶだけにする（書き手が2つあると、版の履歴やプロファイルの排他が片方から漏れる）。フックが corpus.jsonl に追記するのと、`pairvoice eval --model` がケースとプロンプトを読むのは例外

## studio

- 画面は常駐サーバーが根（`/`）で配り（`studio_web.py`）、API は `/api` の下に置く。画面の道筋は `/api` の下を取らない（無い API が 404 でなく画面や 405 になり、studio が「サーバーが古い」と見分けられなくなる）
- 依存はすべて `devDependencies` に入れる（実行時に Node は要らない。`shadcn add` は `dependencies` に入れるので移す）
- 公開用の wheel はビルド済みの `web/dist` を同梱するので、`(cd studio && pnpm build) && uv build` の順で作る
- 画面の項目は `web/src/components/nav.ts`、キーボード操作は `useHotkeys`（`web/src/lib/hotkeys.ts`）を通す
- `web/src/components/ui/` は shadcn/ui の生成物で、`studio/` で `pnpm exec shadcn add <name>` して足す
