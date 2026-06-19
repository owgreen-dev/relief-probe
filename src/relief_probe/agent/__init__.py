"""Layer 6 — the tool-grounded loan investigator and MCP surface.

Everything in this package is **read-only** over the warehouse. The default
investigation path is pure Python and fully testable without the optional
``agent`` extra (langgraph / langchain-anthropic / mcp), which is imported
lazily only on the LLM / MCP code paths.

A high composite score or a fired detector is a *statistical lead for review*,
not evidence of fraud — see ``RESPONSIBLE_USE.md``.
"""

from __future__ import annotations
