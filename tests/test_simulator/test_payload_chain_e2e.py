"""End-to-end, on the REAL engine: the cooler chain and the image chain.

The unit tests in `test_payload_2026_08b.py` pin the models. These pin the
things that were actually broken, which were the *wirings between* them —
every one of them looked correct in isolation:

  * four command paths to one cryocooler, three of which set a request flag
    against a power line nothing switched on (measured pre-fix: node +11.93 C
    and FPA READY = NO after 1200 s, on every path except EPS func 17, with no
    error reported anywhere);
  * S13 sessions that were never bound to a product, served fabricated bytes
    and freed nothing;
  * a Science_Store full of command acknowledgements.

These run the real `SimulationEngine`, real telecommands through the real
`ServiceDispatcher`, and real subsystem ticks.
"""
import struct
import sys
import zlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smo_simulator.engine import SimulationEngine  # noqa: E402


# ── Harness ─────────────────────────────────────────────────────────

@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("SMO_DUMP_DIR", str(tmp_path / "dumps"))
    eng = SimulationEngine(CONFIG_DIR)
    # The phase state machine only ticks payload/tcs/aocs from phase 4; jump
    # to NOMINAL so this is a commissioned spacecraft, not a LEOP one.
    obdh = eng.subsystems.get("obdh")
    if obdh is not None:
        obdh._state.sw_image = 1
    eng._spacecraft_phase = 3
    eng._tick_once(1.0)
    eng._spacecraft_phase = 6
    _s8(eng, 19, bytes([3]))          # EPS_POWER_ON: payload line
    return eng


def _s8(eng, func_id, payload=b""):
    """Dispatch one S8,1 telecommand; return (tm packets, refusal reason).

    `dispatch()` restores its per-command accumulators on exit (it is
    reentrant), so the outcome has to be read off the returned DispatchResult,
    not off the dispatcher.
    """
    out = eng._dispatcher.dispatch(8, 1, bytes([func_id]) + payload)
    return out, (getattr(out, "reason", "") or None)


def _run(eng, seconds, dt=2.0):
    for _ in range(int(seconds / dt)):
        eng._tick_once(dt)


# ── (B) PL-a: every cooler path reaches FPA READY ───────────────────

class TestEveryCoolerPathCools:

    @pytest.mark.parametrize("label,func,arg", [
        ("EPS_FPA_COOLER (func 17)", 17, bytes([1])),
        ("PAYLOAD_SET_MODE STANDBY (func 26)", 26, bytes([1])),
        ("EPS_PAYLOAD_MODE (func 16)", 16, bytes([1])),
        ("TCS FPA_COOLER (func 43) — COM-009's path", 43, bytes([1])),
    ])
    def test_path_energises_the_line_and_brings_the_fpa_ready(
            self, engine, label, func, arg):
        _, err = _s8(engine, func, arg)
        assert err is None, f"{label} was refused: {err}"
        _run(engine, 1200.0)
        p = engine.params
        assert p[0x0114] == 1, f"{label}: EPS fpa_cooler line still dark"
        assert p[0x040C] == 1, f"{label}: TCS effective cooler state still 0"
        assert p[0x0408] < -14.0, f"{label}: node at {p[0x0408]:.2f} C"
        assert p[0x0610] == 1, f"{label}: FPA READY still NO"

    def test_commanding_off_drops_the_line_too(self, engine):
        _s8(engine, 43, bytes([1]))
        _run(engine, 600.0)
        assert engine.params[0x0114] == 1
        _s8(engine, 43, bytes([0]))
        _run(engine, 60.0)
        assert engine.params[0x0114] == 0
        assert engine.params[0x040C] == 0

    def test_payload_mode_off_releases_the_cooler(self, engine):
        _s8(engine, 26, bytes([1]))
        _run(engine, 300.0)
        assert engine.params[0x0114] == 1
        _s8(engine, 26, bytes([0]))
        _run(engine, 60.0)
        assert engine.params[0x0114] == 0

    def test_cooler_authority_is_published_and_positive(self, engine):
        _s8(engine, 26, bytes([1]))
        _run(engine, 1200.0)
        p = engine.params
        # TCS-side and the payload's mirror must agree.
        assert p[0x064A] == pytest.approx(p[0x0424], abs=1e-6)
        assert p[0x064B] == pytest.approx(p[0x0425], abs=1e-6)
        assert p[0x0424] < -15.0, "the nominal setpoint must still close"
        assert p[0x0425] > 0.0, "margin must be positive at the nominal sink"


class TestSetpointOnTheWire:

    def test_zero_degrees_is_accepted_end_to_end(self, engine):
        """raw 27315 cK = 0.00 degC, the documented top of the envelope, used
        to be refused by a float epsilon (`27315*0.01 - 273.15 == 5.68e-14`)."""
        _, err = _s8(engine, 36, struct.pack('>H', 27315))
        assert err is None, err
        _run(engine, 10.0)
        assert engine.params[0x0631] == pytest.approx(0.0, abs=1e-9)

    def test_the_bottom_of_the_envelope_is_accepted(self, engine):
        _, err = _s8(engine, 36, struct.pack('>H', 25315))
        assert err is None, err
        _run(engine, 10.0)
        assert engine.params[0x0631] == pytest.approx(-20.0, abs=1e-9)

    def test_outside_the_envelope_is_still_refused(self, engine):
        _, err = _s8(engine, 36, struct.pack('>H', 27400))
        assert err is not None


# ── (A) PL-d: COM-011's route, over the wire ────────────────────────

class TestCalibrationOverTheWire:

    def _cooled_standby(self, engine):
        _s8(engine, 26, bytes([1]))          # STANDBY (cooler follows)
        _run(engine, 1200.0)
        assert engine.params[0x0610] == 1, "FPA never came ready"

    def test_com011_route_reaches_a_valid_calibration(self, engine):
        self._cooled_standby(engine)
        # Step: close the shutter with the NEW absolute command (func 119).
        _, err = _s8(engine, 119, bytes([0]))
        assert err is None, err
        _run(engine, 4.0)
        assert engine.params[0x063C] == 0.0, "COM-011 checks 0x063C == 0"

        # Step: START_CALIBRATION(DARK) — no PAYLOAD_SET_MODE(2) needed.
        _, err = _s8(engine, 37, bytes([0]))
        assert err is None, err
        assert engine.params[0x0600] == 3 or \
            engine.subsystems["payload"]._state.mode == 3
        _run(engine, 30.0)
        pl = engine.subsystems["payload"]
        assert pl._state.calibration_state == 3
        assert pl._state.cal_abort_reason == 0
        assert pl._state.bias_coeff["blue"] is not None

        # Flat half, and the pair becomes valid.
        _s8(engine, 119, bytes([1]))
        _s8(engine, 95, bytes([1]))          # PAYLOAD_CAL_LAMP on
        _run(engine, 2.0)
        _, err = _s8(engine, 37, bytes([1]))
        assert err is None, err
        _run(engine, 30.0)
        assert engine.params[0x0646] == 0x0F

    def test_calibration_from_imaging_is_refused_over_the_wire(self, engine):
        self._cooled_standby(engine)
        _s8(engine, 26, bytes([2]))          # IMAGING
        _run(engine, 4.0)
        _, err = _s8(engine, 37, bytes([0]))
        assert err is not None
        assert "IMAGING" in err


# ── (C) PL-b / PL-c: the image chain ────────────────────────────────

class TestImageChain:

    def _ready(self, engine):
        _s8(engine, 26, bytes([1]))
        _run(engine, 1200.0)
        assert engine.params[0x0610] == 1
        _s8(engine, 26, bytes([2]))          # IMAGING
        _run(engine, 4.0)
        pl = engine.subsystems["payload"]
        # This bench is not exercising the pointing gate.
        pl._state.aocs_seen = False
        return pl

    def test_imaging_stores_nothing_without_a_capture(self, engine):
        """Pre-fix: 9.735 MB/s of un-catalogued phantom data, 20 GB in ~33 min,
        against NOM-002's teaching note that images do not appear on their own."""
        pl = self._ready(engine)
        before = pl._state.mem_used_mb
        _run(engine, 600.0)
        assert pl._state.mem_used_mb == pytest.approx(before, abs=1e-6)
        assert pl._state.image_catalog == []
        assert engine.params[0x0648] == 0

    def test_capture_creates_a_product_and_a_science_record(self, engine):
        pl = self._ready(engine)
        science_before = len(engine._tm_storage._stores[3])
        out, err = _s8(engine, 28, struct.pack('>HH', 501, 2048))
        assert err is None, err
        assert len(out) == 1, "capture must answer with a product record"
        img = pl.find_image(501)
        assert img is not None
        assert img["size_mb"] > 0
        _run(engine, 4.0)
        assert engine.params[0x0648] == 1
        assert engine.params[0x0647] == pytest.approx(img["size_mb"], rel=0.01)
        assert len(engine._tm_storage._stores[3]) == science_before + 1

    def test_the_science_store_holds_only_science(self, engine):
        """It used to fill with S1 acks and S8 reports, so the scheduled
        S15_DUMP_SCIENCE_STORE returned no science at all."""
        from smo_common.protocol.ecss_packet import decommutate_packet
        pl = self._ready(engine)
        _s8(engine, 28, struct.pack('>HH', 601, 2048))
        _run(engine, 300.0)
        services = set()
        for pkt in engine._tm_storage._stores[3]:
            parsed = decommutate_packet(pkt)
            if parsed and parsed.secondary:
                services.add((parsed.secondary.service,
                              parsed.secondary.subtype))
        assert services == {(8, 2)}, f"non-science in the science store: {services}"

    def test_s13_refuses_a_scene_that_was_never_taken(self, engine):
        """Pre-fix: scene 9999 against an EMPTY catalogue returned
        transfer_id 1, 4 MB, ACTIVE."""
        self._ready(engine)
        d = engine._dispatcher
        d._err_reason = None
        d._handle_s13(1, struct.pack('>HH', 9999, 0))
        assert d._s13_transfers == {}
        assert d._err_reason is not None
        assert "not in the payload image catalogue" in d._err_reason

    def test_s13_sizes_from_the_product_and_the_bytes_checksum(self, engine):
        pl = self._ready(engine)
        _s8(engine, 28, struct.pack('>HH', 701, 2048))
        d = engine._dispatcher
        d._handle_s13(1, struct.pack('>HH', 701, 0))
        tid = max(d._s13_transfers)
        xfer = d._s13_transfers[tid]
        img = pl.find_image(701)
        assert xfer["size_bytes"] == pl.image_size_bytes(img)
        assert xfer["total_blocks"] == pl.image_block_count(img)

        crc = 0
        for i in range(xfer["total_blocks"]):
            crc = zlib.crc32(pl.image_block(701, i), crc)
        assert crc == xfer["checksum"], (
            "the served bytes do not checksum to what S13.1 reported")

    def test_a_completed_transfer_frees_the_product(self, engine):
        pl = self._ready(engine)
        _s8(engine, 28, struct.pack('>HH', 801, 2048))
        used_with = pl._state.mem_used_mb
        d = engine._dispatcher
        d._handle_s13(1, struct.pack('>HH', 801, 0))
        tid = max(d._s13_transfers)
        for i in range(d._s13_transfers[tid]["total_blocks"]):
            d._handle_s13(3, struct.pack('>HH', tid, i))
        d._handle_s13(5, struct.pack('>H', tid))
        assert pl.find_image(801) is None
        assert pl._state.mem_used_mb < used_with

    def test_a_partial_transfer_leaves_the_product_on_board(self, engine):
        """"The pass dropped, re-dump next orbit" is the case S13 exists for."""
        pl = self._ready(engine)
        _s8(engine, 28, struct.pack('>HH', 802, 2048))
        d = engine._dispatcher
        d._handle_s13(1, struct.pack('>HH', 802, 0))
        tid = max(d._s13_transfers)
        for i in range(5):
            d._handle_s13(3, struct.pack('>HH', tid, i))
        d._handle_s13(5, struct.pack('>H', tid))
        assert pl.find_image(802) is not None

    def test_func_32_answers_with_a_catalogue_report(self, engine):
        """It called the model and discarded the result."""
        self._ready(engine)
        _s8(engine, 28, struct.pack('>HH', 901, 2048))
        _s8(engine, 28, struct.pack('>HH', 902, 2048))
        packed = []
        real = engine.tm_builder._pack_tm
        engine.tm_builder._pack_tm = lambda **kw: (packed.append(kw) or b"")
        try:
            _s8(engine, 32)
        finally:
            engine.tm_builder._pack_tm = real
        assert len(packed) == 1
        assert (packed[0]["service"], packed[0]["subtype"]) == (8, 2)
        data = packed[0]["data"]
        shown, held = struct.unpack('>HH', data[:4])
        assert shown == held == 2
        assert len(data) == 12 + 2 * 20

    def test_func_94_reports_bytes_blocks_and_checksum(self, engine):
        pl = self._ready(engine)
        _s8(engine, 28, struct.pack('>HH', 903, 2048))
        packed = []
        real = engine.tm_builder._pack_tm
        engine.tm_builder._pack_tm = lambda **kw: (packed.append(kw) or b"")
        try:
            _s8(engine, 94, struct.pack('>H', 903))
        finally:
            engine.tm_builder._pack_tm = real
        assert len(packed) == 1
        scene, total, blocks, crc = struct.unpack('>HIII', packed[0]["data"])
        assert scene == 903
        assert total == pl.image_size_bytes(pl.find_image(903))
        assert blocks == pl.image_block_count(pl.find_image(903))
        assert crc == pl.image_checksum(903)
