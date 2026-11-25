"""文脈つきの雑音除去の範囲と、切り戻す本人の区間を検証する。"""

from pairvoice.fold import _clean_spans, _clean_window


def merged_context_covers_exactly_the_selected_samples(
    a: int, b: int, c: int, d: int, context: int, sample: int
) -> bool:
    """
    pre: 0 <= a < b <= 12 and a <= c < d <= 12
    pre: 0 <= context <= 3 and -2 <= sample <= 14
    post: __return__
    """
    spans = list(_clean_spans([(a, b), (c, d)], 0, 12, context))
    wanted = 0 <= sample < 12 and (
        a - context <= sample < b + context or c - context <= sample < d + context
    )
    coverage = sum(lo <= sample < hi for lo, hi, _ in spans)
    return (
        coverage == int(wanted)
        and [piece for _, _, group in spans for piece in group] == [(a, b), (c, d)]
        and (len(spans) == 1 or spans[0][1] < spans[1][0])
    )


def clean_window_bounds(start: int, stop: int, cursor: int, context: int, limit: int) -> bool:
    """入力は上限内で、採用範囲は必ず前進し、継ぎ目の両側には文脈がある。

    pre: 0 <= start <= cursor < stop
    pre: 0 <= context and 2 * context < limit
    post: __return__
    """
    left, right, end = _clean_window(start, stop, cursor, limit, context)
    return (
        start <= left <= cursor < end <= right <= stop
        and right - left <= limit
        and cursor - left == min(context, cursor - start)
        and (end == right == stop or right - end == context)
    )
