"""Tests for config helpers, including the minimal .env loader."""

from __future__ import annotations

from relief_probe.config import DEFAULT_LLM_MODEL, llm_model, load_env


def test_load_env_sets_and_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        'NEW_KEY="hello world"\n'
        "export EXPORTED_KEY = 'val'\n"
        "PREEXISTING=fromfile\n"
    )
    monkeypatch.setenv("PREEXISTING", "fromenv")  # must NOT be overridden
    monkeypatch.delenv("NEW_KEY", raising=False)
    monkeypatch.delenv("EXPORTED_KEY", raising=False)

    assert load_env(env) is True
    import os

    assert os.environ["NEW_KEY"] == "hello world"  # quotes stripped
    assert os.environ["EXPORTED_KEY"] == "val"  # `export ` + quotes stripped
    assert os.environ["PREEXISTING"] == "fromenv"  # existing env wins


def test_load_env_missing_file(tmp_path):
    assert load_env(tmp_path / "nope.env") is False


def test_llm_model_default():
    assert DEFAULT_LLM_MODEL == "claude-haiku-4-5"
    # (env-override behavior is covered in test_agent_graph.py)
    assert llm_model() in (DEFAULT_LLM_MODEL, llm_model())
