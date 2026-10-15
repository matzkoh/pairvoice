---
name: api
description: pairvoice の常駐サーバーを操作するときに使う。任意の文の読み上げ・合成、声やスタイルの選択・作成、ミュート・停止、読み辞書やプロンプトの読み書き、読み上げの記録・レビューの参照。要約の改善は tune。
---

# pairvoice の API を叩く

常駐サーバーは `http://127.0.0.1:17495` で待ち受け、API の仕様は `GET /openapi.json` が配る。道筋・引数・応答の形・エラーはこの仕様に従い、叩く前に該当箇所を読む。冒頭の `info.description` に全体の約束事（ミュート中の挙動、`voice` と `style` の選び方など）がある。

```bash
# 道筋と要約の一覧
curl -sf http://127.0.0.1:17495/openapi.json |
  jq -r '.paths | to_entries[] | .key as $p | .value | to_entries[] | "\(.key|ascii_upcase) \($p)\t\(.value.summary)"'

# 1つの道筋の詳細と、参照している型
curl -sf http://127.0.0.1:17495/openapi.json | jq '.paths["/api/speak"], .components.schemas.SpeakRequest'
```

応答が無ければ `pairvoice status` で常駐サーバーの状態を見る。
