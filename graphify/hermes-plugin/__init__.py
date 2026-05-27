"""Graphify Hermes plugin — auto-cron + hippocampal drain.

Distributed by `graphify sleep install` to ~/.hermes/plugins/memory/graphify/.
NOT a memory provider in the traditional sense (graphify is a knowledge graph,
not a key-value store) — but uses the MemoryProvider ABC because that's the
plugin lifecycle Hermes exposes.

See README.md for the hippocampus/neocortex mental model.
"""
from .version import __version__
from .provider import GraphifyMemoryProvider

__all__ = ["__version__", "GraphifyMemoryProvider"]
