import pytest

from pairvoice.config import LLMConfig
from pairvoice.llm import MlxLmBackend


class FakeTokenizer:
    def __init__(self, *, allow_system=True):
        self.allow_system = allow_system
        self.calls = []

    def apply_chat_template(self, messages, add_generation_prompt=True, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if not self.allow_system and any(m["role"] == "system" for m in messages):
            raise ValueError("System role not supported")
        return "PROMPT:" + "|".join(f"{m['role']}={m['content']}" for m in messages)


@pytest.fixture
def backend(monkeypatch):
    backend = MlxLmBackend(LLMConfig(model="fake/llm", max_tokens=32))
    monkeypatch.setattr(backend, "_load_model", lambda: ("MODEL", FakeTokenizer()))
    return backend


def test_preflight_has_nothing_to_check(backend):
    assert backend.preflight() is None


def test_generate_passes_system_and_prompt(backend, monkeypatch):
    captured = {}

    def fake_generate(model, tokenizer, prompt, max_tokens, verbose):
        captured.update(model=model, prompt=prompt, max_tokens=max_tokens, verbose=verbose)
        return "  やった、テスト全部通ったよ。  "

    monkeypatch.setattr("pairvoice.llm.mlx_generate", fake_generate)
    backend.load()

    text = backend.generate(system="ルール", prompt="作業ログ")

    assert text == "やった、テスト全部通ったよ。"
    assert captured["model"] == "MODEL"
    assert captured["max_tokens"] == 32
    assert captured["verbose"] is False
    assert "system=ルール" in captured["prompt"]
    assert "content=作業ログ" not in captured["prompt"]  # role=user=作業ログ の形式
    assert "user=作業ログ" in captured["prompt"]


def test_generate_falls_back_when_system_role_unsupported(monkeypatch):
    backend = MlxLmBackend(LLMConfig(model="fake/llm"))
    tokenizer = FakeTokenizer(allow_system=False)
    monkeypatch.setattr(backend, "_load_model", lambda: ("MODEL", tokenizer))
    monkeypatch.setattr(
        "pairvoice.llm.mlx_generate",
        lambda model, tokenizer, prompt, max_tokens, verbose: prompt,
    )
    backend.load()

    text = backend.generate(system="ルール", prompt="作業ログ")

    assert "system=" not in text
    assert "user=ルール\n\n作業ログ" in text
    assert "ルール" in text
    assert "作業ログ" in text


def test_generate_falls_back_logs_warning(monkeypatch, caplog):
    backend = MlxLmBackend(LLMConfig(model="fake/llm"))
    tokenizer = FakeTokenizer(allow_system=False)
    monkeypatch.setattr(backend, "_load_model", lambda: ("MODEL", tokenizer))
    monkeypatch.setattr(
        "pairvoice.llm.mlx_generate",
        lambda model, tokenizer, prompt, max_tokens, verbose: prompt,
    )
    backend.load()

    with caplog.at_level("WARNING", logger="pairvoice.llm"):
        backend.generate(system="ルール", prompt="作業ログ")

    assert any("falling back to concatenation" in record.message for record in caplog.records)


def test_generate_disables_thinking_on_system_role_path(backend, monkeypatch):
    monkeypatch.setattr(
        "pairvoice.llm.mlx_generate",
        lambda model, tokenizer, prompt, max_tokens, verbose: "ok",
    )
    backend.load()

    backend.generate(system="ルール", prompt="作業ログ")

    tokenizer = backend._tokenizer
    assert len(tokenizer.calls) == 1
    assert tokenizer.calls[0]["kwargs"].get("enable_thinking") is False


def test_generate_disables_thinking_on_fallback_path(monkeypatch):
    backend = MlxLmBackend(LLMConfig(model="fake/llm"))
    tokenizer = FakeTokenizer(allow_system=False)
    monkeypatch.setattr(backend, "_load_model", lambda: ("MODEL", tokenizer))
    monkeypatch.setattr(
        "pairvoice.llm.mlx_generate",
        lambda model, tokenizer, prompt, max_tokens, verbose: prompt,
    )
    backend.load()

    backend.generate(system="ルール", prompt="作業ログ")

    # calls[0]: system ロールを試みて失敗した呼び出し。calls[1]: 連結版フォールバック。
    assert len(tokenizer.calls) == 2
    assert all(call["kwargs"].get("enable_thinking") is False for call in tokenizer.calls)


def test_generate_respects_max_tokens_argument(backend, monkeypatch):
    captured = {}

    def fake_generate(model, tokenizer, prompt, max_tokens, verbose):
        captured["max_tokens"] = max_tokens
        return "ok"

    monkeypatch.setattr("pairvoice.llm.mlx_generate", fake_generate)
    backend.load()

    backend.generate(system="s", prompt="p", max_tokens=8)

    assert captured["max_tokens"] == 8


def test_generate_without_load_raises(backend):
    with pytest.raises(RuntimeError, match="not loaded"):
        backend.generate(system="s", prompt="p")


def test_unload_clears_references_and_cache(backend, monkeypatch):
    cleared = []
    monkeypatch.setattr("pairvoice.llm.mx.clear_cache", lambda: cleared.append(True))
    backend.load()

    backend.unload()

    assert cleared == [True]
    with pytest.raises(RuntimeError, match="not loaded"):
        backend.generate(system="s", prompt="p")
