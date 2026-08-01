"""Report generator library (Phase F, brief 06 §A).

A synthetic dump with KNOWN content drives every numeric assertion:

* SID 1 HK at 1 Hz for 120 s with a 40-sample hole (i = 40…79), so the
  completeness analysis must find exactly one gap of 41 s;
* ``eps.bus_voltage`` (0x0105) held at 29.8 V — above the 29.5 V red_high
  band in mcs/limits.yaml — for the first 30 samples, so ``ool_red_s`` must
  be exactly 30 s;
* one severity-3 event with aux text "SAFE MODE ENTRY" (the historical
  ``tools/tm_report.py`` parse prefixed the severity/CUC bytes to it);
* an S1 acceptance+completion pair and an S1 acceptance-failure — all with
  CUC 0, exercising the zero-CUC interpolation policy;
* AOS/LOS events so pass resolution works from the stream;
* a dump FILENAME timestamped 1999 while the packet CUCs say 2026 — period
  selection must be CUC-based and never read the filename.
"""
from __future__ import annotations

import struct
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

from smo_common.protocol.ecss_packet import datetime_to_cuc  # noqa: E402
from smo_common.telemetry.tm_builder import TMBuilder  # noqa: E402
from smo_common.reporting import (  # noqa: E402
    Catalogs,
    TelemetryDataset,
    generate_report,
    list_templates,
    load_spec,
    validate_spec,
)
from smo_common.reporting.analysis import (  # noqa: E402
    commands_view,
    completeness,
    param_stats,
)
from smo_common.reporting.periods import Period, resolve_period  # noqa: E402
from smo_common.reporting.spec import PeriodSpec, SpecError  # noqa: E402

CONFIG_DIR = REPO / "configs" / "eosat1"
T0 = datetime(2026, 3, 10, 4, 0, 0)  # packets say 2026…
T0_CUC = datetime_to_cuc(T0)
#: …while the archive FILENAME says 1999 (wall clock under acceleration)
DUMP_NAME = "dump_sid01_19990101T000000Z.bin"

PID_BUS_V = 0x0105
RED_HIGH_BUS_V = 29.5  # mcs/limits.yaml band for 0x0105


def _sid1_structure() -> list[tuple[int, str, float]]:
    hk = yaml.safe_load((CONFIG_DIR / "telemetry" / "hk_structures.yaml")
                        .read_text())
    sid1 = next(s for s in hk["structures"] if s["sid"] == 1)
    out = []
    for p in sid1["parameters"]:
        pid = p["param_id"]
        pid = pid if isinstance(pid, int) else int(str(pid), 0)
        out.append((pid, p.get("pack_format", "H"),
                    float(p.get("scale", 1))))
    return out


def _build_dump(dump_dir: Path) -> Path:
    cur = {"t": T0_CUC}
    b = TMBuilder(apid=1, time_source=lambda: cur["t"])
    sid1 = _sid1_structure()
    pkts: list[bytes] = []

    for i in range(120):
        if 40 <= i < 80:
            continue  # the 40-sample hole
        cur["t"] = T0_CUC + i
        params = {
            0x0100: 27.0, 0x0101: 80.0, 0x0102: 15.0,
            PID_BUS_V: 29.8 if i < 30 else 27.5,   # 30 s above red_high
            0x0108: 1.0 if i >= 100 else 0.0,       # eclipse at the end
        }
        pkts.append(b.build_hk_packet(1, params, sid1))

    cur["t"] = T0_CUC + 5
    pkts.append(b.build_event_packet(0x0001, 1, "Svalbard", {}))       # AOS
    cur["t"] = T0_CUC + 10
    pkts.append(b.build_event_packet(0x0102, 3, "SAFE MODE ENTRY", {}))
    cur["t"] = T0_CUC + 110
    pkts.append(b.build_event_packet(0x0002, 1, "Svalbard", {}))       # LOS

    # S1 packets carry no CUC (tm_builder default 0) — placed by file order.
    pkts.append(b.build_verification_acceptance(10, 5))
    pkts.append(b.build_verification_completion(10, 5))
    pkts.append(b.build_verification_failure(10, 6, 0x0007,
                                             "TM rate is forced"))

    dump_dir.mkdir(parents=True, exist_ok=True)
    path = dump_dir / DUMP_NAME
    with path.open("wb") as f:
        for p in pkts:
            f.write(struct.pack(">I", len(p)))
            f.write(p)
    return path


@pytest.fixture(scope="module")
def catalogs() -> Catalogs:
    return Catalogs.load(CONFIG_DIR)


@pytest.fixture()
def dump_dir(tmp_path) -> Path:
    _build_dump(tmp_path / "dumps")
    return tmp_path / "dumps"


@pytest.fixture()
def dataset(dump_dir, catalogs) -> TelemetryDataset:
    return TelemetryDataset.from_dumps(
        sorted(dump_dir.glob("*.bin")), catalogs)


def _abs_period(start_s: int, end_s: int) -> Period:
    return Period(T0_CUC + start_s, T0_CUC + end_s, "absolute", "test")


# ── templates ────────────────────────────────────────────────────────────

def test_three_shipped_templates_validate(catalogs):
    templates = list_templates(CONFIG_DIR)
    ids = {t.get("id") for t in templates}
    assert {"pass_report", "daily_ops_review",
            "anomaly_investigation"} <= ids
    for name in ("pass_report", "daily_ops_review", "anomaly_investigation"):
        spec = load_spec(CONFIG_DIR / "reports" / f"{name}.yaml")
        assert validate_spec(spec, catalogs) == []


def test_unknown_spec_key_fails_naming_the_path():
    with pytest.raises(SpecError) as ei:
        load_spec({"schema_version": 1,
                   "report": {"id": "x", "title": "X", "bogus_key": 1}})
    assert any("bogus_key" in e["path"] for e in ei.value.errors)


def test_unknown_parameter_is_an_error_naming_it(catalogs):
    spec = load_spec({
        "schema_version": 1,
        "report": {"id": "x", "title": "X"},
        "parameter_sets": {"s": ["eps.definitely_not_a_parameter"]},
    })
    errors = validate_spec(spec, catalogs)
    assert any("eps.definitely_not_a_parameter" in e["msg"] for e in errors)


# ── decode ───────────────────────────────────────────────────────────────

def test_event_decodes_with_exact_aux_text(dataset):
    ev = [e for e in dataset.events if e["event_id"] == 0x0102]
    assert len(ev) == 1
    # the historical tm_report.py parse yielded a byte-prefixed string here
    assert ev[0]["aux_text"] == "SAFE MODE ENTRY"
    assert ev[0]["severity"] == 3
    assert ev[0]["t"] == T0_CUC + 10


def test_hk_series_decoded_with_scaling(dataset):
    pts = dataset.series_for(PID_BUS_V)
    assert len(pts) == 80  # 120 - 40 missing
    assert pts[0][1] == pytest.approx(29.8, abs=0.02)
    assert pts[-1][1] == pytest.approx(27.5, abs=0.02)


# ── zero-CUC placement ───────────────────────────────────────────────────

def test_zero_cuc_verifications_interpolated_within_span(dataset):
    assert len(dataset.verifications) == 3
    for v in dataset.verifications:
        assert v["time_estimated"] is True
        assert dataset.first_cuc <= v["t"] <= dataset.last_cuc
    assert dataset.zero_cuc_placed == 3


def test_zero_cuc_exclude_policy(dump_dir, catalogs):
    ds = TelemetryDataset.from_dumps(sorted(dump_dir.glob("*.bin")),
                                     catalogs, zero_cuc_policy="exclude")
    assert ds.verifications == []
    assert ds.zero_cuc_excluded == 3


# ── stats / limits ───────────────────────────────────────────────────────

def test_stats_numeric_and_ool_red_time(dataset, catalogs):
    st = param_stats(dataset, PID_BUS_V, _abs_period(0, 120), catalogs)
    assert st["count"] == 80
    assert st["min"] == pytest.approx(27.5, abs=0.02)
    assert st["max"] == pytest.approx(29.8, abs=0.02)
    expected_mean = (30 * 29.8 + 50 * 27.5) / 80
    assert st["mean"] == pytest.approx(expected_mean, abs=0.05)
    # 30 samples above red_high 29.5 at 1.0 s cadence
    assert st["ool_red_s"] == pytest.approx(30.0)
    assert st["ool_yellow_s"] >= st["ool_red_s"]
    assert st["first"] == pytest.approx(29.8, abs=0.02)
    assert st["last"] == pytest.approx(27.5, abs=0.02)


# ── completeness / gaps ──────────────────────────────────────────────────

def test_exactly_one_gap_of_41_seconds(dataset, catalogs):
    rows = completeness(dataset, _abs_period(0, 120), catalogs, [1],
                        gap_threshold_multiple=3.0)
    r = next(x for x in rows if x["sid"] == 1)
    assert len(r["gaps"]) == 1
    # samples at t+39 then t+80 → 41 s hole
    assert r["gaps"][0]["duration_s"] == pytest.approx(41.0)
    assert r["longest_gap_s"] == pytest.approx(41.0)
    assert r["received"] == 80
    assert r["missing"] == 40
    assert r["missing_pct"] == pytest.approx(100.0 * 40 / 120, abs=0.1)


# ── command history ──────────────────────────────────────────────────────

def test_command_history_outcomes(dataset):
    rows = commands_view(dataset, _abs_period(0, 120))
    by_seq = {r["seq"]: r for r in rows}
    assert by_seq[5]["outcome"] == "COMPLETED"
    assert by_seq[5]["accept"] == "OK"
    assert by_seq[6]["outcome"] == "REJECTED"
    assert by_seq[6]["error_code"] == 0x0007
    assert by_seq[6]["apid"] == 10


# ── period resolution is CUC-based ───────────────────────────────────────

def test_period_selection_is_cuc_based_not_filename(dataset, catalogs):
    """Filename says 1999; packets say 2026. CUC wins."""
    # An absolute window over the 2026 CUC span selects the data…
    p26 = PeriodSpec.model_validate({
        "kind": "absolute",
        "absolute": {"start": "2026-03-10T04:00:00Z",
                     "end": "2026-03-10T04:02:00Z"}})
    period = resolve_period(p26, dataset, catalogs)
    st = param_stats(dataset, PID_BUS_V, period, catalogs)
    assert st is not None and st["count"] == 80
    # …and a window over the filename's 1999 timestamp selects nothing.
    p99 = PeriodSpec.model_validate({
        "kind": "absolute",
        "absolute": {"start": "1999-01-01T00:00:00Z",
                     "end": "1999-01-01T01:00:00Z"}})
    period99 = resolve_period(p99, dataset, catalogs)
    assert param_stats(dataset, PID_BUS_V, period99, catalogs) is None


def test_pass_period_resolves_from_aos_los_events(dataset, catalogs):
    spec = PeriodSpec.model_validate({
        "kind": "pass",
        "pass": {"select": "latest", "pad_before_s": 0, "pad_after_s": 0}})
    period = resolve_period(spec, dataset, catalogs)
    assert period.kind == "pass"
    assert period.meta.get("pass_source") == "events"
    assert period.start_cuc == pytest.approx(T0_CUC + 5)
    assert period.end_cuc == pytest.approx(T0_CUC + 110)
    assert period.meta.get("station") == "Svalbard"


# ── end-to-end HTML ──────────────────────────────────────────────────────

def test_generate_report_end_to_end(dump_dir, tmp_path):
    meta = generate_report(
        "pass_report", config_dir=CONFIG_DIR, dump_dir=dump_dir,
        period_override={"kind": "absolute",
                         "start": "2026-03-10T04:00:00Z",
                         "end": "2026-03-10T04:02:00Z"},
        out_dir=tmp_path / "reports")
    html = Path(meta["path"]).read_text()
    # content markers
    assert "EOSAT-1 Pass Report" in html
    assert "SAFE MODE ENTRY" in html
    assert "REJECTED" in html
    assert "Telemetry received vs expected" in html
    # plots embedded, fully self-contained (no network references)
    assert "data:image/png;base64," in html
    assert "http://" not in html and "https://" not in html
    assert "<script src=" not in html
    # zero-CUC provenance note
    assert "no on-board timestamp" in html
    assert meta["packets"] == 86
    assert meta["zero_cuc_placed"] == 3


def test_sections_never_silently_empty(catalogs, tmp_path):
    """A report over an empty store states 'no data', never blank."""
    empty = tmp_path / "empty-dumps"
    empty.mkdir()
    meta = generate_report(
        "daily_ops_review", config_dir=CONFIG_DIR, dump_dir=empty,
        out_dir=tmp_path / "reports")
    html = Path(meta["path"]).read_text()
    assert ("No data" in html or "no data" in html
            or "No housekeeping packets" in html)
