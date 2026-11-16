import numpy as np

from pairvoice.postprocess import StreamCut, cut_lead, normalize, trim

SR = 1000


def _signal(*parts):
    """(秒, 振幅) の並びから、音（正弦波）と無音をつないだ波形を作る。"""
    chunks = []
    for seconds, amplitude in parts:
        t = np.arange(int(seconds * SR)) / SR
        chunks.append(amplitude * np.sin(2 * np.pi * 50 * t))
    return np.concatenate(chunks).astype(np.float32)


def test_drops_short_burst_after_a_gap():
    samples = _signal((2.0, 0.5), (0.3, 0.0), (0.2, 0.5))

    trimmed = trim(samples, SR)

    # 本文の終わり + 余韻まで
    assert len(trimmed) == int(2.1 * SR)


def test_drops_up_to_two_bursts():
    samples = _signal((2.0, 0.5), (0.3, 0.0), (0.2, 0.4), (0.3, 0.0), (0.1, 0.5))

    assert len(trim(samples, SR)) == int(2.1 * SR)


def test_keeps_long_final_phrase():
    samples = _signal((2.0, 0.5), (0.3, 0.0), (0.8, 0.5))

    assert len(trim(samples, SR)) == len(samples)


def test_keeps_ending_without_a_gap():
    # 間が短ければ本文の語尾とみなす
    samples = _signal((2.0, 0.5), (0.05, 0.0), (0.2, 0.5))

    assert len(trim(samples, SR)) == len(samples)


def test_drops_short_click_before_speech():
    samples = _signal((0.05, 0.3), (0.3, 0.0), (2.0, 0.5))

    trimmed = trim(samples, SR)

    # 頭の無音は 0.05 秒だけ残す
    assert len(trimmed) == int(2.05 * SR)


def test_keeps_short_first_word_before_a_pause():
    # 「次に、」のような語頭は頭のゴミより長い
    samples = _signal((0.3, 0.5), (0.3, 0.0), (2.0, 0.5))

    assert len(trim(samples, SR)) == len(samples)


def test_trims_trailing_silence_and_fades_out():
    samples = _signal((1.0, 0.5), (1.0, 0.0))

    trimmed = trim(samples, SR)

    assert len(trimmed) == int(1.1 * SR)
    assert trimmed[-1] == 0.0


def test_single_burst_is_kept():
    samples = _signal((0.3, 0.5))

    assert len(trim(samples, SR)) == len(samples)


def test_empty_input():
    assert len(trim(np.zeros(0, dtype=np.float32), SR)) == 0


def _voiced_rms_db(samples):
    voiced = samples[np.abs(samples) > 0]
    return 20 * np.log10(np.sqrt(np.mean(voiced**2)))


def test_normalize_brings_quiet_voice_to_target():
    samples = _signal((1.0, 0.1))

    normalized = normalize(samples, SR)

    assert abs(_voiced_rms_db(normalized) - (-16.0)) < 0.5


def test_normalize_keeps_peak_below_ceiling():
    # 声の RMS は低いが一瞬だけ大きい音があると、RMS ではなくピークで止まる
    samples = _signal((1.0, 0.05))
    samples[100] = 0.99

    normalized = normalize(samples, SR)

    assert np.abs(normalized).max() <= 10 ** (-1.0 / 20) + 1e-6


def test_normalize_caps_gain_for_near_silence():
    samples = _signal((1.0, 0.001))

    gain = np.abs(normalize(samples, SR)).max() / np.abs(samples).max()

    assert abs(20 * np.log10(gain) - 12.0) < 0.01


def test_normalize_leaves_silence_alone():
    samples = np.zeros(SR, dtype=np.float32)

    assert np.array_equal(normalize(samples, SR), samples)


def test_cut_lead_keeps_body_from_just_before_it():
    samples = _signal((0.3, 0.5), (0.3, 0.0), (2.0, 0.5))

    body = cut_lead(samples, SR)

    # 本文の手前 0.05 秒（弱い語頭の分）から残す
    assert body is not None
    assert len(body) == int(2.05 * SR)


def test_cut_lead_gives_up_without_a_gap():
    assert cut_lead(_signal((2.0, 0.5)), SR) is None


def test_cut_lead_measures_from_where_speech_starts():
    # 話し始めまでに 1 秒の無音があっても、前置きの後の長い間で切れる
    samples = _signal((1.0, 0.0), (0.6, 0.5), (0.9, 0.0), (2.0, 0.5))

    body = cut_lead(samples, SR)

    assert body is not None
    assert len(body) == int(2.05 * SR)


def test_cut_lead_does_not_cut_at_a_pause_inside_the_body():
    # 前置きが本文とつながって読まれると、最初のかたまりが前置きより長くなる。
    # その後の本文の中の間では切らない
    samples = _signal((1.6, 0.5), (0.3, 0.0), (1.0, 0.5))

    assert cut_lead(samples, SR) is None


# --- 合成しながら鳴らす（StreamCut）---


def _stream(samples, piece_seconds, *, lead):
    """samples を piece_seconds ずつ StreamCut に足し、(切り出し役, 出てきた波形の列) を返す。"""
    cutter = StreamCut(SR, lead=lead)
    size = int(piece_seconds * SR)
    released = []
    for start in range(0, len(samples), size):
        piece = samples[start : start + size]
        released.append(cutter.push(piece, max(0, len(samples) - start - size)))
    return cutter, released


def _joined(released):
    return np.concatenate([piece for piece in released if piece is not None])


def _whole(samples):
    """全体を見て、前置きを切り、頭と末尾のゴミを落とした波形。"""
    body = cut_lead(samples, SR)
    assert body is not None
    return trim(body, SR)


def test_stream_cut_hands_out_the_body_before_everything_arrives():
    # 前置き、間、本文、間、末尾のゴミ
    samples = _signal((0.3, 0.5), (0.3, 0.0), (4.0, 0.5), (0.3, 0.0), (0.2, 0.4))

    cutter, released = _stream(samples, 1.0, lead=True)

    # 最初の1秒で前置きの切り方が決まり、本文の頭から出し始める
    assert cutter.cut == int(0.55 * SR)
    assert len(released[0]) == int(0.45 * SR)
    # 出した音をつなぐと、全体を見て切ったのと同じになる（末尾のゴミも落ちている）
    np.testing.assert_array_equal(_joined(released), _whole(samples))


def test_stream_cut_holds_the_tail_until_everything_arrives():
    samples = _signal((0.3, 0.5), (0.3, 0.0), (4.0, 0.5), (0.3, 0.0), (0.2, 0.4))

    _, released = _stream(samples, 1.0, lead=True)

    # 末尾のゴミかどうかは全部届くまで分からないので、終わりの 1.5 秒は最後まで出さない
    before_end = sum(len(piece) for piece in released[:-1] if piece is not None)
    assert int(0.55 * SR) + before_end == len(samples) - int(1.5 * SR)
    assert released[4] is None
    assert released[-1] is not None


def test_stream_cut_waits_while_the_cut_depends_on_how_loud_the_rest_gets():
    # 前置きの後の小さな音は、いまの最大音量に比べれば「音がある」が、この先にもっと大きい音が
    # 来れば無音の扱いになり、切る位置が変わる。どちらにも転びうるうちは出さない
    quiet = 0.5 * 10 ** (-32 / 20)
    samples = _signal((0.3, 0.5), (0.3, 0.0), (0.2, quiet), (0.2, 0.0), (4.0, 0.5))

    cutter, released = _stream(samples, 1.0, lead=True)

    assert cutter.cut is None
    assert all(piece is None for piece in released)


def test_stream_cut_decides_once_the_loud_part_has_arrived():
    # 同じ小さな音でも、大きい本文がもう届いていれば、無音の扱いで決まる
    quiet = 1.0 * 10 ** (-45 / 20)
    samples = _signal((0.3, 0.5), (0.3, 0.0), (0.2, quiet), (0.2, 0.0), (4.0, 1.0))

    cutter, released = _stream(samples, 1.5, lead=True)

    assert cutter.cut == int(0.95 * SR)
    np.testing.assert_array_equal(_joined(released), _whole(samples))


def test_stream_cut_without_a_lead_drops_the_head_click_like_trim():
    # 頭のプツッという音を落としたときは、trim と同じくフェードインする
    samples = _signal((0.05, 0.3), (0.3, 0.0), (4.0, 0.5))

    cutter, released = _stream(samples, 1.0, lead=False)

    assert cutter.cut == 0
    np.testing.assert_array_equal(_joined(released), trim(samples, SR))


def test_stream_cut_continues_the_fade_in_across_pieces():
    # 終わりの 1.5 秒を出さないので、短い音では最初に出せる分がフェードインより短くなる
    samples = _signal((0.05, 0.3), (0.3, 0.0), (1.47, 0.5))
    assert int(0.3 * SR) < len(samples) - int(1.5 * SR) < int(0.33 * SR)

    _, released = _stream(samples, 0.6, lead=False)

    first = next(piece for piece in released if piece is not None)
    assert len(first) < int(0.03 * SR)
    np.testing.assert_array_equal(_joined(released), trim(samples, SR))


def test_stream_cut_hands_out_nothing_when_the_cut_is_never_decided_early():
    samples = _signal((0.3, 0.5), (0.3, 0.0), (1.0, 0.5))

    cutter, released = _stream(samples, 5.0, lead=True)

    # 全部が一度に届いた。呼び手が全体を見て切る
    assert cutter.cut is None
    assert released == [None]


def test_stream_cut_does_not_decide_a_lead_cut_before_the_body_starts():
    samples = _signal((0.3, 0.5), (0.3, 0.0), (4.0, 0.5))

    _, released = _stream(samples, 0.5, lead=True)

    # 最初の 0.5 秒は前置きと間だけで、本文がまだ見えていない
    assert released[0] is None
    assert released[1] is not None


def test_stream_cut_starts_at_the_earliest_onset_it_might_need():
    # 本文の語頭の弱い 10ms は、この先にもっと大きい音が来れば「無音」の扱いになる。
    # どちらでも 10ms しか変わらないので待たずに決め、語頭を削らない方（手前）から出す
    soft = 0.5 * 10 ** (-33 / 20)
    samples = _signal((0.3, 0.5), (0.3, 0.0), (0.01, soft), (4.0, 0.5))

    cutter, released = _stream(samples, 1.0, lead=True)

    assert cutter.cut == int(0.55 * SR)
    assert released[0] is not None
    np.testing.assert_array_equal(_joined(released), _whole(samples))
