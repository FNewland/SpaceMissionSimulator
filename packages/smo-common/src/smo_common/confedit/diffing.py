"""Diff preview — unified diff of the YAML text before/after.

Edits are text-based end to end (the client edits the raw YAML), so the
many explanatory comment blocks in these files — which *are* the teaching
material — survive untouched unless the instructor deletes them, and the
diff shows exactly what will change on disk.
"""
from __future__ import annotations

import difflib

__all__ = ["unified_text_diff", "semantic_diff"]


def unified_text_diff(before: str, after: str, relpath: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{relpath}", tofile=f"b/{relpath}"))


def _flatten(data, prefix=""):
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out.update(_flatten(v, f"{prefix}{i}."))
    else:
        out[prefix.rstrip(".")] = data
    return out


def semantic_diff(before_data, after_data) -> list[dict]:
    """Leaf-level changes between two parsed YAML documents.

    Catches the inverse failure too: a comment-only edit produces an empty
    semantic diff, which the UI reports as "changes nothing at runtime".
    """
    a = _flatten(before_data)
    b = _flatten(after_data)
    out = []
    for key in sorted(set(a) | set(b)):
        if key not in a:
            out.append({"path": key, "change": "added", "after": b[key]})
        elif key not in b:
            out.append({"path": key, "change": "removed", "before": a[key]})
        elif a[key] != b[key]:
            out.append({"path": key, "change": "modified",
                        "before": a[key], "after": b[key]})
    return out
