"""Know Your Context: deterministic context for AI agents.

A standalone, local-first system built on three pillars — multi-modal
knowledge, deterministic code intelligence, and auditable learning memory.
It never loads model assets, provider SDKs, credentials, embedding/vector
backends, and never registers MCP sampling handlers. See
``docs/adr/0001-standalone-deterministic-monolith.md``.
"""

from __future__ import annotations

__version__ = "0.3.0"

__all__ = ["__version__"]
