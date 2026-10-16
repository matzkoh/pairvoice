from pairvoice.audio_state import AudioProbe, AudioProcess, sample_processes


def probe_of(*processes, ignore=("afplay",), ignore_input=()):
    return AudioProbe(ignore_output=ignore, ignore_input=ignore_input, sampler=lambda: processes)


def test_microphone_active_when_any_process_uses_input():
    zoom = AudioProcess(pid=1, name="zoom.us", input_running=True, output_running=False)

    assert probe_of(zoom).sample().microphone == ("zoom.us",)
    assert probe_of().sample().microphone == ()


def test_output_active_when_any_process_plays():
    playing = AudioProcess(pid=3, name="Safari", input_running=False, output_running=True)
    silent = AudioProcess(pid=3, name="Safari", input_running=False, output_running=False)

    assert probe_of(playing).sample().output == ("Safari",)
    assert probe_of(silent).sample().output == ()


def test_ignored_processes_do_not_count_as_output():
    afplay = AudioProcess(pid=2, name="afplay", input_running=False, output_running=True)

    assert probe_of(afplay).sample().output == ()


def test_ignore_list_is_case_insensitive():
    afplay = AudioProcess(pid=4, name="AFPLAY", input_running=False, output_running=True)

    assert probe_of(afplay).sample().output == ()
    assert probe_of(afplay, ignore=("AfPlay",)).sample().output == ()


def test_sample_processes_returns_well_formed_tuples():
    """実機の CoreAudio を呼ぶ。列挙結果の中身は環境によるので形だけ検証する。"""
    processes = sample_processes()

    assert isinstance(processes, tuple)
    for process in processes:
        assert isinstance(process.pid, int)
        assert isinstance(process.name, str)
        assert isinstance(process.input_running, bool)
        assert isinstance(process.output_running, bool)


def test_own_process_does_not_count_as_output():
    me = AudioProcess(pid=42, name="python3.12", input_running=False, output_running=True)

    assert AudioProbe(sampler=lambda: (me,), own_pid=42).sample().output == ()
    assert AudioProbe(sampler=lambda: (me,), own_pid=7).sample().output == ("python3.12",)


def test_ignored_processes_do_not_count_as_microphone():
    huddle = AudioProcess(pid=5, name="Slack Helper", input_running=True, output_running=True)

    assert probe_of(huddle, ignore_input=("slack helper",)).sample().microphone == ()
    # 入力の除外は出力に効かない
    assert probe_of(huddle, ignore_input=("Slack Helper",)).sample().output == ("Slack Helper",)


def test_helpers_with_same_name_are_listed_once():
    helpers = [
        AudioProcess(pid=pid, name="Google Chrome Helper", input_running=False, output_running=True)
        for pid in (6, 7)
    ]

    assert probe_of(*helpers).sample().output == ("Google Chrome Helper",)
