import importlib
import os
from pathlib import Path
import subprocess
import sys


def test_legacy_namespace_resolves_to_canonical_module_objects():
    canonical_paths = importlib.import_module("nexus_engine.paths")
    legacy_paths = importlib.import_module("auto_yt.paths")
    assert legacy_paths is canonical_paths

    canonical_database = importlib.import_module("nexus_engine.services.database")
    legacy_database = importlib.import_module("auto_yt.services.database")
    assert legacy_database is canonical_database

    canonical_main = importlib.import_module("nexus_engine.main")
    legacy_main = importlib.import_module("auto_yt.main")
    assert legacy_main is canonical_main


def test_legacy_first_import_still_reuses_canonical_module_objects():
    code = """
import importlib
for suffix in ('paths', 'services.database', 'main'):
    legacy = importlib.import_module(f'auto_yt.{suffix}')
    canonical = importlib.import_module(f'nexus_engine.{suffix}')
    assert legacy is canonical, suffix
"""
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(source_root), environment.get("PYTHONPATH", ""))
        if value
    )
    subprocess.run([sys.executable, "-c", code], check=True, env=environment)
