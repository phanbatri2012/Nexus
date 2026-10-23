"""One-release compatibility alias for the former ``auto_yt`` package."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import warnings


LEGACY_PACKAGE = "auto_yt"
CANONICAL_PACKAGE = "nexus_engine"


class _LegacyModuleLoader(importlib.abc.Loader):
    def __init__(self, alias_name: str, canonical_name: str) -> None:
        self.alias_name = alias_name
        self.canonical_name = canonical_name
        self._metadata: tuple[object, object, object] | None = None

    def create_module(self, spec):
        module = importlib.import_module(self.canonical_name)
        self._metadata = (
            getattr(module, "__spec__", None),
            getattr(module, "__loader__", None),
            getattr(module, "__package__", None),
        )
        sys.modules[self.alias_name] = module
        return module

    def exec_module(self, module) -> None:
        sys.modules[self.alias_name] = module
        if self._metadata is not None:
            module.__spec__, module.__loader__, module.__package__ = self._metadata


class _LegacyModuleFinder(importlib.abc.MetaPathFinder):
    _nexus_legacy_alias_finder = True

    def find_spec(self, fullname: str, path=None, target=None):
        prefix = f"{LEGACY_PACKAGE}."
        if not fullname.startswith(prefix):
            return None
        canonical_name = f"{CANONICAL_PACKAGE}.{fullname[len(prefix):]}"
        canonical_spec = importlib.util.find_spec(canonical_name)
        if canonical_spec is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            _LegacyModuleLoader(fullname, canonical_name),
            is_package=canonical_spec.submodule_search_locations is not None,
        )


if not any(
    getattr(finder, "_nexus_legacy_alias_finder", False)
    for finder in sys.meta_path
):
    sys.meta_path.insert(0, _LegacyModuleFinder())

warnings.warn(
    "The 'auto_yt' package is deprecated; import 'nexus_engine' instead.",
    DeprecationWarning,
    stacklevel=2,
)

_canonical_package = importlib.import_module(CANONICAL_PACKAGE)
sys.modules[LEGACY_PACKAGE] = _canonical_package

