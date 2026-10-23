import json
import os
from pathlib import Path
import subprocess
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"


def _read_environment_contract(overrides: dict[str, str]) -> dict:
    environment = os.environ.copy()
    for name in (
        "NEXUS_DB_PATH",
        "AUTO_YT_DB_PATH",
        "NEXUS_MAX_TTS_CHARS",
        "AUTO_YT_MAX_TTS_CHARS",
        "NEXUS_OMNIVOICE_ROOT",
        "AUTO_YT_OMNIVOICE_ROOT",
    ):
        environment.pop(name, None)
    environment.update(overrides)
    environment["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(SOURCE_ROOT), environment.get("PYTHONPATH", ""))
        if value
    )
    code = """
import json
from nexus_engine.services import database, omnivoice_client, tts_service
print(json.dumps({
    'db_path': str(database.DB_PATH),
    'max_tts_chars': tts_service.MAX_TTS_TOTAL_CHARS,
    'omnivoice_root': str(omnivoice_client.OMNIVOICE_ROOT),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(result.stdout)


def test_nexus_environment_names_take_precedence_over_legacy_names(tmp_path):
    canonical_db = tmp_path / "canonical.db"
    legacy_db = tmp_path / "legacy.db"
    canonical_omni = tmp_path / "canonical-omni"
    legacy_omni = tmp_path / "legacy-omni"

    values = _read_environment_contract(
        {
            "NEXUS_DB_PATH": str(canonical_db),
            "AUTO_YT_DB_PATH": str(legacy_db),
            "NEXUS_MAX_TTS_CHARS": "12345",
            "AUTO_YT_MAX_TTS_CHARS": "54321",
            "NEXUS_OMNIVOICE_ROOT": str(canonical_omni),
            "AUTO_YT_OMNIVOICE_ROOT": str(legacy_omni),
        }
    )

    assert values == {
        "db_path": str(canonical_db),
        "max_tts_chars": 12345,
        "omnivoice_root": str(canonical_omni),
    }


def test_legacy_environment_names_remain_one_release_fallbacks(tmp_path):
    legacy_db = tmp_path / "legacy.db"
    legacy_omni = tmp_path / "legacy-omni"

    values = _read_environment_contract(
        {
            "AUTO_YT_DB_PATH": str(legacy_db),
            "AUTO_YT_MAX_TTS_CHARS": "54321",
            "AUTO_YT_OMNIVOICE_ROOT": str(legacy_omni),
        }
    )

    assert values == {
        "db_path": str(legacy_db),
        "max_tts_chars": 54321,
        "omnivoice_root": str(legacy_omni),
    }
