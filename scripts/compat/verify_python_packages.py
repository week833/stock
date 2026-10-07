#!/usr/bin/env python3
"""Read-only verification of the direct requirements used by the toolkit.

The verifier deliberately does not invoke pip install/upgrade or modify any
environment.  It checks both the import name and the installed distribution
metadata so a package that is present only under an unrelated alias cannot
silently pass.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import re
import sys
from pathlib import Path


# Distribution names are case-insensitive; the values are the importable
# module names used by Python.  Keep aliases here even when a local
# requirements file does not currently contain every optional package.
IMPORT_ALIASES = {
    "beautifulsoup4": "bs4",
    "python-dotenv": "dotenv",
    "python-dateutil": "dateutil",
    "jinja2": "jinja2",
    "scikit-learn": "sklearn",
    "pillow": "PIL",
    "pyyaml": "yaml",
}

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")


def _requirement_name(line: str) -> str | None:
    """Return a PEP 508 distribution name from one requirements line."""

    stripped = line.split("#", 1)[0].strip()
    if not stripped or stripped.startswith("-"):
        return None
    match = _NAME_RE.match(stripped)
    if not match:
        raise ValueError(f"cannot parse requirement line: {line.strip()}")
    return match.group(1)


def read_requirements(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        name = _requirement_name(raw)
        if name:
            names.append(name)
    if not names:
        raise ValueError(f"no direct requirements found: {path}")
    return names


def check_requirement(distribution_name: str) -> tuple[bool, str, str]:
    """Return (success, import_name, installed_version_or_reason)."""

    import_name = IMPORT_ALIASES.get(distribution_name.lower(), distribution_name)
    try:
        metadata = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return False, import_name, "distribution-not-installed"
    except Exception as exc:  # pragma: no cover - defensive metadata failure
        return False, import_name, f"distribution-error:{type(exc).__name__}"

    try:
        importlib.import_module(import_name)
    except Exception as exc:
        return False, import_name, f"import-error:{type(exc).__name__}"
    return True, import_name, metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify installed stock-toolkit Python requirements")
    parser.add_argument("--requirements", required=True, type=Path)
    args = parser.parse_args(argv)
    # Imports performed by this program must not create __pycache__ files.
    sys.dont_write_bytecode = True

    try:
        names = read_requirements(args.requirements)
    except (OSError, ValueError) as exc:
        print(f"[FAIL] requirements-read-error: {exc}")
        return 2

    failures = 0
    for name in names:
        ok, import_name, value = check_requirement(name)
        if ok:
            print(f"[OK] package={name} import={import_name} version={value}")
        else:
            failures += 1
            print(f"[FAIL] package={name} import={import_name} reason={value}")

    print(f"[SUMMARY] direct_requirements={len(names)} failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
