#!/usr/bin/env python3
"""Launch the Nexus Studio Engine login UI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from nexus_engine.app import main

if __name__ == "__main__":
    main()
