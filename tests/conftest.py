import pytest

from pairvoice import player
from tests.fakes import InstantOutput


@pytest.fixture(autouse=True)
def silent_stream_output(monkeypatch):
    """合成しながら鳴らす経路が、テストで本物の音を出さないようにする。"""
    monkeypatch.setattr(player, "_EngineOutput", InstantOutput)
