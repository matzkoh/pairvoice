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

## こもった声を直す

声の一覧（`GET /profiles`）で `muffled` が true の声は、上の帯域が欠けて低音が膨らんだ、こもった音で読む。その声1つを重み1で `mix` に渡し、`unmuffle`（0〜3、目安は 2.5）を付けて `POST /synthesize` で試聴し、同じ値を `POST /profiles/mix` の `unmuffle` に渡すと、直した声を別の声として保存する。元の声は書き換えない。どれだけで澄んで聞こえるかは声によるので、利用者に前後を聴いて選んでもらう。強いほど声も少し変わる。

```bash
base="$(pairvoice url)/api"
jq -n --arg id "$id" '{name: "直した声", voices: [{voice: $id, weight: 1}], unmuffle: 2.5}' |
  curl -sf -X POST "$base/profiles/mix" -H 'content-type: application/json' -d @-
```

## 素材から声を作る

`POST /profiles/fold` で素材を畳み込む。素材全体の平均の声になり、区間を選ばないので候補を作って聴き比べる必要がない。素材は長いほど声がぶれない。受け付けたらすぐ返るので、`GET /profiles/fold/{job_id}` を間を空けて問い合わせる。
声の名前 `name` は multipart の本文ではなくクエリに付ける（無いと 422）。途中でやめるなら `DELETE /profiles/fold/{job_id}` で止める（返った時点で列が空き、作業用の素材も消える）。常駐サーバーごと再起動して止めない。

```bash
base="$(pairvoice url)/api"
job=$(curl -sf -X POST "$base/profiles/fold?name=$(jq -rn --arg v '配信の声' '$v|@uri')" \
  -F files=@配信1.m4a -F files=@配信2.m4a | jq -r .job_id)
curl -sf "$base/profiles/fold/$job" | jq '{status, stage, progress}'
curl -sf -X DELETE "$base/profiles/fold/$job"
```
