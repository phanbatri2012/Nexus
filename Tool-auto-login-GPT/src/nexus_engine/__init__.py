"""Nexus Studio Engine - Core Package.

Canonical namespace for Nexus Studio Engine.
Provides complete, bidirectional compatibility between 'nexus_engine' and 'auto_yt'.
"""
from pathlib import Path
import sys
import auto_yt

__path__ = list(auto_yt.__path__)

for attr in dir(auto_yt):
    if not attr.startswith("__"):
        globals()[attr] = getattr(auto_yt, attr)
