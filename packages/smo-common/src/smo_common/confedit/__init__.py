"""SMO Common — safe configuration editor (Phase F, brief 06 §B).

A library the simulator's instructor server (port 8080 — the instructor's
door) hosts as a full CRUD config editor, and the MCS/planner host as
**read-only** browsers (students can read configuration and its history —
that is teaching — but no student port ever gets a write route).

Components::

    classification.py  editability classes per config file (encoded as data)
    formgen.py         schema-driven form model from the pydantic schemas
    validation.py      pipeline: YAML → pydantic schema → plausibility →
                       cross-file checks (stations geometry, RF frequencies,
                       EPS shed ladder, HK wire)
    diffing.py         unified diff of before/after YAML text
    history.py         GitHistoryBackend (shadow repo at
                       workspace/config-history/, work-tree configs/) +
                       SnapshotHistoryBackend fallback, one interface
    editor.py          ConfigEditor — stage → validate → diff → atomic
                       write → commit(+SMO-* trailers) → audit
    api.py             register_config_routes(app, …, readonly=…)

Safety rules implemented here (brief 06 §B):

* must-not-edit files (dead config, generated XTCE/SMDL) are rejected
  server-side, never merely hidden in a UI;
* every response reports the file's restart scope — a config edit never
  affects a running simulation and the UI must say so;
* every revision carries machine-readable ``SMO-*`` trailers (actor, role,
  reason, restart scope) so the audit survives a bare clone of the shadow
  history;
* writes are atomic (``os.replace``) under a process-wide lock;
* named baselines are tags, per-exercise variants are branches (git
  backend) with snapshot-backend equivalents.
"""
from .classification import classify, classification_table  # noqa: F401
from .editor import ConfigEditor  # noqa: F401
from .history import (  # noqa: F401
    GitHistoryBackend,
    HistoryBackend,
    SnapshotHistoryBackend,
    select_backend,
)
from .api import register_config_routes  # noqa: F401
