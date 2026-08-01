"""Config history backends — shadow git, with a snapshot fallback.

``GitHistoryBackend`` keeps a **shadow repository** whose git dir lives at
``workspace/config-history/.git`` and whose work-tree is ``configs/`` — real
diffs, history, tags and blame without touching the source repo or its
dirty tree (the source repo's own ``.git`` is never consulted). Named
baselines are annotated tags ``baseline/<name>``; per-exercise variants are
branches ``exercise/<name>``.

``SnapshotHistoryBackend`` (auto-selected when ``git`` is not on PATH —
the Windows lab installer does not ship it) stores full copies under
``workspace/config-history/snapshots/<rev>/`` with an ``index.json``
journal. Same interface, degraded diff/blame.

Every revision carries machine-readable trailers::

    SMO-Actor: <who>   SMO-Role: <role>   SMO-Reason: <why>
    SMO-RestartScope: simulator,mcs   SMO-CrossCheck: 1 warning 0 error

so the audit survives a bare clone of the history (git) or a zip of the
snapshot dir.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = ["HistoryBackend", "GitHistoryBackend", "SnapshotHistoryBackend",
           "select_backend"]

#: files inside configs/ that the history tracks
_TRACK_GLOBS = ("**/*.yaml", "**/*.yml", "**/*.md", "**/*.xml")


def _tracked_files(configs_dir: Path) -> list[Path]:
    out: set[Path] = set()
    for g in _TRACK_GLOBS:
        out.update(p for p in configs_dir.glob(g) if p.is_file())
    return sorted(out)


class HistoryBackend(ABC):
    """One interface for both backends. Revisions are opaque string ids."""

    def __init__(self, history_dir: Path, configs_dir: Path):
        self.history_dir = Path(history_dir)
        self.configs_dir = Path(configs_dir)

    # -- lifecycle ---------------------------------------------------
    @abstractmethod
    def init(self) -> None:
        """Create the store and record revision 0 (initial baseline)."""

    # -- revisions ---------------------------------------------------
    @abstractmethod
    def commit(self, message: str, trailers: dict[str, str],
               paths: Optional[list[str]] = None) -> str:
        """Record the current work-tree state; returns the revision id."""

    @abstractmethod
    def log(self, path: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Newest first: {rev, ts, message, trailers, files}."""

    @abstractmethod
    def show(self, rev: str, path: str) -> Optional[str]:
        """File content at a revision (None if absent there)."""

    @abstractmethod
    def restore(self, rev: str, paths: list[str]) -> None:
        """Copy files from ``rev`` into the work-tree (no commit — the
        caller commits, so a revert is itself a new, revertible revision)."""

    @abstractmethod
    def diff(self, rev_a: str, rev_b: str,
             path: Optional[str] = None) -> str:
        """Unified diff between two revisions."""

    # -- baselines ---------------------------------------------------
    @abstractmethod
    def tag_baseline(self, name: str, description: str, rev: str) -> None: ...

    @abstractmethod
    def list_baselines(self) -> list[dict]: ...

    @abstractmethod
    def baseline_rev(self, name: str) -> Optional[str]: ...

    @property
    @abstractmethod
    def kind(self) -> str: ...


# ── git backend ──────────────────────────────────────────────────────────

class GitHistoryBackend(HistoryBackend):
    """Shadow git repo: ``--git-dir=<history>/.git --work-tree=<configs>``."""

    @property
    def kind(self) -> str:
        return "git"

    def _git(self, *args: str, check: bool = True,
             input_: Optional[str] = None) -> subprocess.CompletedProcess:
        cmd = ["git", "--git-dir", str(self.history_dir / ".git"),
               "--work-tree", str(self.configs_dir), *args]
        return subprocess.run(cmd, capture_output=True, text=True,
                              check=check, input=input_)

    def init(self) -> None:
        gitdir = self.history_dir / ".git"
        if (gitdir / "HEAD").exists():
            return
        self.history_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--quiet", str(self.history_dir)],
                       capture_output=True, text=True, check=True)
        # identity local to the shadow repo, never the user's global config
        self._git("config", "user.name", "SMO Config Editor")
        self._git("config", "user.email", "config-editor@smo.local")
        self._git("config", "core.worktree", str(self.configs_dir))
        # keep the history dir's own tree empty (work-tree is configs/)
        for stray in self.history_dir.iterdir():
            if stray.name != ".git":
                pass  # leave anything the operator placed there alone
        self._git("add", "-A")
        self._git("commit", "--allow-empty", "-m",
                  "revision 0 — initial baseline\n\n"
                  "SMO-Actor: system\nSMO-Role: system\n"
                  "SMO-Reason: config-history initialisation")

    def commit(self, message: str, trailers: dict[str, str],
               paths: Optional[list[str]] = None) -> str:
        if paths:
            for p in paths:
                self._git("add", "--", p)
        else:
            self._git("add", "-A")
        trailer_block = "\n".join(f"SMO-{k}: {v}" for k, v in trailers.items())
        full = f"{message.strip()}\n\n{trailer_block}\n" if trailer_block \
            else message.strip()
        res = self._git("commit", "--allow-empty", "-m", full, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"git commit failed: {res.stderr.strip()}")
        return self._git("rev-parse", "HEAD").stdout.strip()

    def log(self, path: Optional[str] = None, limit: int = 50) -> list[dict]:
        args = ["log", f"--max-count={limit}",
                "--pretty=format:%H%x1f%aI%x1f%B%x1e"]
        if path:
            args += ["--follow", "--", path]
        res = self._git(*args, check=False)
        if res.returncode != 0:
            return []
        out = []
        for chunk in res.stdout.split("\x1e"):
            if not chunk.strip():
                continue
            head, _, tail = chunk.partition("\x1f")
            ts, _, body = tail.partition("\x1f")
            rev = head.strip()
            trailers = {}
            message_lines = []
            for l in body.splitlines():
                if l.startswith("SMO-") and ": " in l:
                    k, v = l.split(": ", 1)
                    trailers[k[4:]] = v
                else:
                    message_lines.append(l)
            while message_lines and not message_lines[-1].strip():
                message_lines.pop()
            files_res = self._git("diff-tree", "--no-commit-id",
                                  "--name-only", "-r", rev, check=False)
            files = [l for l in (files_res.stdout or "").splitlines()
                     if l.strip()]
            out.append({
                "rev": rev,
                "ts": ts,
                "message": "\n".join(message_lines).strip(),
                "trailers": trailers,
                "files": files,
            })
        return out

    def show(self, rev: str, path: str) -> Optional[str]:
        res = self._git("show", f"{rev}:{path}", check=False)
        return res.stdout if res.returncode == 0 else None

    def restore(self, rev: str, paths: list[str]) -> None:
        for p in paths:
            res = self._git("checkout", rev, "--", p, check=False)
            if res.returncode != 0:
                raise RuntimeError(
                    f"git checkout {rev} -- {p} failed: {res.stderr.strip()}")

    def diff(self, rev_a: str, rev_b: str,
             path: Optional[str] = None) -> str:
        args = ["diff", f"{rev_a}..{rev_b}"]
        if path:
            args += ["--", path]
        res = self._git(*args, check=False)
        return res.stdout

    def tag_baseline(self, name: str, description: str, rev: str) -> None:
        res = self._git("tag", "-a", f"baseline/{name}", rev, "-m",
                        description or name, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"git tag failed: {res.stderr.strip()}")
        self._save_baseline_meta(name, description, rev)

    def list_baselines(self) -> list[dict]:
        meta = self._baseline_meta()
        res = self._git("tag", "-l", "baseline/*", check=False)
        out = []
        for tag in (res.stdout or "").split():
            name = tag[len("baseline/"):]
            rev = self._git("rev-list", "-n", "1", tag,
                            check=False).stdout.strip()
            m = meta.get(name, {})
            out.append({"name": name, "revision": rev,
                        "description": m.get("description", ""),
                        "created": m.get("created", "")})
        return out

    def baseline_rev(self, name: str) -> Optional[str]:
        res = self._git("rev-list", "-n", "1", f"baseline/{name}",
                        check=False)
        rev = res.stdout.strip()
        return rev if res.returncode == 0 and rev else None

    def _baseline_meta(self) -> dict:
        path = self.history_dir / "baselines.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_baseline_meta(self, name, description, rev) -> None:
        meta = self._baseline_meta()
        meta[name] = {"description": description, "revision": rev,
                      "created": datetime.now(timezone.utc)
                      .isoformat(timespec="seconds")}
        (self.history_dir / "baselines.json").write_text(
            json.dumps(meta, indent=1))


# ── snapshot backend ─────────────────────────────────────────────────────

class SnapshotHistoryBackend(HistoryBackend):
    """Full-copy snapshots + JSON journal. No external dependencies."""

    @property
    def kind(self) -> str:
        return "snapshot"

    @property
    def _snap_root(self) -> Path:
        return self.history_dir / "snapshots"

    @property
    def _index_path(self) -> Path:
        return self.history_dir / "index.json"

    def _index(self) -> list[dict]:
        try:
            return json.loads(self._index_path.read_text())
        except (OSError, ValueError):
            return []

    def _write_index(self, entries: list[dict]) -> None:
        self._index_path.write_text(json.dumps(entries, indent=1))

    def init(self) -> None:
        if self._index_path.exists():
            return
        self._snap_root.mkdir(parents=True, exist_ok=True)
        self._write_index([])
        self.commit("revision 0 — initial baseline",
                    {"Actor": "system", "Role": "system",
                     "Reason": "config-history initialisation"})

    def _snapshot_tree(self, rev: str) -> None:
        dest = self._snap_root / rev
        for f in _tracked_files(self.configs_dir):
            rel = f.relative_to(self.configs_dir)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)

    def commit(self, message: str, trailers: dict[str, str],
               paths: Optional[list[str]] = None) -> str:
        entries = self._index()
        rev = f"r{len(entries):04d}-{int(time.time())}"
        self._snapshot_tree(rev)
        prev_files = set()
        if entries:
            prev_dir = self._snap_root / entries[-1]["rev"]
            prev_files = {str(p.relative_to(prev_dir))
                          for p in prev_dir.rglob("*") if p.is_file()}
        cur_dir = self._snap_root / rev
        changed = []
        for p in cur_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = str(p.relative_to(cur_dir))
            if rel not in prev_files:
                changed.append(rel)
            else:
                if p.read_bytes() != (self._snap_root / entries[-1]["rev"]
                                      / rel).read_bytes():
                    changed.append(rel)
        entries.append({
            "rev": rev,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message,
            "trailers": dict(trailers),
            # revision 0 records every file as added, so per-path
            # log/revert can always reach the initial baseline
            "files": sorted(changed),
        })
        self._write_index(entries)
        return rev

    def log(self, path: Optional[str] = None, limit: int = 50) -> list[dict]:
        entries = list(reversed(self._index()))
        if path:
            entries = [e for e in entries if path in (e.get("files") or [])]
        return entries[:limit]

    def show(self, rev: str, path: str) -> Optional[str]:
        f = self._snap_root / rev / path
        try:
            return f.read_text()
        except OSError:
            return None

    def restore(self, rev: str, paths: list[str]) -> None:
        for p in paths:
            src = self._snap_root / rev / p
            if not src.is_file():
                raise RuntimeError(f"{p} does not exist at revision {rev}")
            dest = self.configs_dir / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    def diff(self, rev_a: str, rev_b: str,
             path: Optional[str] = None) -> str:
        import difflib
        a_dir = self._snap_root / rev_a
        b_dir = self._snap_root / rev_b
        paths = [path] if path else sorted(
            {str(p.relative_to(d)) for d in (a_dir, b_dir) if d.is_dir()
             for p in d.rglob("*") if p.is_file()})
        chunks = []
        for rel in paths:
            a = self.show(rev_a, rel) or ""
            b = self.show(rev_b, rel) or ""
            if a == b:
                continue
            chunks.append("".join(difflib.unified_diff(
                a.splitlines(keepends=True), b.splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}")))
        return "\n".join(chunks)

    def tag_baseline(self, name: str, description: str, rev: str) -> None:
        path = self.history_dir / "baselines.json"
        try:
            meta = json.loads(path.read_text())
        except (OSError, ValueError):
            meta = {}
        meta[name] = {"revision": rev, "description": description,
                      "created": datetime.now(timezone.utc)
                      .isoformat(timespec="seconds")}
        path.write_text(json.dumps(meta, indent=1))

    def list_baselines(self) -> list[dict]:
        try:
            meta = json.loads((self.history_dir / "baselines.json").read_text())
        except (OSError, ValueError):
            return []
        return [{"name": k, **v} for k, v in sorted(meta.items())]

    def baseline_rev(self, name: str) -> Optional[str]:
        for b in self.list_baselines():
            if b["name"] == name:
                return b["revision"]
        return None


def select_backend(history_dir: Path, configs_dir: Path,
                   prefer: Optional[str] = None) -> HistoryBackend:
    """Git when available (or an existing git history is found), else
    snapshots. ``prefer`` forces one for tests."""
    history_dir = Path(history_dir)
    configs_dir = Path(configs_dir)
    if prefer == "snapshot":
        be: HistoryBackend = SnapshotHistoryBackend(history_dir, configs_dir)
    elif prefer == "git" or (
            shutil.which("git")
            and not (history_dir / "index.json").exists()):
        be = GitHistoryBackend(history_dir, configs_dir)
    else:
        be = SnapshotHistoryBackend(history_dir, configs_dir)
    be.init()
    return be
