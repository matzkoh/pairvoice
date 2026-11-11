---
name: api
description: pairvoice の常駐サーバーを操作するときに使う。任意の文の読み上げ・合成、声やスタイルの選択・作成、ミュート・停止、読み辞書やプロンプトの読み書き、読み上げの記録・レビューの参照。要約の改善は tune。
---

# pairvoice の API を叩く

常駐サーバーの接続先は `pairvoice url` が出し、API の仕様は `GET /openapi.json` が配る。道筋・引数・応答の形・エラーはこの仕様に従い、叩く前に該当箇所を読む。冒頭の `info.description` に全体の約束事（ミュート中の挙動、`voice` と `style` の選び方など）がある。

```bash
# 道筋と要約の一覧
curl -sf "$(pairvoice url)/openapi.json" |
  jq -r '.paths | to_entries[] | .key as $p | .value | to_entries[] | "\(.key|ascii_upcase) \($p)\t\(.value.summary)"'

# 1つの道筋の詳細と、参照している型
curl -sf "$(pairvoice url)/openapi.json" | jq '.paths["/api/speak"], .components.schemas.SpeakRequest'
```

応答が無ければ `pairvoice status` で常駐サーバーの状態を見る。

## 素材から声を作って選ぶ

1. `POST /curate` で解析し、`POST /curate/{job_id}/render` で参照音声を 4 通り組む。自動（`pieces` なし）、高SN（`snr_db` の大きい順）、高め（`snr_db` 15 以上を `pitch_hz` の高い順）、低め（同じく低い順）。自動以外は `deviation` の付いた区間を順に足し、合計 25 秒前後で止める。`deviation` の小さい順は自動とほぼ同じになるので組まない。どれが似るかは声の主によって違う
2. `POST /voices/evaluate` で比べ、崩れなかったものを `POST /curate/{job_id}/profile` で声にする
3. 本人に似ているかは測れないので、声にした候補を `/speak` で鳴らして利用者に選んでもらい、選ばれなかった声は消す
