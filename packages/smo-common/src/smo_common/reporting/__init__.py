"""SMO Common — Delayed-TM report generator (Phase F, brief 06 §A).

Turns archived S15 delayed-TM dumps (``workspace/dumps/dump_*.bin``) into
configurable, self-contained HTML reports driven by YAML report definitions
("templates"). Three templates ship with the mission config under
``configs/<mission>/reports/``:

* ``pass_report``          — the artefact written after every contact
* ``daily_ops_review``     — the Daily Operations Report
* ``anomaly_investigation``— the Anomaly Report

Two facts about the dump store shape the whole design (brief 06):

* The archive is the **complete tape** — the engine writes every packet the
  spacecraft played back, including those the (simulated) link dropped. The
  MCS live view is lossy. Archive-minus-MCS is therefore the operationally
  interesting "what the link cost you" number.
* The dump **filename timestamp is wall clock** while packet CUC is **sim
  time**; under time acceleration the two axes are unrelated. All period
  selection here is CUC-based; the filename is never used to place data on
  the report's time axis.

Library layout::

    spec.py        report-definition schema (pydantic v2, extra="forbid")
    catalogs.py    mission catalogue loading (parameters/HK/events/limits)
    decode.py      the one correct decoder set (reuses protocol.pus_services)
    dataset.py     TelemetryDataset — one decode pass over N dumps
    periods.py     period resolution (pass/orbit/day/absolute/relative/dump)
    analysis.py    stats, OOL time, gap/completeness, commands, breakdowns
    render_html.py self-contained HTML renderer (matplotlib Agg → base64 PNG)
    plotting.py    the ONE matplotlib/backend probe + its operator diagnostic
    generate.py    orchestration — generate_report()
    api.py         register_report_routes() for the MCS / viewer / simulator
"""
from .plotting import (  # noqa: F401
    PlottingStatus,
    PlottingUnavailable,
    log_plotting_status,
    plotting_status,
    require_plotting,
    unavailable_message,
)
from .spec import ReportSpec, load_spec, validate_spec  # noqa: F401
from .catalogs import Catalogs  # noqa: F401
from .dataset import TelemetryDataset  # noqa: F401
from .generate import generate_report, list_templates  # noqa: F401
from .api import register_report_routes  # noqa: F401
