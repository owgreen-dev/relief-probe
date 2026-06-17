"""DuckDB warehouse: connection + schema."""

from __future__ import annotations

from relief_probe.warehouse.db import connect, init_schema

__all__ = ["connect", "init_schema"]
