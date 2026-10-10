---
name: tune
description: pairvoice の読み上げ要約を改善するときに使う。要約プロンプトの手直し、studio のレビュー（👍 / 👎）の反映、要約モデルの比較・乗り換え、読み辞書の追加。
---

# pairvoice の要約を改善する

要約は常駐サーバーのローカル LLM が、データの置き場所の `prompt.txt` をシステムプロンプトにして作る。改善の良し悪しは `pairvoice eval` で測ってから決める。

## 前提

- データの置き場所は `PAIRVOICE_DATA_ROOT`（既定 `~/Library/Application Support/pairvoice`）。候補や結果はここではなく作業用の一時ディレクトリに置く。
- `pairvoice eval` は checker の7規則（句点の数・コード識別子・命令口調・「ね」止め・文字数と絵文字・一人称・です/ます調）で判定する。ヒューリスティックなので候補どうしの相対比較に使い、出力そのものも読んで判断する。
- 要約は貪欲デコードで決定論的なので、各条件1回で足りる。差を確かめたいときはケースを増やす（`--cases` に `id<TAB>input` の TSV、1行目はヘッダ。実入力は `corpus.jsonl` の `input`）。
- です/ます調は `config.toml` の `[eval] style = "casual"` のときだけ違反になる。

## 手順

1. 現状を測る: `pairvoice eval --reviews --out <tmp>/baseline.jsonl`。`--reviews` は studio のレビューをケースに加える。結果の各行の `verdict` が `bad` なら `ideal`（理想の出力）に近づけるのが目標、`good` なら `reference`（そのときの出力）の良さを保つのが条件。
2. 候補を作る: `prompt.txt` を写して直す。👎 の傾向を規則として足すより、既存の規則を明確にする方を先に試す。
3. 候補を測る: `pairvoice eval --prompt <tmp>/candidate.txt --reviews --out <tmp>/candidate.jsonl`。ALL_PASS 率、`bad` が `ideal` に近づいたか、`good` が劣化していないかを baseline と同じケースで比べる。2〜3を納得いくまで繰り返す。
4. 反映する: 変更点と比較結果（改善例・劣化例を含む）を利用者に見せ、了承を得てから pairvoice の API で書く。

   ```bash
   jq -n --rawfile text <tmp>/candidate.txt '{text: $text}' |
     curl -sf -X PUT http://127.0.0.1:17495/api/prompt -H 'Content-Type: application/json' -d @-
   ```

   pairvoice が履歴を撮るので、利用者は studio のプロンプト画面（または `POST /api/prompt/restore`）から戻せる。フックは読み上げのたびに `prompt.txt` を読むので、再起動は要らない。

## 読み辞書を直す

pairvoice が合成の直前に `dict.tsv` で表記を読みに置き換える（要約の評価には効かない）。書き込みは全置換なので、取り直した辞書に足してから `PUT` する。

```bash
curl -sf http://127.0.0.1:17495/api/dict |
  jq '.rows += [{from: "README", to: "リードミー", memo: ""}]' |
  curl -sf -X PUT http://127.0.0.1:17495/api/dict -H 'Content-Type: application/json' -d @-
```

`from` が同じ行が既にあれば、足さずにその行を書き換える。置き換えは上の行から順に部分一致で当てるので、長い表記を先に置き、短すぎる表記は避ける（「通り」は「予定通り」まで化ける）。結果は `POST http://127.0.0.1:17495/api/dict/test`（`{text, rows}`）で確かめられる。

## モデルを比べる

`pairvoice eval --model <Hugging Face のモデル ID> --out <tmp>/<名前>.jsonl` は、そのモデルをこのプロセスに読み込んで評価する（常駐サーバーとは別に重みの分のメモリを使う）。乗り換えるなら `config.toml` の `[llm] model` を書き換えて `pairvoice restart` する。プロンプトは今のモデルに合わせて育ててあるので、乗り換えたら手順 1〜4 をやり直す。
