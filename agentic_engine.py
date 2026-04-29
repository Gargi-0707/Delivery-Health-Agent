# -*- coding: utf-8 -*-
"""
agentic_engine.py (Shim)
~~~~~~~~~~~~~~~~~~~~~~~~
Backward compatibility shim for agentic_engine.py.
Re-exports memory and planner functions from the engine/ package.
"""

from engine.memory import _load_memory_state, _save_memory_state, _memory_file_path
from engine.planner import run_agentic_planner
from engine.catalog import ACTION_CATALOG

# Re-exporting for any downstream modules that still import from agentic_engine
__all__ = [
    "_load_memory_state",
    "_save_memory_state",
    "_memory_file_path",
    "run_agentic_planner",
    "ACTION_CATALOG",
]
