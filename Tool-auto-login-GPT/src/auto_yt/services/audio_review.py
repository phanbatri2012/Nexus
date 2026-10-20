"""Backward-compatible imports for the script quality gate."""

from auto_yt.services.script_quality import (
    DUPLICATE_PARAGRAPH_MIN_WORDS,
    EDITORIAL_PREFIXES,
    MIN_AUDIO_SCRIPT_WORDS,
    MIN_SECTION_WORDS,
    QUALITY_GATE_VERSION,
    REQUIRED_AUDIO_SECTIONS,
    SECTION_PATTERN,
    STRUCTURAL_NARRATIVE_LABEL_PATTERN,
    ScriptQualityGate,
    audit_script_for_audio,
    extract_audio_sections,
    get_audio_script,
    get_audio_script_hash,
    looks_like_editorial_artifact,
)

__all__ = [
    "DUPLICATE_PARAGRAPH_MIN_WORDS",
    "EDITORIAL_PREFIXES",
    "MIN_AUDIO_SCRIPT_WORDS",
    "MIN_SECTION_WORDS",
    "QUALITY_GATE_VERSION",
    "REQUIRED_AUDIO_SECTIONS",
    "SECTION_PATTERN",
    "STRUCTURAL_NARRATIVE_LABEL_PATTERN",
    "ScriptQualityGate",
    "audit_script_for_audio",
    "extract_audio_sections",
    "get_audio_script",
    "get_audio_script_hash",
    "looks_like_editorial_artifact",
]
