"""Tests for the MCP server (Layer 6).

Guarded by ``pytest.importorskip('mcp')`` so the core env (without the ``agent``
extra) skips cleanly. We assert only the tool *registry* — the four read-only
tools register under their documented names with descriptions — so no network or
stdio transport is needed. ``build_server`` itself imports cleanly without the
extra because ``mcp`` is imported lazily.
"""

from __future__ import annotations

import asyncio

import pytest

from relief_probe.agent.mcp_server import TOOL_NAMES, build_server


def test_module_imports_without_the_agent_extra():
    """The module + symbols import even when `mcp` is absent (lazy import)."""
    assert TOOL_NAMES == ("score_loan", "peer_compare", "check_fraud_case", "investigate")
    assert callable(build_server)


def test_server_exposes_the_four_readonly_tools():
    pytest.importorskip("mcp")
    server = build_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == set(TOOL_NAMES)
    assert names == {"score_loan", "peer_compare", "check_fraud_case", "investigate"}


def test_every_exposed_tool_is_documented():
    pytest.importorskip("mcp")
    server = build_server()
    tools = asyncio.run(server.list_tools())
    # Every tool carries a docstring-derived description (grounding/UX).
    assert all((t.description or "").strip() for t in tools)


def test_build_server_without_extra_raises_clearly(monkeypatch):
    """If `mcp` is missing, build_server gives an actionable error, not ImportError."""
    import builtins

    real_import = builtins.__import__

    def _block(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("No module named 'mcp'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block)
    with pytest.raises(RuntimeError, match="agent.*extra"):
        build_server()
