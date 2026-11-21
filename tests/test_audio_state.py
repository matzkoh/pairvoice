from pairvoice.audio_state import sample_processes


def test_sample_processes_returns_well_formed_tuples():
    """実機の CoreAudio を呼ぶ。列挙結果の中身は環境によるので形だけ検証する。"""
    processes = sample_processes()

    assert isinstance(processes, tuple)
    for process in processes:
        assert isinstance(process.pid, int)
        assert isinstance(process.name, str)
        assert isinstance(process.input_running, bool)
        assert isinstance(process.output_running, bool)
