"""音声の入出力の判定（AudioProbe.sample）の性質。

CoreAudio の列挙は差し替え、最大3つのプロセスを選ばせる。名前は大文字小文字だけが違うものと
同じ名前のヘルパーを含む候補から選び、除外の設定も同じ候補（と空）から選ぶ。
"""

from pairvoice.audio_state import AudioProbe, AudioProcess

OWN_PID = 42
NAMES = ("zoom.us", "ZOOM.US", "Safari")
# 除外の設定。0 は除外なし
IGNORES = ((), ("zoom.us",), ("Zoom.US",), ("safari",))


def _sample(processes: list[AudioProcess], ignore_output: int = 0, ignore_input: int = 0):
    probe = AudioProbe(
        ignore_output=IGNORES[ignore_output],
        ignore_input=IGNORES[ignore_input],
        sampler=lambda: tuple(processes),
        own_pid=OWN_PID,
    )
    return probe.sample()


def one_process_counts_by_its_own_settings(
    name: int, own: bool, inputs: bool, outputs: bool, ignore_output: int, ignore_input: int
) -> bool:
    """1つのプロセスは、入力中で入力の除外に無ければマイクに、出力中で自分でも出力の除外にも
    無ければ再生に数える。除外は大文字小文字を問わず、入力と出力の除外は互いに効かない。

    pre: 0 <= name < 3 and 0 <= ignore_output < 4 and 0 <= ignore_input < 4
    post: __return__
    """
    process = AudioProcess(
        pid=OWN_PID if own else 7, name=NAMES[name], input_running=inputs, output_running=outputs
    )
    lowered = NAMES[name].lower()
    microphone = inputs and lowered not in {n.lower() for n in IGNORES[ignore_input]}
    output = outputs and not own and lowered not in {n.lower() for n in IGNORES[ignore_output]}
    activity = _sample([process], ignore_output, ignore_input)
    return activity.microphone == ((NAMES[name],) if microphone else ()) and activity.output == (
        (NAMES[name],) if output else ()
    )


def names_are_listed_once_in_order_of_appearance(
    names: tuple[int, int, int], inputs: tuple[bool, bool, bool]
) -> bool:
    """数えるプロセスの名前は、初めて出た順に1度ずつ並べる（同じ名前のヘルパーを重ねない）。

    pre: all(0 <= n < 3 for n in names)
    post: __return__
    """
    processes = [
        AudioProcess(pid=100 + i, name=NAMES[n], input_running=inputs[i], output_running=True)
        for i, n in enumerate(names)
    ]
    activity = _sample(processes)
    every = [NAMES[n] for n in names]
    listening = [NAMES[n] for n, listens in zip(names, inputs, strict=True) if listens]
    return activity.output == tuple(dict.fromkeys(every)) and activity.microphone == tuple(
        dict.fromkeys(listening)
    )
