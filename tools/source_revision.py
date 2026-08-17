"""Deterministic, source-derived revision stamps for generated artefacts.

A generated file's content must be a pure function of its inputs. Stamping a
wall-clock date into one breaks that: the file differs from the committed copy
on every day but the one it was generated on, so the CI byte-diff gates
(``gen_command_reference.py --check``, the byte-stability parametrisation in
``tests/test_content/test_generated_command_reference.py``) fail for reasons
that have nothing to do with drift.

``source_revision`` replaces the date with a short digest of the exact input
files a generator reads, so the provenance stamp changes when — and only
when — the sources change.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parent.parent


def _rel(path: Path) -> str:
    """Repo-relative POSIX path, so the digest does not depend on checkout dir."""
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.name


def source_revision(paths: Iterable[Path | str], length: int = 12) -> str:
    """Return a short hex digest over the named source files.

    The digest covers each file's repo-relative path and its bytes, in sorted
    path order, so it is stable across machines, checkouts and clock settings.
    Missing files contribute their path and an empty body rather than raising,
    so an optional input does not turn into a crash.
    """
    h = hashlib.sha256()
    for path in sorted((Path(p) for p in paths), key=_rel):
        rel = _rel(path)
        body = path.read_bytes() if path.is_file() else b""
        h.update(f"{rel}:{len(body)}:".encode("utf-8"))
        h.update(body)
    return h.hexdigest()[:length]
