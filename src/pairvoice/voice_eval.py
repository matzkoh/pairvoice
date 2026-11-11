"""参照音声の候補を、合成した音の崩れで比べる。

似ているかは測らない（話者ベクトルは録り方の差も拾い、物差しにならない）。測るのは
聞き取った文と原稿のずれ（読み崩れ）と、同じ文の合成どうしの長さのずれ（無音の居座り・
途中で止まる）だけで、似ているかは人が聴いて決める。
"""

from __future__ import annotations

import statistics
import unicodedata
from dataclasses import dataclass

# 読ませる文。声を作るときに読ませる REFERENCE_TEXTS とは別の文にする（参照音声に入っている
# 文を読ませると、崩れにくく見える）。説明・問いかけ・カタカナ語を一通り含め、数と同音で表記の揺れる語は入れない
# （聞き取りの表記が揺れ、どの候補にも同じずれが乗る）
EVAL_TEXTS = (
    "設定ファイルを読み直したので、次の読み上げから新しい声に切り替わります。",
    "最後のテストだけが落ちています。ログを見てから、もう一度流してもいいですか？",
    "キャッシュを消してビルドし直したら、動作がとても軽くなりました。",
)
DEFAULT_SEEDS = (1, 2)
# 同じ文の合成の長さの中央値からこの比を外れたら、崩れとみなす
LONG_RATIO = 1.4
SHORT_RATIO = 0.7


@dataclass(frozen=True)
class Sample:
    candidate: int
    text: str
    seed: int
    audio: str
    duration: float
    transcript: str


def normalize(text: str) -> str:
    """読みの比べ方をそろえる。全角半角とカタカナ・ひらがなの違い、記号と空白は数えない。"""
    # カタカナ（ァ〜ヶ）はひらがなに寄せる
    return "".join(
        chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c
        for c in unicodedata.normalize("NFKC", text)
        if unicodedata.category(c)[0] not in "PSZC"
    )


def cer(reference: str, hypothesis: str) -> float:
    """文字誤り率。原稿の文字数で割った編集距離（読み飛ばし・言い直しで 1 を超えうる）。"""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1] / len(ref)


def score(samples: list[Sample], count: int) -> dict:
    """候補ごとの成績と、良い順の番号。

    長さは、同じ文を全候補・全種で合成した長さの中央値と比べる。崩れた合成の数が少ない順、
    同じなら読み崩れの平均が小さい順に並べる。
    """
    medians = {
        text: statistics.median(s.duration for s in samples if s.text == text)
        for text in {s.text for s in samples}
    }
    results = []
    for index in range(count):
        mine = [s for s in samples if s.candidate == index]
        ratios = [s.duration / medians[s.text] if medians[s.text] > 0 else 1.0 for s in mine]
        errors = [round(cer(s.text, s.transcript), 3) for s in mine]
        broken = [not SHORT_RATIO <= ratio <= LONG_RATIO for ratio in ratios]
        results.append(
            {
                "index": index,
                "cer": round(statistics.fmean(errors), 3),
                "worst_cer": max(errors),
                "broken": sum(broken),
                "samples": [
                    {
                        "text": s.text,
                        "seed": s.seed,
                        "audio": s.audio,
                        "duration": round(s.duration, 2),
                        "duration_ratio": round(ratio, 2),
                        "transcript": s.transcript,
                        "cer": error,
                        "broken": flag,
                    }
                    for s, ratio, error, flag in zip(mine, ratios, errors, broken, strict=True)
                ],
            }
        )
    ranking = sorted(range(count), key=lambda i: (results[i]["broken"], results[i]["cer"]))
    return {"ranking": ranking, "candidates": results}
