"""Test isolation for modules that initialize SQLite during import."""

import os
import tempfile
from pathlib import Path


_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="autoyt-tests-"))
os.environ["AUTO_YT_DB_PATH"] = str(_TEST_DATA_DIR / "database.db")
