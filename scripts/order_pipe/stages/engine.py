"""Lazy import of the reverse-query engine (monolith)."""

from __future__ import annotations


def rq():
    import order_pipe_reverse_query as module  # noqa: WPS433

    return module
