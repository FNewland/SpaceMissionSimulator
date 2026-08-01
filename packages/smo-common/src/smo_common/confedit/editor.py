"""ConfigEditor — the apply pipeline and everything the API serves.

stage → validate (schema → plausibility → cross-file) → diff preview →
atomic write (``os.replace`` under a process-wide lock) → history commit
with ``SMO-*`` trailers → append-only audit line.

Safety (brief 06 §B):

* must-not-edit files (classification ``editable=False``) are rejected here,
  server-side, for every mutating operation;
* an ``error``-severity issue blocks apply unless ``force=True`` **and** a
  reason is given (both recorded);
* a revert is a *new* revision (never a history rewrite), so a bad revert
  is itself revertible;
* every response carries the file's restart scope — an edit never affects
  the running simulation and the caller must be able to say so.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from .classification import GROUP_ORDER, classify
from .diffing import semantic_diff, unified_text_diff
from .formgen import form_spec_for, schema_model_for, unknown_key_report
from .history import HistoryBackend, select_backend
from .validation import run_crosschecks, validate_file

__all__ = ["ConfigEditor", "ConfigEditError"]


class ConfigEditError(Exception):
    """A rejected mutating operation. ``status`` suggests the HTTP code."""

    def __init__(self, msg: str, status: int = 400,
                 issues: Optional[list[dict]] = None):
        super().__init__(msg)
        self.status = status
        self.issues = issues or []


_TEXT_SUFFIXES = {".yaml", ".yml", ".md"}


class ConfigEditor:
    def __init__(self, config_dir: str | Path,
                 history_dir: str | Path | None = None,
                 backend: Optional[HistoryBackend] = None,
                 backend_kind: Optional[str] = None):
        self.config_dir = Path(config_dir).resolve()
        if not self.config_dir.is_dir():
            # Guard against mock/absent config dirs (e.g. test harnesses passing
            # a MagicMock engine) silently creating history repos in the cwd.
            raise ConfigEditError(
                f"config_dir does not exist: {self.config_dir}", 500)
        if history_dir is None:
            # workspace/config-history next to configs/ (i.e. repo root)
            history_dir = self.config_dir.parent.parent / "workspace" / "config-history"
        self.history_dir = Path(history_dir)
        self._lock = threading.Lock()
        self._backend = backend or select_backend(
            self.history_dir, self.config_dir, prefer=backend_kind)

    # ── path safety ─────────────────────────────────────────────────

    def _resolve(self, relpath: str) -> tuple[str, Path]:
        raw = str(relpath or "").replace("\\", "/").strip()
        if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            raise ConfigEditError(
                f"config path must be relative: {relpath!r}", 400)
        rp = raw
        if not rp or "\x00" in rp or ".." in rp.split("/"):
            raise ConfigEditError(f"illegal config path: {relpath!r}", 400)
        abs_path = (self.config_dir / rp)
        try:
            abs_path.resolve().relative_to(self.config_dir)
        except ValueError:
            raise ConfigEditError(
                f"path escapes the config tree: {relpath!r}", 400) from None
        return rp, abs_path

    # ── read side ───────────────────────────────────────────────────

    @property
    def backend(self) -> HistoryBackend:
        return self._backend

    def list_files(self) -> dict[str, Any]:
        files = []
        for p in sorted(self.config_dir.rglob("*")):
            if not p.is_file() or p.suffix not in {".yaml", ".yml", ".md", ".xml"}:
                continue
            rp = p.relative_to(self.config_dir).as_posix()
            cls = classify(rp)
            files.append({
                "path": rp,
                "size_bytes": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
                "group": cls.group,
                "label": cls.label,
                "editable": cls.editable,
                "restart_scope": list(cls.restart_scope),
                "interface_defining": cls.interface_defining,
                "note": cls.note,
                "has_schema": schema_model_for(rp) is not None,
            })
        return {
            "config_dir": str(self.config_dir),
            "backend": self._backend.kind,
            "groups": [{"key": k, "label": l} for k, l in GROUP_ORDER],
            "files": files,
        }

    def get_file(self, relpath: str) -> dict[str, Any]:
        rp, abs_path = self._resolve(relpath)
        if not abs_path.is_file():
            raise ConfigEditError(f"no such config file: {rp}", 404)
        cls = classify(rp)
        text = abs_path.read_text(errors="replace")
        out: dict[str, Any] = {
            "path": rp,
            "text": text,
            "editable": cls.editable,
            "group": cls.group,
            "restart_scope": list(cls.restart_scope),
            "interface_defining": cls.interface_defining,
            "note": cls.note,
            "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
        }
        if abs_path.suffix in (".yaml", ".yml"):
            try:
                data = yaml.safe_load(text)
                out["unknown_keys"] = unknown_key_report(rp, data)
            except yaml.YAMLError as e:
                out["unknown_keys"] = []
                out["parse_error"] = str(e)
            out["form_spec"] = form_spec_for(rp)
        return out

    def validate(self, relpath: str, text: str) -> list[dict]:
        rp, _ = self._resolve(relpath)
        return validate_file(self.config_dir, rp, text)

    def diff(self, relpath: str, text: str) -> dict[str, Any]:
        rp, abs_path = self._resolve(relpath)
        before = abs_path.read_text(errors="replace") if abs_path.is_file() else ""
        cls = classify(rp)
        out = {
            "path": rp,
            "text_diff": unified_text_diff(before, text, rp),
            "restart_scope": list(cls.restart_scope),
        }
        if rp.endswith((".yaml", ".yml")):
            try:
                out["semantic_diff"] = semantic_diff(
                    yaml.safe_load(before) if before else None,
                    yaml.safe_load(text))
            except yaml.YAMLError as e:
                out["semantic_diff"] = []
                out["semantic_error"] = str(e)
        return out

    def crosschecks(self) -> list[dict]:
        return run_crosschecks(self.config_dir)

    # ── mutating side ───────────────────────────────────────────────

    def _require_editable(self, rp: str) -> None:
        cls = classify(rp)
        if not cls.editable:
            raise ConfigEditError(
                f"{rp} must not be edited: {cls.note or cls.label}", 403)

    def _audit(self, entry: dict) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now(timezone.utc)
                 .isoformat(timespec="seconds"), **entry}
        with open(self.history_dir / "audit.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _atomic_write(self, abs_path: Path, text: str) -> None:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(abs_path.parent),
                                   prefix=f".{abs_path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.replace(tmp, abs_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _trailers(self, actor: str, role: str, reason: str,
                  restart_scope: list[str], issues: list[dict],
                  overridden: bool) -> dict[str, str]:
        n_err = sum(1 for i in issues if i["severity"] == "error")
        n_warn = sum(1 for i in issues if i["severity"] == "warning")
        t = {
            "Actor": actor or "unknown",
            "Role": role or "instructor",
            "Reason": reason or "-",
            "CrossCheck": f"{n_warn} warning {n_err} error",
            "RestartScope": ",".join(restart_scope) or "none",
        }
        if overridden:
            t["Override"] = "errors overridden by instructor"
        return t

    def apply(self, relpath: str, text: str, *, actor: str = "instructor",
              role: str = "instructor", message: str = "",
              reason: str = "", force: bool = False) -> dict[str, Any]:
        """The full apply pipeline for one file."""
        rp, abs_path = self._resolve(relpath)
        self._require_editable(rp)
        issues = validate_file(self.config_dir, rp, text)
        errors = [i for i in issues if i["severity"] == "error"]
        if errors and not (force and reason):
            raise ConfigEditError(
                "validation errors block apply (set force=true and give a "
                "reason to override)", 422, issues)
        cls = classify(rp)
        with self._lock:
            before = abs_path.read_text(errors="replace") \
                if abs_path.is_file() else ""
            self._atomic_write(abs_path, text)
            trailers = self._trailers(actor, role, reason,
                                      list(cls.restart_scope), issues,
                                      overridden=bool(errors))
            rev = self._backend.commit(
                message or f"edit {rp}", trailers, paths=[rp])
            self._audit({
                "actor": actor, "role": role, "action": "apply",
                "paths": [rp], "revision": rev, "message": message,
                "reason": reason,
                "issues_summary": trailers["CrossCheck"],
                "restart_scope": list(cls.restart_scope),
                "overridden": bool(errors),
            })
        return {
            "revision": rev,
            "path": rp,
            "issues": issues,
            "restart_required": list(cls.restart_scope),
            "text_diff": unified_text_diff(before, text, rp),
            "backend": self._backend.kind,
        }

    def history(self, relpath: Optional[str] = None,
                limit: int = 50) -> list[dict]:
        rp = self._resolve(relpath)[0] if relpath else None
        return self._backend.log(rp, limit)

    def file_at(self, rev: str, relpath: str) -> Optional[str]:
        rp, _ = self._resolve(relpath)
        return self._backend.show(rev, rp)

    def diff_revisions(self, rev_a: str, rev_b: str,
                       relpath: Optional[str] = None) -> str:
        rp = self._resolve(relpath)[0] if relpath else None
        return self._backend.diff(rev_a, rev_b, rp)

    def revert(self, rev: str, relpath: str, *, actor: str = "instructor",
               role: str = "instructor", reason: str = "") -> dict[str, Any]:
        """Restore ``relpath`` from ``rev`` as a NEW revision."""
        rp, abs_path = self._resolve(relpath)
        self._require_editable(rp)
        content = self._backend.show(rev, rp)
        if content is None:
            raise ConfigEditError(
                f"{rp} does not exist at revision {rev}", 404)
        cls = classify(rp)
        with self._lock:
            self._atomic_write(abs_path, content)
            trailers = self._trailers(actor, role,
                                      reason or f"revert to {rev[:12]}",
                                      list(cls.restart_scope), [], False)
            new_rev = self._backend.commit(
                f"revert {rp} to {rev[:12]}", trailers, paths=[rp])
            self._audit({
                "actor": actor, "role": role, "action": "revert",
                "paths": [rp], "revision": new_rev, "reverted_to": rev,
                "reason": reason,
                "restart_scope": list(cls.restart_scope),
            })
        return {"revision": new_rev, "path": rp, "reverted_to": rev,
                "restart_required": list(cls.restart_scope)}

    # ── baselines ───────────────────────────────────────────────────

    def list_baselines(self) -> list[dict]:
        return self._backend.list_baselines()

    def create_baseline(self, name: str, description: str = "",
                        actor: str = "instructor") -> dict[str, Any]:
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ConfigEditError(
                "baseline name must be alphanumeric/-/_", 400)
        with self._lock:
            rev = self._backend.commit(
                f"baseline {name}",
                self._trailers(actor, "instructor",
                               f"create baseline {name}", [], [], False))
            self._backend.tag_baseline(name, description, rev)
            self._audit({"actor": actor, "action": "baseline_create",
                         "name": name, "revision": rev,
                         "description": description})
        return {"name": name, "revision": rev}

    def apply_baseline(self, name: str, *, actor: str = "instructor",
                       reason: str = "") -> dict[str, Any]:
        rev = self._backend.baseline_rev(name)
        if not rev:
            raise ConfigEditError(f"no baseline named {name!r}", 404)
        # restore every file that exists at the baseline revision
        with self._lock:
            paths = [f["path"] for f in self.list_files()["files"]
                     if classify(f["path"]).editable]
            restorable = [p for p in paths
                          if self._backend.show(rev, p) is not None]
            self._backend.restore(rev, restorable)
            trailers = self._trailers(actor, "instructor",
                                      reason or f"apply baseline {name}",
                                      ["simulator", "mcs", "planner"], [],
                                      False)
            new_rev = self._backend.commit(
                f"apply baseline {name}", trailers)
            self._audit({"actor": actor, "action": "baseline_apply",
                         "name": name, "revision": new_rev,
                         "baseline_revision": rev, "reason": reason})
        return {"name": name, "revision": new_rev, "baseline_revision": rev,
                "files_restored": restorable,
                "restart_required": ["simulator", "mcs", "planner"]}
