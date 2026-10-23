import os
import sys
import tempfile
from pathlib import Path


_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="nexus-tests-"))
os.environ["NEXUS_DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["NEXUS_DB_PATH"] = str(_TEST_DATA_DIR / "database.db")

SOURCE_ROOT = Path(__file__).resolve().parent / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
