# -*- coding: utf-8 -*-
"""
delivery_intelligence.py (Shim)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Backward compatibility shim for delivery_intelligence.py.
Re-exports the main intelligence runner.
"""

from intelligence.runner import run_delivery_intelligence

__all__ = ["run_delivery_intelligence"]
