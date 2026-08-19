"""SMO Simulator — Enhanced OBDH Model.

Dual cold-redundant OBC (A/B), boot loader / application software model,
dual CAN bus with failure isolation, buffer management with stop-when-full,
and comprehensive failure injection.
"""
import math
import struct
from dataclasses import dataclass, field
from typing import Any

from smo_common.models.subsystem import SubsystemModel
from smo_simulator.failure_spec import (
    FailureSpec, ParamSpec, T_FLOAT, T_INT, enum, number,
)

# Software image constants
SW_BOOTLOADER = 0
SW_APPLICATION = 1

# OBC unit status
OBC_OFF = 0
OBC_STANDBY = 1
OBC_ACTIVE = 2

# Bus status
BUS_OK = 0
BUS_DEGRADED = 1
BUS_FAILED = 2

# Reboot causes
REBOOT_NONE = 0
REBOOT_COMMAND = 1
REBOOT_WATCHDOG = 2
REBOOT_MEMORY_ERROR = 3
REBOOT_SWITCHOVER = 4

# ── Onboard clock: time reference (obdh.time_ref, param 0x0A13) ──
# See docs/plan_2026-08c/TIME_ARCHITECTURE.md Part B.
TIME_REF_NONE = 0      # never set since the last bootloader reset (free-running from CUC 0)
TIME_REF_GROUND = 1    # last set by ground command (S9,1); free-running with drift
TIME_REF_GPS = 2       # GPS-disciplined; NO drift while the fix is good
TIME_REF_HOLDOVER = 3  # was GPS-disciplined, fix lost; free-running with drift

TIME_REF_NAMES = {
    TIME_REF_NONE: "NONE",
    TIME_REF_GROUND: "GROUND",
    TIME_REF_GPS: "GPS",
    TIME_REF_HOLDOVER: "HOLDOVER",
}

#: AOCS GPS fix type (0=none, 1=2D, 2=3D, 3=3D+velocity). A 3D fix or better
#: is required to discipline the onboard clock.
GPS_FIX_PARAM = 0x0274
GPS_FIX_MIN = 2

#: EPS `obc` power-line status. Losing and restoring this line is a power-on
#: reset: the bootloader comes up and the clock is back at J2000.
OBC_POWER_LINE_PARAM = 0x0110

#: Sentinel for obdh.time_since_sync_s when the clock has never been synced.
TIME_SINCE_SYNC_NEVER = 0xFFFFFFFF

# ── Buffer-fill telemetry ↔ S15 on-board stores ──────────────────────
# obdh.hktm_buf_fill / event_buf_fill / alarm_buf_fill (0x0312/0x0313/0x0314)
# are NOT a second, independent set of buffers living inside the OBC. They are
# the fill of the S15 on-board stores that TM(15,14) reports and that the
# console's PUS SERVICES → S15 Storage page shows. There is exactly one set of
# stores on board, so there must be exactly one number per store.
#
# They used to be a free-running stub: hktm_buf_fill accumulated a random
# `uniform(0, 2)` percentage every second against a fictitious 1000-slot
# capacity and drained only while the TTC carrier was locked — i.e. it was a
# model of *ground contact*, not of storage. Measured headlessly against the
# shipped config it saturated at 100 % roughly 25 minutes into every gap
# between passes while the real HK_Store (18 000 slots) sat at 15–35 %, and it
# read 0 % during a pass while the real store kept growing. event_buf_fill and
# alarm_buf_fill were worse: raw packet counts declared with units "%" that
# nothing on board ever incremented, so they read 0 for ever.
#
# param key → S15 store id (see smo_simulator.tm_storage.DEFAULT_STORES).
BUFFER_STORE_IDS = {"hktm": 1, "event": 2, "alarm": 4}


def _pct(count: float, capacity: float) -> float:
    """count/capacity as a percentage, clamped to [0, 100]."""
    if capacity <= 0:
        return 0.0
    return max(0.0, min(100.0, float(count) / float(capacity) * 100.0))

# Onboard-clock S5 events (event_catalog.yaml, OBDH block).
EV_OBC_TIME_SET = 0x030C       # S9,1 ground SET_TIME
EV_OBC_TIME_SYNC = 0x030D      # GPS sync (commanded or automatic re-acquisition)
EV_OBC_TIME_REF_LOST = 0x030E  # GPS fix lost → HOLDOVER


@dataclass
class OBDHState:
    # OBC mode: 0=nominal, 1=safe, 2=maintenance
    mode: int = 0
    temp: float = 25.0
    cpu_load: float = 35.0
    mmm_used_pct: float = 20.0

    # TC/TM counters
    tc_rx_count: int = 0
    tc_acc_count: int = 0
    tc_rej_count: int = 0
    tm_pkt_count: int = 0
    # S1 failure code of the most recently refused telecommand (0 = none since
    # boot). The code used to exist only inside ServiceDispatcher; nothing
    # downlinked it, so no parameter display could ever show it (defect #88).
    last_tc_error_code: int = 0

    # Timing
    uptime_s: int = 0

    # ── Onboard clock (TIME_ARCHITECTURE.md Part B) ──
    # The OBC's OWN clock, independent of sim time. Seconds since J2000
    # (2000-01-01T12:00:00 UTC = CUC 0), sub-second resolution. Starts at 0.0
    # on first start-up and after every bootloader reset.
    obc_time_s: float = 0.0
    #: TIME_REF_NONE / _GROUND / _GPS / _HOLDOVER
    time_ref: int = TIME_REF_NONE
    #: obc_time_s at the last successful sync; -1.0 if never synced.
    last_sync_s: float = -1.0
    #: This unit's actual oscillator drift, drawn ONCE per run from the seeded
    #: per-model RNG. Hardware property — a reboot does NOT change it.
    clock_drift_ppm: float = 0.0

    # Reboot
    reboot_count: int = 0
    sw_version: int = 0x0100
    mem_errors: int = 0

    # Watchdog
    watchdog_armed: bool = True
    watchdog_timer: int = 0
    watchdog_period: int = 30

    # ── Dual OBC ──
    active_obc: int = 0  # 0=A, 1=B
    obc_b_status: int = OBC_STANDBY  # OFF/STANDBY (backup cold redundant)
    boot_count_a: int = 0
    boot_count_b: int = 0

    # ── Software image ──
    sw_image: int = SW_BOOTLOADER  # 0=bootloader, 1=application — boots into bootloader, app loaded by ground
    boot_app_timer: float = 0.0  # Countdown for application boot (10s)
    boot_app_pending: bool = False
    boot_inhibit: bool = False  # Prevent auto-boot
    boot_image_corrupt: bool = False  # Application image CRC fail

    # ── Dual CAN Bus ──
    active_bus: int = 0  # 0=A, 1=B
    bus_a_status: int = BUS_OK
    bus_b_status: int = BUS_OK

    # ── Buffer management ──
    # All three *_buf_fill values are PERCENTAGES (0.0–100.0) of the matching
    # S15 on-board store (BUFFER_STORE_IDS): store 1 HK, store 2 events,
    # store 4 alarms. They are refreshed from the store every tick and are the
    # same numbers TM(15,14) reports — see the note at the top of this module.
    hktm_buf_fill: float = 0.0
    event_buf_fill: float = 0.0
    alarm_buf_fill: float = 0.0
    # Fallback capacities, used ONLY when the model is constructed standalone
    # (unit tests) with no engine and therefore no S15 storage to read. When a
    # store is present its own capacity is authoritative.
    hktm_buf_capacity: int = 1000
    event_buf_capacity: int = 500
    alarm_buf_capacity: int = 200
    # Local event/alarm counters behind record_event() / record_alarm(). Kept
    # so the standalone model still has a fill to report without a store.
    event_buf_count: int = 0
    alarm_buf_count: int = 0

    # ── Last reboot cause ──
    last_reboot_cause: int = REBOOT_NONE

    # ── Memory segment health (per-segment fault status for CON memory_segment_failure) ──
    # Segment 0..3 → True == healthy, False == failed
    memory_segments: list[bool] = field(
        default_factory=lambda: [True, True, True, True]
    )

    # ── Phase 4: Flight hardware realism ──
    seu_count: int = 0              # Single-event upset counter
    scrub_progress_pct: float = 0.0 # Memory scrub progress (0-100%)
    scrub_active: bool = False      # Memory scrub in progress
    task_count: int = 12            # Active OS tasks
    stack_usage_pct: float = 35.0   # Stack usage percentage
    heap_usage_pct: float = 40.0    # Heap usage percentage
    heat_dissipation_w: float = 0.0 # OBC nominal heat dissipation (Watts)

    # ── Bus equipment mapping (which subsystems are on which bus) ──
    # In a real system this is hardware-defined; here it's configurable
    bus_a_subsystems: list[str] = field(
        default_factory=lambda: ["eps", "tcs", "aocs"]
    )
    bus_b_subsystems: list[str] = field(
        default_factory=lambda: ["ttc", "payload"]
    )

    # S2 Device Access — device on/off states (device_id -> on/off)
    device_states: dict = field(default_factory=lambda: {
        0x0500: True,   # OBC-A
        0x0501: True,   # OBC-B
        0x0502: True,   # Mass memory unit
        0x0503: True,   # Watchdog timer
        0x0504: True,   # CAN bus interface
    })


class OBDHBasicModel(SubsystemModel):
    """Enhanced OBDH with dual OBC, boot loader, dual CAN bus,
    and buffer management."""

    def __init__(self):
        self._state = OBDHState()
        # Per-model RNG (plan A-2). Standalone construction gets a fresh
        # unseeded stream so existing unit tests keep working; the engine
        # replaces this with a Random derived from simulation.yaml `seed`.
        import random as _random
        self.rng = _random.Random(20260731)  # deterministic standalone fallback; engine overrides via derive_rng
        self._cpu_base = 35.0
        # Nominal CPU load, from `obdh.cpu_baseline_pct`. `cpu_spike` moves
        # `_cpu_base`; clearing it used to restore a hard-coded 35 %, which
        # silently reconfigured the spacecraft whenever the config said
        # otherwise.
        self._cpu_base_nominal = 35.0
        self._param_ids: dict[str, int] = {}
        self._event_to_emit: tuple[int, str] | None = None  # (event_id, event_name)
        self._prev_mode = 0
        self._prev_reboot_count = 0
        self._prev_bus_a_status = 0
        self._prev_bus_b_status = 0
        self._prev_active_obc = 0
        self._prev_hktm_buf_fill = 0
        self._prev_event_buf_fill = 0

        # ── Onboard clock ──
        # Config (configure() overwrites from OBDHConfig; these are the
        # fallbacks for standalone construction in unit tests).
        self._clock_drift_ppm_nominal = 12.0
        self._clock_drift_sigma_ppm = 4.0
        self._clock_gps_discipline = True
        self._clock_jump_event_threshold_s = 1.0
        self._clock_drift_failure_ppm = 500.0
        # clock_drift_ppm is drawn once per run, lazily on the first tick: the
        # engine replaces `self.rng` with the seeded per-model stream AFTER
        # configure() runs, so drawing in configure() would use the fallback
        # RNG and determinism would not hold.
        self._clock_drift_drawn = False
        self._clock_drift_saved: float | None = None
        # Sub-second uptime accumulator. uptime_s stays an integer count of
        # whole seconds for telemetry, but `int(dt)` truncation used to freeze
        # it at any non-integer sub-step (speed 2.5 at 1 Hz ⇒ 0.8333 s steps).
        self._uptime_acc = 0.0
        self._prev_gps_fix_ok = False
        self._prev_obc_line_on: bool | None = None

    @property
    def name(self) -> str:
        return "obdh"

    def configure(self, config: dict[str, Any]) -> None:
        self._state.watchdog_period = config.get("watchdog_period_ticks", 30)
        self._cpu_base = config.get("cpu_baseline_pct", 35.0)
        self._cpu_base_nominal = self._cpu_base

        # Buffer capacities
        buffers = config.get("buffers", {})
        self._state.hktm_buf_capacity = buffers.get("hktm_capacity", 1000)
        self._state.event_buf_capacity = buffers.get("event_capacity", 500)
        self._state.alarm_buf_capacity = buffers.get("alarm_capacity", 200)

        # Bus equipment mapping
        bus_config = config.get("bus_mapping", {})
        if "bus_a" in bus_config:
            self._state.bus_a_subsystems = list(bus_config["bus_a"])
        if "bus_b" in bus_config:
            self._state.bus_b_subsystems = list(bus_config["bus_b"])

        self._param_ids = config.get("param_ids", {
            "obc_mode": 0x0300, "obc_temp": 0x0301, "cpu_load": 0x0302,
            "mmm_used": 0x0303, "tc_rx": 0x0304, "tc_acc": 0x0305,
            "tc_rej": 0x0306, "tm_pkt": 0x0307, "uptime": 0x0308,
            "obc_time": 0x0309, "reboot_count": 0x030A,
            "sw_version": 0x030B,
            # New params
            "active_obc": 0x030C, "obc_b_status": 0x030D,
            "active_bus": 0x030E, "bus_a_status": 0x030F,
            "bus_b_status": 0x0310, "sw_image": 0x0311,
            "hktm_buf_fill": 0x0312, "event_buf_fill": 0x0313,
            "alarm_buf_fill": 0x0314,
            "last_reboot_cause": 0x0316,
            "boot_count_a": 0x0317, "boot_count_b": 0x0318,
            # Onboard clock (TIME_ARCHITECTURE.md Part B)
            "obc_time_fine": 0x0A12, "time_ref": 0x0A13,
            "time_since_sync": 0x0A14,
        })

        # ── Onboard clock configuration ──
        self._clock_drift_ppm_nominal = float(
            config.get("clock_drift_ppm_nominal", 12.0))
        self._clock_drift_sigma_ppm = float(
            config.get("clock_drift_sigma_ppm", 4.0))
        self._clock_gps_discipline = bool(
            config.get("clock_gps_discipline", True))
        self._clock_jump_event_threshold_s = float(
            config.get("clock_jump_event_threshold_s", 1.0))
        self._clock_drift_failure_ppm = float(
            config.get("clock_drift_failure_ppm", 500.0))

        # The OBC clock starts at J2000 (CUC 0) on first start-up — it is NOT
        # initialised from the wall clock and NOT from sim time. Ground has to
        # set it (S9,1) or GPS has to discipline it. This is the LEOP flow.
        self._reset_clock()
        self._clock_drift_drawn = False

    # ─── Onboard clock (TIME_ARCHITECTURE.md Part B) ─────────────────

    def _reset_clock(self) -> None:
        """Bootloader clock reset: back to J2000, no time reference.

        Owner ruling: OBC_REBOOT drops to the bootloader and the bootloader
        resets the clock. OBC_BOOT_APP does NOT restore it — the application
        comes up at J2000 and must be re-synced.
        """
        s = self._state
        s.obc_time_s = 0.0
        s.time_ref = TIME_REF_NONE
        s.last_sync_s = -1.0

    def _ensure_clock_drift(self) -> None:
        """Draw this unit's oscillator drift once per run.

        Drawn from the seeded per-model RNG (`smo_common.state.derive_rng`,
        installed by the engine as `self.rng`) so the same seed reproduces the
        same clock. Not redrawn on reboot — it is a property of the crystal.
        """
        if self._clock_drift_drawn:
            return
        self._state.clock_drift_ppm = (
            self._clock_drift_ppm_nominal
            + self.rng.gauss(0.0, self._clock_drift_sigma_ppm)
        )
        self._clock_drift_drawn = True

    def get_obc_time_s(self) -> float:
        """Onboard time in seconds since J2000, full precision.

        **This is the single accessor for onboard time.** `engine._get_cuc_time`
        must return this value (a float, sub-second) — never sim time, never
        wall clock. `TMBuilder` carries the fraction into the CUC fine field.
        """
        return float(self._state.obc_time_s)

    def get_obc_cuc(self) -> tuple[int, int]:
        """Onboard time as a `(coarse, fine)` CUC pair."""
        t = float(self._state.obc_time_s)
        coarse = int(math.floor(t))
        fine = int(round((t - coarse) * 65536.0))
        if fine >= 65536:
            coarse += 1
            fine -= 65536
        return coarse, max(0, fine)

    def get_time_ref(self) -> int:
        return int(self._state.time_ref)

    def time_since_sync_s(self) -> float | None:
        """Onboard seconds since the last successful sync, None if never."""
        s = self._state
        if s.last_sync_s < 0:
            return None
        return max(0.0, float(s.obc_time_s) - float(s.last_sync_s))

    def _sim_cuc_seconds(self) -> float | None:
        """Sim time expressed in CUC seconds, or None if unavailable.

        Only the GPS-discipline path may use this: GPS is the spacecraft's
        window onto true (sim) time. Nothing else in the model reads sim time.
        """
        eng = getattr(self, "_engine", None)
        sim_time = getattr(eng, "_sim_time", None) if eng is not None else None
        if sim_time is None:
            return None
        from smo_common.protocol.ecss_packet import datetime_to_cuc_seconds
        try:
            return float(datetime_to_cuc_seconds(sim_time))
        except Exception:
            return None

    def set_obc_time(self, new_time_s: float, ref: int) -> float:
        """Set the onboard clock. Returns the SIGNED jump in seconds."""
        s = self._state
        jump = float(new_time_s) - float(s.obc_time_s)
        s.obc_time_s = float(new_time_s)
        s.time_ref = int(ref)
        s.last_sync_s = float(new_time_s)
        return jump

    def _queue_event(self, event_id: int, severity: str,
                     description: str) -> None:
        """Push an S5 event to the engine's model-event queue (best effort)."""
        eng = getattr(self, "_engine", None)
        if eng is None:
            return
        try:
            eng._model_event_queue.put_nowait({
                "event_id": event_id,
                "severity": severity,
                "subsystem": "obdh",
                "description": description,
            })
        except Exception:
            pass

    def _propagate_clock(self, dt: float,
                         shared_params: dict[int, float]) -> None:
        """Advance the onboard clock by one tick.

        * GPS-disciplined and the fix is good → held EXACTLY to sim time; no
          drift accumulates (owner ruling).
        * otherwise → free-running float accumulation with the drawn drift.
          Float, not `int(dt)`: the old truncation froze the clock entirely at
          any non-integer sub-step.
        """
        s = self._state
        self._ensure_clock_drift()

        gps_fix = shared_params.get(GPS_FIX_PARAM, 0.0)
        try:
            fix_ok = float(gps_fix) >= GPS_FIX_MIN
        except (TypeError, ValueError):
            fix_ok = False

        # ── GPS fix transitions ──
        if s.time_ref == TIME_REF_GPS and not fix_ok:
            s.time_ref = TIME_REF_HOLDOVER
            self._queue_event(
                EV_OBC_TIME_REF_LOST, "MEDIUM",
                "OBC clock HOLDOVER: GPS fix lost, drift resumes "
                f"at {s.clock_drift_ppm:.1f} ppm (jump +0.000s)")
        elif (s.time_ref == TIME_REF_HOLDOVER and fix_ok
                and self._clock_gps_discipline):
            sim_cuc = self._sim_cuc_seconds()
            if sim_cuc is not None:
                jump = self.set_obc_time(sim_cuc, TIME_REF_GPS)
                self._queue_event(
                    EV_OBC_TIME_SYNC, "MEDIUM",
                    "OBC clock re-acquired GPS discipline "
                    f"(jump {jump:+.3f}s)")
            else:
                s.time_ref = TIME_REF_GPS

        # ── Propagation ──
        if s.time_ref == TIME_REF_GPS and fix_ok:
            sim_cuc = self._sim_cuc_seconds()
            if sim_cuc is not None:
                s.obc_time_s = sim_cuc
                s.last_sync_s = sim_cuc  # continuously disciplined
                self._prev_gps_fix_ok = fix_ok
                return
        s.obc_time_s += dt * (1.0 + s.clock_drift_ppm * 1e-6)
        self._prev_gps_fix_ok = fix_ok

    def _check_obc_power_line(self, shared_params: dict[int, float]) -> bool:
        """Track the EPS `obc` line. Returns True while the OBC is powered.

        Powering the line off and on is a power-on reset: same effect as
        OBC_REBOOT — bootloader, and the bootloader resets the clock to J2000.
        """
        raw = shared_params.get(OBC_POWER_LINE_PARAM)
        if raw is None:
            # EPS has not published yet (e.g. model-order on the first tick,
            # or a standalone unit test). Assume powered; record nothing.
            return True if self._prev_obc_line_on is None else self._prev_obc_line_on
        on = bool(raw)
        prev = self._prev_obc_line_on
        self._prev_obc_line_on = on
        if prev is not None and on and not prev:
            # Power restored → power-on reset.
            self._reboot(REBOOT_COMMAND)
        return on

    # ─── Buffer fill (S15 on-board stores) ───────────────────────────

    def store_fill_pct(self) -> dict[str, float] | None:
        """Fill percentage of each S15 store behind the buffer telemetry.

        Returns ``{"hktm": pct, "event": pct, "alarm": pct}`` read from
        ``engine._tm_storage``, or None when no storage is reachable (the
        model constructed standalone in a unit test, or an engine mock).
        The percentages are exactly ``count / capacity * 100`` — the same
        expression ``OnboardTMStorage.get_status()`` reports in TM(15,14), so
        the two consoles pages are numerically identical by construction.
        """
        eng = getattr(self, "_engine", None)
        storage = getattr(eng, "_tm_storage", None) if eng is not None else None
        if storage is None:
            return None
        try:
            status = {int(st["id"]): st for st in storage.get_status()}
        except Exception:  # noqa: BLE001 — a mock/absent store must not break the tick
            return None
        out: dict[str, float] = {}
        for key, store_id in BUFFER_STORE_IDS.items():
            st = status.get(store_id)
            if st is None:
                continue
            try:
                cap = float(st.get("capacity") or 0.0)
                count = float(st.get("count") or 0.0)
            except (TypeError, ValueError):
                continue
            pct = 0.0 if cap <= 0 else count / cap * 100.0
            out[key] = max(0.0, min(100.0, pct))
        return out or None

    def _refresh_buffer_fill(self) -> None:
        """Refresh the three buffer-fill percentages from the S15 stores.

        With no store reachable (standalone model) the event/alarm fills fall
        back to the local record_event()/record_alarm() counters and the HK
        fill simply holds — it is never invented.
        """
        s = self._state
        fills = self.store_fill_pct()
        if fills is not None:
            s.hktm_buf_fill = float(fills.get("hktm", s.hktm_buf_fill))
            s.event_buf_fill = float(fills.get("event", s.event_buf_fill))
            s.alarm_buf_fill = float(fills.get("alarm", s.alarm_buf_fill))
        else:
            s.event_buf_fill = _pct(s.event_buf_count, s.event_buf_capacity)
            s.alarm_buf_fill = _pct(s.alarm_buf_count, s.alarm_buf_capacity)
        # Belt and braces: these are percentages and nothing downstream may
        # ever render a value above 100 % with a '%' suffix again.
        s.hktm_buf_fill = max(0.0, min(100.0, float(s.hktm_buf_fill)))
        s.event_buf_fill = max(0.0, min(100.0, float(s.event_buf_fill)))
        s.alarm_buf_fill = max(0.0, min(100.0, float(s.alarm_buf_fill)))

    def _apply_device_power_gates(self, s: "OBDHState") -> None:
        """Reflect OBDH equipment powered OFF via S2 device access into the
        telemetry. Wires the S2_OBDH_* on/off commands, previously dead flags.

        Devices (effects chosen to be telemetry-visible but non-cascading so
        they don't spuriously trigger bus/OBC failover FDIR):
          0x0501 OBC-B          — backup OBC unpowered → report OBC_OFF.
          0x0502 Mass memory    — no accessible mass memory → MMM used = 0%.
          0x0503 Watchdog timer  — watchdog unpowered → disarmed.
          0x0504 CAN bus iface   — active CAN interface lost → active bus
                                   reads DEGRADED (not FAILED, to avoid forcing
                                   an autonomous bus switchover every tick).

        OBC-A (0x0500) is the unit running the flight software; commanding it
        off does not silently kill the simulation here — its state is exposed
        through the existing active_obc / OBC switchover telemetry instead.
        """
        ds = s.device_states

        if not ds.get(0x0501, True):  # OBC-B (backup)
            s.obc_b_status = OBC_OFF

        if not ds.get(0x0502, True):  # Mass memory unit
            s.mmm_used_pct = 0.0

        if not ds.get(0x0503, True):  # Watchdog timer
            s.watchdog_armed = False

        if not ds.get(0x0504, True):  # CAN bus interface
            if s.active_bus == 0 and s.bus_a_status == BUS_OK:
                s.bus_a_status = BUS_DEGRADED
            elif s.active_bus == 1 and s.bus_b_status == BUS_OK:
                s.bus_b_status = BUS_DEGRADED

    def tick(self, dt: float, orbit_state: Any,
             shared_params: dict[int, float]) -> None:
        s = self._state

        # ── OBC power line ──
        # Off→on is a power-on reset (bootloader + clock back to J2000).
        powered = self._check_obc_power_line(shared_params)

        # ── Timing ──
        # Float accumulation. The old `int(dt)` truncated every sub-second
        # sub-step to zero, so at speed 2.5 (three 0.8333 s sub-steps per 1 Hz
        # tick) both uptime and the OBC clock froze completely.
        if powered:
            self._uptime_acc += dt
            s.uptime_s = int(self._uptime_acc)
            self._propagate_clock(dt, shared_params)

        # Read OBC temperature from TCS via shared_params
        tcs_obc_temp = shared_params.get(0x0406)
        if tcs_obc_temp is not None:
            s.temp = tcs_obc_temp

        # Boot application pending (10s simulated CRC check)
        if s.boot_app_pending:
            s.boot_app_timer -= dt
            if s.boot_app_timer <= 0.0:
                s.boot_app_pending = False
                if s.boot_image_corrupt:
                    # CRC fail — stay in boot loader, generate BOOT_FAILURE event
                    s.sw_image = SW_BOOTLOADER
                    self._event_to_emit = (0x0305, "BOOT_FAILURE")
                else:
                    s.sw_image = SW_APPLICATION
                    s.mode = 0  # nominal

        # CPU load depends on mode and sw_image
        if s.sw_image == SW_BOOTLOADER:
            # Boot loader: minimal CPU, limited functionality
            s.cpu_load = max(0, min(100, 15.0 + self.rng.gauss(0, 1.0)))
        else:
            mode_load = {0: 0, 1: -10, 2: 20}
            s.cpu_load = max(
                0,
                min(
                    100,
                    self._cpu_base
                    + mode_load.get(s.mode, 0)
                    + self.rng.gauss(0, 2.0),
                ),
            )

        # Watchdog (only in application mode AND when armed).
        # Defect #30: previously the mode check was inverted vs. its comment —
        # mode 0 is *nominal* (not safe), so the watchdog was frozen in normal
        # flight and only ran in safe/maintenance, and an injected watchdog_reset
        # was erased before it could fire. Correct model: the watchdog is active
        # in nominal mode, kicked every tick by healthy software so it never
        # times out on its own; a watchdog_reset failure forces watchdog_timer to
        # >= watchdog_period, which is detected here and triggers a reboot.
        if s.sw_image == SW_APPLICATION and s.watchdog_armed:
            if s.mode == 0:
                # Nominal: watchdog armed and counting toward timeout.
                if s.watchdog_timer >= s.watchdog_period:
                    self._event_to_emit = (0x0303, "WATCHDOG_TIMEOUT")
                    self._reboot(REBOOT_WATCHDOG)
                else:
                    s.watchdog_timer = 0  # healthy software kicks the watchdog
            else:
                # Safe / maintenance: watchdog held (software not running normally).
                s.watchdog_timer = 0
        else:
            # Bootloader or watchdog disabled: timer stays at zero
            s.watchdog_timer = 0

        # Phase 4: SEU simulation (South Atlantic Anomaly model)
        # ~1 SEU per orbit on average for LEO spacecraft
        seu_prob = 0.00015 * dt  # ~1 per 6600s orbit
        if self.rng.random() < seu_prob:
            s.seu_count += 1
            s.mem_errors += 1
            self._event_to_emit = (0x0307, "SEU_DETECTED")

        # Memory scrub progress
        if s.scrub_active:
            s.scrub_progress_pct += dt / 60.0 * 5.0  # ~20 min for full scrub
            if s.scrub_progress_pct >= 100.0:
                s.scrub_progress_pct = 0.0
                s.scrub_active = False
                # Scrub corrects SEU errors
                s.mem_errors = max(0, s.mem_errors - 3)
                self._event_to_emit = (0x0308, "SCRUB_COMPLETE")

        # Task count and stack/heap usage
        if s.sw_image == SW_APPLICATION:
            s.task_count = 12 + (3 if s.mode == 0 else 0)
            s.stack_usage_pct = 30.0 + s.cpu_load * 0.3 + self.rng.gauss(0, 1.0)
            s.stack_usage_pct = max(10.0, min(95.0, s.stack_usage_pct))
            s.heap_usage_pct = 35.0 + s.mmm_used_pct * 0.4 + self.rng.gauss(0, 0.5)
            s.heap_usage_pct = max(10.0, min(95.0, s.heap_usage_pct))
        else:
            s.task_count = 4  # Boot loader has minimal tasks
            s.stack_usage_pct = 15.0 + self.rng.gauss(0, 0.5)
            s.heap_usage_pct = 10.0 + self.rng.gauss(0, 0.5)

        # OBC nominal heat dissipation (defects/reviews/obdh.md)
        # ~15W in application mode, ~2W in bootloader (minimal activity)
        if s.sw_image == SW_APPLICATION:
            # Base 12W nominal + 2W per 10% CPU load + mode variation
            mode_heat = {0: 3.0, 1: 0.5, 2: 6.0}  # nominal, safe, maintenance
            s.heat_dissipation_w = 12.0 + (s.cpu_load / 10.0 * 2.0) + mode_heat.get(s.mode, 0) + self.rng.gauss(0, 0.5)
            s.heat_dissipation_w = max(5.0, min(35.0, s.heat_dissipation_w))  # Clamp to realistic range
        else:
            # Bootloader: minimal power consumption
            s.heat_dissipation_w = 2.0 + self.rng.gauss(0, 0.1)

        # ── Buffer fill ──
        # Read straight off the S15 on-board stores (BUFFER_STORE_IDS) so the
        # OBDH page and the PUS SERVICES → S15 Storage page cannot disagree:
        # they are the same store, reported once. See the module note for what
        # the free-running stub that used to live here did instead.
        self._refresh_buffer_fill()

        # Event generation — edge detection and dispatch to engine
        events_to_emit = []

        # OBDH_MODE_CHANGE (0x0300)
        if s.mode != self._prev_mode:
            events_to_emit.append((0x0300, "OBDH_MODE_CHANGE", "INFO"))
            self._event_to_emit = (0x0300, "OBDH_MODE_CHANGE")
            self._prev_mode = s.mode

        # OBC_REBOOT (0x0301) — generated by _reboot method
        if s.reboot_count != self._prev_reboot_count:
            events_to_emit.append((0x0301, "OBC_REBOOT", "HIGH"))
            self._event_to_emit = (0x0301, "OBC_REBOOT")
            self._prev_reboot_count = s.reboot_count

        # MEMORY_ERROR (0x0302) — injected via failure system
        # (already handled in inject_failure)

        # BUS_FAILURE (0x0304) — CAN bus failure detected
        if s.bus_a_status != self._prev_bus_a_status and s.bus_a_status == BUS_FAILED:
            events_to_emit.append((0x0304, "BUS_FAILURE_A", "HIGH"))
            self._event_to_emit = (0x0304, "BUS_FAILURE_A")
        if s.bus_b_status != self._prev_bus_b_status and s.bus_b_status == BUS_FAILED:
            events_to_emit.append((0x0304, "BUS_FAILURE_B", "HIGH"))
            self._event_to_emit = (0x0304, "BUS_FAILURE_B")
        self._prev_bus_a_status = s.bus_a_status
        self._prev_bus_b_status = s.bus_b_status

        # OBC_SWITCHOVER (0x0306) — redundant OBC activated
        if s.active_obc != self._prev_active_obc:
            events_to_emit.append((0x0306, "OBC_SWITCHOVER", "MEDIUM"))
            self._event_to_emit = (0x0306, "OBC_SWITCHOVER")
            self._prev_active_obc = s.active_obc

        # TC_QUEUE_OVERFLOW (0x0309) — S11 queue full
        # (monitored via record methods)
        # TM_STORE_OVERFLOW (0x030A) — S15 store full
        # (checked in tick by storage monitoring)

        # Emit events to engine if available
        if hasattr(self, '_engine') and self._engine:
            for evt_id, evt_name, severity in events_to_emit:
                try:
                    self._engine._model_event_queue.put_nowait({
                        'event_id': evt_id,
                        'severity': severity,
                        'subsystem': 'obdh',
                        'description': evt_name
                    })
                except Exception:
                    pass  # event queue full or format error

        # ── S2 device-access power gates ──
        # Reflect OBDH equipment switched OFF via S2 device access into the
        # telemetry. Previously every entry in device_states was a dead flag —
        # the S2_OBDH_* on/off commands were accepted but the model never read
        # the dict, so they had no effect. All devices default ON so nominal
        # behaviour and the existing test suite are unchanged.
        self._apply_device_power_gates(s)

        # Write params
        p = self._param_ids
        shared_params[p.get("obc_mode", 0x0300)] = s.mode
        shared_params[p.get("obc_temp", 0x0301)] = s.temp
        shared_params[p.get("cpu_load", 0x0302)] = s.cpu_load
        shared_params[p.get("mmm_used", 0x0303)] = s.mmm_used_pct
        shared_params[p.get("tc_rx", 0x0304)] = s.tc_rx_count
        shared_params[p.get("tc_acc", 0x0305)] = s.tc_acc_count
        shared_params[p.get("tc_rej", 0x0306)] = s.tc_rej_count
        shared_params[p.get("last_tc_error_code", 0x0315)] = s.last_tc_error_code
        shared_params[p.get("tm_pkt", 0x0307)] = s.tm_pkt_count
        shared_params[p.get("uptime", 0x0308)] = s.uptime_s
        # Onboard clock. 0x0309 keeps its meaning (coarse CUC seconds);
        # the fine field, the time reference and the age of the last sync are
        # new (TIME_ARCHITECTURE.md Part B).
        coarse, fine = self.get_obc_cuc()
        shared_params[p.get("obc_time", 0x0309)] = coarse
        shared_params[p.get("obc_time_fine", 0x0A12)] = fine
        shared_params[p.get("time_ref", 0x0A13)] = s.time_ref
        since = self.time_since_sync_s()
        if since is None:
            since_tm = TIME_SINCE_SYNC_NEVER
        else:
            # Saturate rather than wrap: 0xFFFFFFFF is reserved for "never".
            since_tm = min(int(since), TIME_SINCE_SYNC_NEVER - 1)
        shared_params[p.get("time_since_sync", 0x0A14)] = since_tm
        shared_params[p.get("reboot_count", 0x030A)] = s.reboot_count
        shared_params[p.get("sw_version", 0x030B)] = s.sw_version
        # New params
        shared_params[p.get("active_obc", 0x030C)] = s.active_obc
        shared_params[p.get("obc_b_status", 0x030D)] = s.obc_b_status
        shared_params[p.get("active_bus", 0x030E)] = s.active_bus
        shared_params[p.get("bus_a_status", 0x030F)] = s.bus_a_status
        shared_params[p.get("bus_b_status", 0x0310)] = s.bus_b_status
        shared_params[p.get("sw_image", 0x0311)] = s.sw_image
        shared_params[p.get("hktm_buf_fill", 0x0312)] = s.hktm_buf_fill
        shared_params[p.get("event_buf_fill", 0x0313)] = s.event_buf_fill
        shared_params[p.get("alarm_buf_fill", 0x0314)] = s.alarm_buf_fill
        shared_params[p.get("last_reboot_cause", 0x0316)] = s.last_reboot_cause
        shared_params[p.get("boot_count_a", 0x0317)] = s.boot_count_a
        shared_params[p.get("boot_count_b", 0x0318)] = s.boot_count_b
        # Phase 4: Flight hardware params
        shared_params[0x0319] = float(s.seu_count)
        shared_params[0x031A] = s.scrub_progress_pct
        shared_params[0x031B] = float(s.task_count)
        shared_params[0x031C] = s.stack_usage_pct
        shared_params[0x031D] = s.heap_usage_pct
        shared_params[0x031E] = float(s.mem_errors)
        # Heat dissipation (defects/reviews/obdh.md — nominal ~15W in app mode)
        shared_params[0x031F] = s.heat_dissipation_w
        # 0x0A10 obdh.storage_fill / 0x0A11 obdh.can_bus_status. Both were in
        # the catalogue and written by nothing. can_bus_status in particular is
        # the primary parameter of the `obdh_can_bus_failure` FDIR rule and of
        # the fault cascade of the same name: with no value it read 0.0 = FAILED
        # from the first tick, which is why wiring detection up had to fix this
        # too. 1 = healthy, 0 = the active bus has failed.
        shared_params[0x0A10] = s.mmm_used_pct
        active_bus_status = (s.bus_a_status if s.active_bus == 0
                             else s.bus_b_status)
        shared_params[0x0A11] = 0.0 if active_bus_status == BUS_FAILED else 1.0

    def _reboot(self, cause: int = REBOOT_COMMAND) -> None:
        """Reboot OBC — drop to boot loader, reset counters."""
        s = self._state
        s.reboot_count += 1
        s.last_reboot_cause = cause
        s.uptime_s = 0
        self._uptime_acc = 0.0
        # The bootloader resets the clock (owner ruling). OBC_BOOT_APP does
        # NOT restore it — the application comes up at J2000 and must be
        # re-synced. clock_drift_ppm survives: it is the crystal, not state.
        self._reset_clock()
        s.tc_rx_count = 0
        s.tc_acc_count = 0
        s.tc_rej_count = 0
        s.last_tc_error_code = 0
        s.tm_pkt_count = 0
        s.watchdog_timer = 0
        s.sw_image = SW_BOOTLOADER
        s.mode = 1  # safe mode
        s.boot_app_pending = False
        s.boot_app_timer = 0.0

        # Increment per-unit boot count
        if s.active_obc == 0:
            s.boot_count_a += 1
        else:
            s.boot_count_b += 1

        # Auto-boot to application after 10s (unless inhibited)
        if not s.boot_inhibit:
            s.boot_app_pending = True
            s.boot_app_timer = 10.0

    def _switchover(self) -> None:
        """Switch to the other OBC unit (cold redundant)."""
        s = self._state
        old_unit = s.active_obc
        new_unit = 1 - old_unit
        s.active_obc = new_unit
        # Old unit goes to standby, new unit activates
        if old_unit == 0:
            s.obc_b_status = OBC_ACTIVE
        else:
            s.obc_b_status = OBC_STANDBY
        # Cold redundant: fresh start (no state transfer)
        s.last_reboot_cause = REBOOT_SWITCHOVER
        self._reboot(REBOOT_SWITCHOVER)

    def is_subsystem_reachable(self, subsystem_name: str) -> bool:
        """Check if a subsystem is reachable via the active CAN bus."""
        s = self._state
        active = s.active_bus  # 0=A, 1=B

        # Check if active bus is OK
        if active == 0 and s.bus_a_status == BUS_FAILED:
            return False
        if active == 1 and s.bus_b_status == BUS_FAILED:
            return False

        # Check if subsystem is on the active bus
        if active == 0:
            return subsystem_name in s.bus_a_subsystems
        else:
            return subsystem_name in s.bus_b_subsystems

    def record_tc_received(self):
        self._state.tc_rx_count += 1

    def record_tc_accepted(self):
        self._state.tc_acc_count += 1

    def record_tc_rejected(self):
        self._state.tc_rej_count += 1

    def record_tc_failure_code(self, error_code: int) -> None:
        """Latch the S1 failure code of the most recent refused telecommand.

        Called from ``SimulationEngine._emit_command_rejected``, which is the
        one place every refusal — acceptance and execution — passes through.
        """
        try:
            self._state.last_tc_error_code = int(error_code) & 0xFFFF
        except (TypeError, ValueError):
            self._state.last_tc_error_code = 0x0001

    def record_tc_executed(self):
        # Best-effort: if a separate execution counter exists use it,
        # otherwise this is a no-op (acceptance counter is already
        # incremented at S1.1 emit time).
        if hasattr(self._state, "tc_exec_count"):
            self._state.tc_exec_count += 1

    def record_tm_packet(self):
        self._state.tm_pkt_count += 1

    def get_pending_event(self) -> tuple[int, str] | None:
        """Get and clear the pending event (if any)."""
        event = self._event_to_emit
        self._event_to_emit = None
        return event

    def record_event(self) -> bool:
        """Record an event in event buffer. Returns False if buffer full.

        Counts into `event_buf_count`; the reported `event_buf_fill` is a
        percentage and comes from the S15 event store whenever one exists.
        """
        s = self._state
        if s.event_buf_count >= s.event_buf_capacity:
            return False
        s.event_buf_count += 1
        return True

    def record_alarm(self) -> bool:
        """Record an alarm in alarm buffer. Returns False if buffer full."""
        s = self._state
        if s.alarm_buf_count >= s.alarm_buf_capacity:
            return False
        s.alarm_buf_count += 1
        return True

    def get_telemetry(self) -> dict[int, float]:
        return {
            0x0302: self._state.cpu_load,
            0x030A: float(self._state.reboot_count),
        }

    # Commands accepted while running the bootloader image. Anything else
    # belongs to the application image and must be rejected pre-boot, otherwise
    # operators can drive memory scrubs, watchdog config, set_time, etc. before
    # the application is even running.
    _BOOTLOADER_ALLOWED = frozenset({
        "obc_boot_app",
        "obc_boot_inhibit",
        "obc_reboot",
        "obc_switch_unit",
        "obc_select_bus",
        "obc_clear_reboot_cnt",
        "diagnostic",
        "error_log",
    })

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")

        # Bootloader gate: while sw_image == SW_BOOTLOADER, the application
        # command set is not yet loaded.
        if self._state.sw_image == SW_BOOTLOADER and command not in self._BOOTLOADER_ALLOWED:
            return {"success": False, "message": f"Bootloader: {command} not available pre-boot"}

        if command == "set_mode":
            m = int(cmd.get("mode", 0))
            if m in (0, 1, 2):
                self._state.mode = m
                return {"success": True}

        elif command == "set_time":
            # S9,1 SET_TIME. The commanded value is CUC seconds since J2000
            # (coarse + optional fine), NOT a Unix timestamp.
            self._ensure_clock_drift()
            new_s = float(cmd.get("cuc_seconds",
                                  cmd.get("cuc", 0)))
            jump = self.set_obc_time(new_s, TIME_REF_GROUND)
            return {"success": True, "time_jump_s": jump,
                    "obc_time_s": self._state.obc_time_s,
                    "message": (f"OBC time set by ground to CUC {new_s:.3f}s "
                                f"(jump {jump:+.3f}s)")}

        elif command == "gps_time_sync":
            # S8 func 80. GPS is the spacecraft's window onto true time: the
            # engine's sim time IS GPS time here.
            #
            # Epoch bug (TIME_ARCHITECTURE.md Part B): this used to write
            # `int(sim_time.timestamp())` — a UNIX timestamp — into a J2000
            # field, a ~9.5e8 s jump on every single sync, which tripped the
            # 5-second-jump guard into AOCS SAFE_BOOT every time.
            self._ensure_clock_drift()
            sp = cmd.get("shared_params", {})
            gps_fix = sp.get(GPS_FIX_PARAM, 0.0)  # 0=none, 1=2D, 2=3D, 3=3D+vel
            if gps_fix < GPS_FIX_MIN:
                return {"success": False,
                        "message": "GPS time sync requires 3D fix "
                                   "(current fix type: %d)" % int(gps_fix)}
            sim_time = cmd.get("sim_time")
            if sim_time is not None:
                from smo_common.protocol.ecss_packet import datetime_to_cuc_seconds
                gps_time_s = float(datetime_to_cuc_seconds(sim_time))
            else:
                sim_cuc = self._sim_cuc_seconds()
                if sim_cuc is None:
                    return {"success": False,
                            "message": "GPS time sync: no time source available"}
                gps_time_s = sim_cuc
            # A GPS sync latches into disciplined mode only if configured to;
            # otherwise it is a one-shot set and the clock keeps drifting.
            new_ref = (TIME_REF_GPS if self._clock_gps_discipline
                       else TIME_REF_GROUND)
            time_jump_s = self.set_obc_time(gps_time_s, new_ref)
            return {"success": True, "time_jump_s": time_jump_s,
                    "obc_time_s": self._state.obc_time_s,
                    "time_ref": self._state.time_ref,
                    "message": (f"OBC time synced to GPS: CUC {gps_time_s:.3f}s "
                                f"(jump {time_jump_s:+.3f}s, ref "
                                f"{TIME_REF_NAMES.get(new_ref, '?')})")}

        elif command == "memory_scrub":
            self._state.scrub_active = True
            self._state.scrub_progress_pct = 0.0
            return {"success": True}

        elif command == "obc_reboot":
            self._reboot(REBOOT_COMMAND)
            return {"success": True}

        elif command == "obc_switch_unit":
            self._switchover()
            return {"success": True}

        elif command == "obc_select_bus":
            bus = int(cmd.get("bus", 0))
            if bus not in (0, 1):
                return {"success": False, "message": "Invalid bus (0=A, 1=B)"}
            # Check target bus status
            if bus == 0 and self._state.bus_a_status == BUS_FAILED:
                return {"success": False, "message": "Bus A is FAILED"}
            if bus == 1 and self._state.bus_b_status == BUS_FAILED:
                return {"success": False, "message": "Bus B is FAILED"}
            self._state.active_bus = bus
            return {"success": True}

        elif command == "obc_boot_app":
            s = self._state
            if s.sw_image == SW_APPLICATION:
                return {"success": False, "message": "Already in application"}
            s.boot_app_pending = True
            s.boot_app_timer = 10.0  # 10s CRC verification
            return {"success": True}

        elif command == "obc_boot_inhibit":
            inhibit = bool(cmd.get("inhibit", True))
            self._state.boot_inhibit = inhibit
            return {"success": True}

        elif command == "obc_clear_reboot_cnt":
            self._state.boot_count_a = 0
            self._state.boot_count_b = 0
            self._state.reboot_count = 0
            return {"success": True}

        elif command == "set_watchdog_period":
            period = int(cmd.get("period", 30))
            self._state.watchdog_period = max(1, period)
            return {"success": True}

        elif command == "watchdog_enable":
            self._state.watchdog_armed = True
            return {"success": True}

        elif command == "watchdog_disable":
            self._state.watchdog_armed = False
            return {"success": True}

        elif command == "diagnostic":
            s = self._state
            # Return a health summary as bytes
            health_data = struct.pack('>BfBBBBBBBf',
                s.mode,
                s.cpu_load,
                1 if s.watchdog_armed else 0,
                s.active_obc,
                s.obc_b_status,
                s.active_bus,
                s.bus_a_status,
                s.bus_b_status,
                s.sw_image,
                s.temp
            )
            return {"success": True, "data": health_data}

        elif command == "error_log":
            s = self._state
            # Return recent error entries: mem_errors, reboot_count, last_reboot_cause, seu_count
            error_data = struct.pack('>HHHH',
                s.mem_errors,
                s.reboot_count,
                s.last_reboot_cause,
                s.seu_count
            )
            return {"success": True, "data": error_data}

        return {"success": False, "message": f"Unknown: {command}"}

    # ─── Failure injection ───────────────────────────────────────────
    # Declarative parameter specs — single source for the instructor form,
    # FailureManager validation and the scenario content gate.
    FAILURE_SPECS = {
        "watchdog_reset": FailureSpec(
            name="watchdog_reset", label="Watchdog armed / about to bite",
            effect="Arms the watchdog with a full period; unless the "
                   "application kicks it, the OBC reboots.",
            magnitude=None, reversible=True,
        ),
        "obc_crash": FailureSpec(
            name="obc_crash", label="OBC crash (watchdog reboot)",
            effect="Immediate watchdog reboot: uptime resets, reboot counter "
                   "increments, cause = WATCHDOG, TM stops for the boot time.",
            magnitude=None, reversible=False,
            notes="One-shot: nothing to clear, the reboot already happened.",
        ),
        "memory_errors": FailureSpec(
            name="memory_errors", label="EDAC correctable memory errors",
            effect="Bumps the corrected-error counter; the operator sees the "
                   "EDAC rate climb without a reboot.",
            magnitude=None,
            params=(number("count", "Errors to add", unit="errors", lo=1,
                           hi=10000, default=5, integer=True),),
        ),
        "memory_corruption": FailureSpec(
            name="memory_corruption", label="Uncorrectable memory error",
            effect="Uncorrectable EDAC event: emits MEMORY_ERROR (0x0302) and "
                   "reboots the OBC.",
            magnitude=None, reversible=False,
            params=(number("count", "Errors to add", unit="errors", lo=1,
                           hi=10000, default=10, integer=True),),
        ),
        "memory_segment_fail": FailureSpec(
            name="memory_segment_fail", label="Mass-memory segment failure",
            effect="Permanently fails one of the 4 OBC memory segments; "
                   "usable capacity shrinks and mmm_used_pct rises.",
            magnitude=None,
            params=(enum("segment", "Memory segment", [0, 1, 2, 3],
                         labels=["Segment 0", "Segment 1", "Segment 2",
                                 "Segment 3"], default=0),),
        ),
        "cpu_spike": FailureSpec(
            name="cpu_spike", label="CPU load spike",
            effect="Holds the OBC CPU load at the given percentage; task "
                   "overruns and buffer backlog follow.",
            magnitude=None,
            params=(number("load", "CPU load", unit="%", lo=0.0, hi=100.0,
                           default=95.0, step=1.0),),
        ),
        "bus_failure": FailureSpec(
            name="bus_failure", label="CAN bus failure",
            effect="Marks one of the redundant CAN buses FAILED; the bus "
                   "isolation logic must cross over to the other.",
            magnitude=None,
            params=(enum("bus", "CAN bus", ["A", "B"],
                         labels=["Bus A (nominal)", "Bus B (redundant)"],
                         default="A"),),
        ),
        "boot_image_corrupt": FailureSpec(
            name="boot_image_corrupt", label="Application image corrupt",
            effect="The application image fails its CRC — the NEXT boot stops "
                   "in the bootloader.",
            magnitude=None,
        ),
        "stuck_in_bootloader": FailureSpec(
            name="stuck_in_bootloader", label="Stuck in bootloader",
            effect="Drops the OBC into the bootloader image now and inhibits "
                   "the application boot: sw_image = 0 immediately.",
            magnitude=None,
        ),
        "clock_drift": FailureSpec(
            name="clock_drift", label="Onboard clock oscillator drift",
            effect="Forces the OBC oscillator drift rate. The onboard clock "
                   "walks away from UTC until a GPS/S9 time sync pulls it back "
                   "(500 ppm is about 43 s/day; nominal is about 12 ppm).",
            magnitude=ParamSpec(
                name="magnitude", type=T_FLOAT,
                label="Severity (x the 500 ppm failure drift)",
                unit="fraction", min=0.0, max=10.0, default=1.0, step=0.05,
                description="Ignored when an explicit ppm is given."),
            params=(number("ppm", "Drift rate", unit="ppm", lo=-100000.0,
                           hi=100000.0, step=1.0,
                           description="Absolute drift; overrides magnitude."),),
        ),
    }

    def inject_failure(self, failure: str, magnitude: float = 1.0,
                       **kw) -> None:
        s = self._state

        if failure == "watchdog_reset":
            s.watchdog_armed = True
            s.watchdog_timer = s.watchdog_period

        elif failure == "memory_errors":
            s.mem_errors += int(kw.get("count", 5))

        elif failure == "cpu_spike":
            self._cpu_base = float(kw.get("load", 95.0))

        elif failure == "obc_crash":
            # Simulate OBC crash -> watchdog timeout -> boot loader
            self._reboot(REBOOT_WATCHDOG)

        elif failure == "bus_failure":
            bus = kw.get("bus", "A").upper()
            if bus == "A":
                s.bus_a_status = BUS_FAILED
            elif bus == "B":
                s.bus_b_status = BUS_FAILED

        elif failure == "boot_image_corrupt":
            s.boot_image_corrupt = True

        elif failure == "clock_drift":
            # Force a large oscillator drift so the clock error is visible
            # inside a training session (order 500 ppm ≈ 43 s/day) instead of
            # the nominal ~12 ppm ≈ 1 s/day.
            self._ensure_clock_drift()
            if self._clock_drift_saved is None:
                self._clock_drift_saved = s.clock_drift_ppm
            ppm = kw.get("ppm")
            if ppm is None:
                ppm = self._clock_drift_failure_ppm * max(0.0, float(magnitude))
            s.clock_drift_ppm = float(ppm)

        elif failure == "memory_corruption":
            # EDAC uncorrectable error — triggers reboot
            s.mem_errors += int(kw.get("count", 10))
            self._event_to_emit = (0x0302, "MEMORY_ERROR")
            self._reboot(REBOOT_MEMORY_ERROR)

        elif failure == "memory_segment_fail":
            # Permanent failure of a single mass-memory segment.
            # Drives the memory_segment_failure contingency procedure.
            seg = int(kw.get("segment", 0))
            if 0 <= seg < len(s.memory_segments):
                s.memory_segments[seg] = False
                # Increment uncorrectable error counter, raise mmm fill so the
                # operator can see capacity has shrunk.
                s.mem_errors += 1
                healthy = sum(1 for h in s.memory_segments if h)
                total = len(s.memory_segments)
                # Effective usage rises proportionally to the lost capacity.
                if total > 0 and healthy < total:
                    shrink = (total - healthy) / total
                    s.mmm_used_pct = min(100.0, s.mmm_used_pct + shrink * 100.0 / total)
                self._event_to_emit = (0x0302, "MEMORY_SEGMENT_FAIL")

        elif failure == "stuck_in_bootloader":
            # Persistent stuck-in-bootloader state — boot_image_corrupt is set
            # AND we drop the OBC into the bootloader image right now so the
            # operator sees sw_image=0 immediately and the recovery procedure
            # can be drilled without waiting for a reboot.
            s.boot_image_corrupt = True
            s.sw_image = SW_BOOTLOADER
            s.boot_app_pending = False
            s.boot_inhibit = True
            self._event_to_emit = (0x0305, "BOOT_FAILURE")

    def clear_failure(self, failure: str, **kw) -> None:
        s = self._state

        if failure == "watchdog_reset":
            # One-shot — just disarm the timer so it doesn't fire again.
            s.watchdog_timer = 0

        elif failure == "memory_errors":
            # Errors that already happened stay counted, but mark the inject
            # "cleared" by clamping the running counter (operator-driven scrub).
            s.mem_errors = max(0, s.mem_errors - int(kw.get("count", 5)))

        elif failure == "cpu_spike":
            self._cpu_base = self._cpu_base_nominal

        elif failure == "bus_failure":
            bus = kw.get("bus", "A").upper()
            if bus == "A":
                s.bus_a_status = BUS_OK
            elif bus == "B":
                s.bus_b_status = BUS_OK

        elif failure == "boot_image_corrupt":
            s.boot_image_corrupt = False

        elif failure == "clock_drift":
            # Back to this unit's own drawn drift.
            if self._clock_drift_saved is not None:
                s.clock_drift_ppm = self._clock_drift_saved
                self._clock_drift_saved = None

        elif failure == "memory_corruption":
            s.mem_errors = 0

        elif failure == "obc_crash":
            pass  # Crash is a one-time event, no persistent state

        elif failure == "memory_segment_fail":
            seg = int(kw.get("segment", 0))
            if 0 <= seg < len(s.memory_segments):
                s.memory_segments[seg] = True

        elif failure == "stuck_in_bootloader":
            s.boot_image_corrupt = False
            s.boot_inhibit = False

    # Model-level (non-dataclass) entries carried in the state dict. Every one
    # of these is state that must NOT silently heal across a breakpoint
    # save/restore — an injected clock_drift that reverted to the drawn value
    # on restore would be exactly the gyro_bias bug of the last batch.
    _EXTRA_STATE_KEYS = ("_rng_state", "_clock_drift_drawn",
                         "_clock_drift_saved", "_uptime_acc",
                         "_prev_gps_fix_ok", "_prev_obc_line_on",
                         "_cpu_base", "_cpu_base_nominal")

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        from smo_common.state import rng_state_to_json
        d = dataclasses.asdict(self._state)
        d["_rng_state"] = rng_state_to_json(self.rng.getstate())
        # obc_time_s / time_ref / last_sync_s / clock_drift_ppm are dataclass
        # fields and ride along in asdict(); these are the model-level ones.
        d["_clock_drift_drawn"] = bool(self._clock_drift_drawn)
        d["_clock_drift_saved"] = (None if self._clock_drift_saved is None
                                   else float(self._clock_drift_saved))
        d["_uptime_acc"] = float(self._uptime_acc)
        d["_prev_gps_fix_ok"] = bool(self._prev_gps_fix_ok)
        d["_prev_obc_line_on"] = self._prev_obc_line_on
        # `cpu_spike` lives ONLY here — not in the dataclass — so a snapshot
        # that dropped it healed an injected CPU overload on restore.
        d["_cpu_base"] = float(self._cpu_base)
        d["_cpu_base_nominal"] = float(self._cpu_base_nominal)
        return d

    def set_state(self, state: dict[str, Any]) -> None:
        # NOTE: must not mutate `state` — it may be a stored breakpoint dict
        # that will be loaded again.
        # Container fields are deep-copied by restore_dataclass_state: the
        # plain setattr this used to do aliased the snapshot's own dicts and
        # lists into live model state (G2-f).
        from smo_common.state import restore_dataclass_state, restore_rng
        restore_rng(self.rng, state.get("_rng_state"))
        self._clock_drift_drawn = bool(state.get("_clock_drift_drawn", False))
        saved = state.get("_clock_drift_saved")
        self._clock_drift_saved = None if saved is None else float(saved)
        self._uptime_acc = float(state.get("_uptime_acc",
                                           state.get("uptime_s", 0) or 0))
        self._prev_gps_fix_ok = bool(state.get("_prev_gps_fix_ok", False))
        self._prev_obc_line_on = state.get("_prev_obc_line_on")
        self._cpu_base_nominal = float(state.get("_cpu_base_nominal",
                                                 self._cpu_base_nominal))
        self._cpu_base = float(state.get("_cpu_base", self._cpu_base))
        restore_dataclass_state(self._state, state,
                                skip=self._EXTRA_STATE_KEYS)

    # S2 Device Access — device-level on/off control
    def set_device_state(self, device_id: int, on_off: bool) -> bool:
        """Set device on/off state. Returns True if successful."""
        if device_id not in self._state.device_states:
            return False
        self._state.device_states[device_id] = on_off
        return True

    def get_device_state(self, device_id: int) -> bool:
        """Get device on/off state. Returns True if on, False if off or invalid."""
        return self._state.device_states.get(device_id, False)
