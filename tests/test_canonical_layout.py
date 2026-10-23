import os
from pathlib import Path

from nexus_engine import paths
from nexus_engine.services import database, omnivoice_client


def test_canonical_paths_resolve_from_flat_repository_layout():
    repository_root = Path(__file__).resolve().parents[1]

    assert paths.PROJECT_ROOT == repository_root
    assert database.PROJECT_ROOT == repository_root
    assert database.DB_PATH == Path(os.environ["NEXUS_DB_PATH"])
    assert omnivoice_client.OMNIVOICE_ROOT == repository_root.parent / "omnivoice"
