"""SMO Simulator — Enhanced TT&C Model.

Link budget with BER/Eb-N0, PA thermal model with auto-shutdown,
lock acquisition sequence (carrier->bit->frame), dual transponder,
and comprehensive failure modes.
"""
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any

from smo_common.models.subsystem import SubsystemModel

logger = logging.getLogger(__name__)


@dataclass
class TTCState:
    mode: int = 0  # 0=primary, 1=redundant
    link_active: bool = False
    rssi_dbm: float = -120.0
    link_margin_db: float = 0.0
    # Information rate in bps of the *active* tier. This is the tier's `bps`
    # exactly — modulation changes the SYMBOL rate and the occupied bandwidth,
    # never the bit rate (defect #85).
    tm_data_rate: int = 16000
    xpdr_temp: float = 28.0
    ranging_active: bool = False
    range_km: float = 0.0
    elevation_deg: float = -90.0
    azimuth_deg: float = 0.0
    primary_failed: bool = False
    redundant_failed: bool = False

    # ── PA model ──
    pa_on: bool = True
    pa_temp: float = 35.0
    pa_overheat_shutdown: bool = False  # Auto-shutdown at 70C
    tx_fwd_power: float = 2.0  # Watts
    # Phase E WP-2: last commanded TTC_SET_TX_POWER level (0=LOW, 1=NOMINAL,
    # 2=HIGH); 1 at power-up (pa_nominal_power_w).
    tx_power_level: int = 1
    # Live EIRP (dBW) at the commanded PA power. Derived, not configured —
    # see _effective_eirp_dbw(). Reported at 0x052C so an operator can SEE
    # that TTC_SET_TX_POWER did something to the link (2026-08b, T-b).
    eirp_dbw: float = 10.0

    # ── S-band downlink gate (2026-08b, finding T-a) ────────────────────
    # THE parameter that says whether the prime chain is radiating. Before
    # this existed, NOTHING anywhere in the TM chain read the transmitter
    # state: `pa_on` was read in four places (tx_fwd_power reporting, the
    # link-budget branch, the PA thermal load and HK 0x0516) and none of them
    # was on the path a telemetry packet takes. The engine's `downlink_active`
    # gated on 0x0501 link_status, which is derived from frame_sync — i.e. the
    # UPLINK RECEIVE chain — so it was a receiver-lock gate wearing a downlink
    # label, and telemetry flowed happily with the PA off and 0.0 W of forward
    # power. `tx_active` is the real gate: PA up AND the EPS ttc_tx line hot
    # AND a station in view.
    tx_active: bool = False
    # Last seen state of the EPS `ttc_tx` power line (shared param 0x0112).
    # EPS_POWER_LINE_OFF('ttc_tx') used to set `eps._state.transponder_tx`,
    # which had no readers anywhere in the repo — a dead flag. The TT&C model
    # now reads the live line state, so pulling the line really does stop the
    # downlink.
    ttc_tx_line_on: bool = True

    # ── UHF bootloader beacon TRANSMITTER (Franz's ruling R2) ───────────
    # A SECOND RADIO. Not `beacon_mode` (which is a rung of the S-band rate
    # ladder, further down this dataclass) and not the BEACON tier. Its own
    # PA, band, antenna, budget and power draw; carries HK SID 11 and nothing
    # else; survives the S-band PA being switched off, which is the whole
    # reason a real spacecraft carries one.
    beacon_tx_on: bool = True        # commanded state (TTC_BEACON_TX_ON/OFF)
    beacon_active: bool = False      # actually radiating to a station now
    beacon_margin_db: float = 0.0    # UHF link margin, its own budget
    beacon_eb_n0_db: float = 0.0
    beacon_tx_power_w: float = 0.0   # RF watts out of the UHF PA right now
    beacon_temp: float = 25.0        # its own (small) thermal contribution
    _beacon_timer: float = 0.0       # seconds since the last beacon packet

    # Last seen OBDH software image (0x0311). Used to detect the
    # bootloader -> application transition, which un-inhibits the S-band PA
    # (2026-08b, finding 3). -1 = not yet observed.
    _prev_sw_image: int = -1

    # ── BER / Eb-N0 ──
    ber: float = -10.0  # log10 scale (1e-10)
    eb_n0: float = 20.0  # dB

    # ── Lock acquisition sequence ──
    carrier_lock: bool = False
    bit_sync: bool = False
    frame_sync: bool = False
    _lock_timer: float = 0.0  # Seconds since AOS
    _carrier_lock_delay: float = 2.0
    _bit_sync_delay: float = 5.0  # cumulative from AOS
    _frame_sync_delay: float = 10.0  # cumulative from AOS

    # ── Lock state tracking for edge detection ──
    _prev_carrier_lock: bool = False
    _prev_bit_sync: bool = False
    _prev_frame_sync: bool = False
    _prev_ranging_active: bool = False

    # ── Counters ──
    cmd_rx_count: int = 0

    # ── Phase 4: Flight hardware realism ──
    agc_level_db: float = -60.0     # Automatic gain control level
    doppler_hz: float = 0.0         # Doppler shift on downlink
    range_rate_m_s: float = 0.0     # Range rate to ground station
    cmd_auth_status: int = 1        # 0=disabled, 1=enabled, 2=locked out
    total_bytes_tx: int = 0         # Total bytes transmitted this pass
    total_bytes_rx: int = 0         # Total bytes received this pass

    # ── Dedicated command channel / deployment ──
    cmd_decode_timer: float = 0.0       # 15-min countdown (900 s)
    cmd_channel_active: bool = False     # True while timer is running
    antenna_deployed: bool = False       # True after burn-wire deployment
    beacon_mode: bool = False            # True in bootloader beacon mode

    # ── TM data-rate ladder (defect #85) ──
    # `data_rate_mode` is the COMMANDED tier index over ttc.yaml's
    # tm_rate_tiers ladder (0=BEACON, 1=LOW, 2=HIGH by default).
    # It stays authoritative across ticks — that is what defect #74 fixed and
    # what must not regress. What tick() may change is `tm_tier`, the tier
    # actually ACTIVE once the permission gates (beacon mode, antenna stowed,
    # DOWNLINK mode, elevation) have been applied; the commanded value is
    # remembered so the tier resumes when the gate clears.
    data_rate_mode: int = 1              # commanded tier index (LOW)
    tm_tier: int = 1                     # active tier index
    downlink_mode: bool = False          # True in DOWNLINK / payload-dump mode

    # ── Antenna deployment sensors (DEFECT FIX #4) ──
    antenna_deployment_ready: bool = True  # True if pyro continuity OK
    antenna_deployment_sensor: int = 1     # 0=unknown, 1=stowed, 2=deployed, 3=partial/jammed
    _antenna_deploy_last_time: float = 0.0  # Timestamp of last deploy attempt

    # ── Link quality thresholds for events ──
    _prev_pa_temp_warning: bool = False
    _prev_pa_overheat_shutdown: bool = False
    _prev_link_margin_warning: bool = False
    _prev_link_margin_critical: bool = False
    _prev_ber_exceeded: bool = False
    _uplink_timeout_counter: float = 0.0
    uplink_timeout_threshold: float = 300.0  # 5 minutes default

    # ── Transponder switch tracking ──
    _prev_mode: int = 0

    # ── Failure injection ──
    ber_inject_offset: float = 0.0  # dB reduction in Eb/N0
    pa_heat_inject: float = 0.0  # Extra heat W
    uplink_lost: bool = False
    receiver_nf_degrade: float = 0.0  # dB noise figure increase

    # ── Ground segment failure injection ──
    # These model ground equipment degradation that affects the link
    # budget from the ground side. Injected via the instructor interface.
    gs_lna_degrade_db: float = 0.0          # LNA noise figure increase
    gs_antenna_mispoint_db: float = 0.0     # Antenna pointing loss
    gs_feed_loss_db: float = 0.0            # Feed/waveguide loss
    gs_rfi_db: float = 0.0                  # RF interference noise rise
    gs_hpa_degrade_db: float = 0.0          # Uplink HPA power reduction
    gs_ref_osc_drift_db: float = 0.0        # Reference oscillator drift
    gs_tracking_loss_db: float = 0.0        # Tracking system failure

    # ── Modulation ──
    modulation_mode: int = 0  # 0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK

    # S2 Device Access — device on/off states (device_id -> on/off)
    device_states: dict = field(default_factory=lambda: {
        0x0400: True,   # Transponder A
        0x0401: True,   # Transponder B
        0x0402: True,   # Power amplifier
        0x0403: True,   # LNA (low noise amplifier)
        0x0404: True,   # Antenna drive
    })


class TTCBasicModel(SubsystemModel):
    """Enhanced TTC with BER, PA thermal, lock sequence, and new failures."""

    def __init__(self):
        self._state = TTCState()
        # Per-model RNG (plan A-2). Standalone construction gets a fresh
        # unseeded stream so existing unit tests keep working; the engine
        # replaces this with a Random derived from simulation.yaml `seed`.
        import random as _random
        self.rng = _random.Random(20260731)  # deterministic standalone fallback; engine overrides via derive_rng
        self._param_ids: dict[str, int] = {}
        self._eirp = 10.0
        self._gs_gt = 20.0
        self._sc_gain = 3.0
        self._dl_freq_hz = 2200.5e6
        self._min_el = 5.0
        # ── TM data-rate ladder (defect #85) ──
        self._tiers: list[dict[str, Any]] = [
            dict(t) for t in self.DEFAULT_TM_RATE_TIERS
        ]
        self._default_tier = 1   # LOW — routine operations
        self._beacon_tier = 0    # BEACON — bootloader only
        self._safe_tier = 1      # LOW — safe mode / FDIR / antenna stowed
        # The carrier->bit->frame sequence is an UPLINK lock paced by the
        # uplink command rate, NOT the downlink TM rate. It must NOT default
        # to the top TM tier: that would make the telecommand uplink 1 Mbps.
        self._tc_uplink_rate_bps = 4000
        self._ul_freq_mhz = 449.0
        self._coding_gain = 3.0  # dB (convolutional + RS) — ladder default
        # Link-budget losses (mirror rfsim.yaml link_budget)
        self._atmospheric_loss = 0.5
        self._polarization_loss = 0.3
        self._implementation_loss = 2.0
        self._gt_derate_10 = 1.5
        self._gt_derate_30 = 0.5
        self._required_eb_n0 = 9.6
        self._applied_tier: int | None = None
        self._pa_max_power_w = 5.0
        self._pa_low_power_w = 1.0
        self._pa_nominal_power_w = 2.0
        # RF watts the config's eirp_dbw was quoted at. EIRP scales as
        # 10*log10(P / P_ref) about this point, which is what makes
        # TTC_SET_TX_POWER a real command instead of a telemetry change.
        self._pa_ref_power_w = 2.0
        self._pa_shutdown_temp = 70.0
        self._pa_tau = 60.0  # PA thermal time constant (seconds)
        # ── UHF beacon transmitter (ruling R2) ──
        self._beacon_cfg: dict[str, Any] = dict(self.DEFAULT_BEACON)
        # Safe-mode edge detection: entering SC_SAFE must actually COMMAND the
        # configured safe tier. ttc.yaml documents LOW as "safe mode, FDIR
        # fallback" but `_safe_tier` was only ever read in `_effective_tier`'s
        # last-resort branch, so safe-mode entry left the commanded tier
        # wherever the operator had put it (2026-08b, finding 4).
        self._prev_sc_safe: bool | None = None
        self._was_in_contact = False

    # S-band near-Earth allocations (CCSDS 401.0-B). These are the bands the
    # ground stations, the RF simulator, ttc.yaml and the textbook all use;
    # set_ul_freq / set_dl_freq validate against them (roadmap N-4).
    UL_BAND_MHZ = (2025.0, 2110.0)
    DL_BAND_MHZ = (2200.0, 2290.0)

    # Modulation scheme parameters:
    # bits_per_symbol, spectral_efficiency_factor, required_eb_n0_offset_db
    MODULATION_PARAMS = {
        0: {"name": "BPSK",  "bits_per_symbol": 1, "eb_n0_penalty": 0.0},
        1: {"name": "QPSK",  "bits_per_symbol": 2, "eb_n0_penalty": 0.0},
        2: {"name": "8PSK",  "bits_per_symbol": 3, "eb_n0_penalty": 3.6},
        3: {"name": "OQPSK", "bits_per_symbol": 2, "eb_n0_penalty": 0.0},
    }

    MODULATION_INDEX = {"BPSK": 0, "QPSK": 1, "8PSK": 2, "OQPSK": 3}

    # Uplink (TC) lock-acquisition pacing thresholds. The nominal TC rate is
    # 4000 bps (Sentinel-1's) and gives the nominal ~11 s lock; a beacon-rate
    # 1 kbps uplink triples it, the 64 kbps software-upload rate speeds it up.
    # These used to be written against the TM ladder's lo/hi rates, which is
    # wrong now the uplink no longer inherits from the TM ladder.
    TC_SLOW_LOCK_RATE_BPS = 1000
    TC_FAST_LOCK_RATE_BPS = 64000

    # ── TM data-rate ladder (defect #85) ────────────────────────────────
    # Fallback used only when `configure()` is given no `tm_rate_tiers`.
    # `configs/eosat1/subsystems/ttc.yaml` is the source of truth; these
    # defaults exist so a bare TTCBasicModel() still has a sane ladder.
    DEFAULT_TM_RATE_TIERS: list[dict[str, Any]] = [
        {"name": "BEACON", "bps": 1000, "command_code": 0xFF,
         "modulation": "BPSK", "coding": "CONV_RS", "coding_gain_db": 7.5,
         "beacon_only": True, "requires_antenna": False},
        {"name": "LOW", "bps": 16000, "command_code": 0x00,
         "modulation": "BPSK", "coding": "CONV_RS", "coding_gain_db": 7.5,
         "requires_antenna": False},
        {"name": "HIGH", "bps": 128000, "command_code": 0x01,
         "modulation": "QPSK", "coding": "RS", "coding_gain_db": 6.0,
         "requires_antenna": True, "requires_downlink_mode": True,
         "min_elevation_deg": 10.0},
    ]

    # ── UHF bootloader beacon transmitter (Franz's ruling R2) ───────────
    # Fallback used only when `configure()` is given no `beacon` block;
    # `configs/eosat1/subsystems/ttc.yaml` is the source of truth and cites
    # the flight hardware behind every number.
    DEFAULT_BEACON: dict[str, Any] = {
        "enabled": True,
        "dl_freq_mhz": 437.5, "band_mhz": (435.0, 438.0),
        "rf_power_w": 1.0, "sc_gain_dbi": 0.0, "gs_g_t_db": -8.0,
        "bps": 1000, "coding_gain_db": 7.5, "required_eb_n0_db": 9.6,
        "atmospheric_loss_db": 0.2, "polarization_loss_db": 3.0,
        "implementation_loss_db": 2.0,
        "dc_power_w": 1.8, "standby_dc_power_w": 0.2,
        "eps_line": "ttc_rx", "hk_sid": 11,
    }

    # ── Ladder helpers ──────────────────────────────────────────────────

    @property
    def _tm_rate_lo(self) -> int:
        """Deprecated alias — the safe-mode tier's bps (LOW, 16 kbps).

        Kept so out-of-lane readers keep resolving. New code should use
        ``tier_bps(model._safe_tier)`` or a tier name.
        """
        return self.tier_bps(self._safe_tier)

    @property
    def _tm_rate_hi(self) -> int:
        """Deprecated alias — the top tier's bps (HIGH, 1.024 Mbps)."""
        return self.tier_bps(len(self._tiers) - 1)

    @property
    def tiers(self) -> list[dict[str, Any]]:
        """The configured TM rate ladder, ascending in bps."""
        return self._tiers

    def tier(self, index: int) -> dict[str, Any]:
        i = max(0, min(len(self._tiers) - 1, int(index)))
        return self._tiers[i]

    def tier_index(self, name: str) -> int:
        """Index of a tier by (case-insensitive) name; -1 if unknown."""
        key = str(name).strip().upper()
        for i, t in enumerate(self._tiers):
            if str(t.get("name", "")).upper() == key:
                return i
        return -1

    def tier_name(self, index: int) -> str:
        return str(self.tier(index).get("name", f"TIER{index}"))

    def tier_bps(self, index: int) -> int:
        return int(self.tier(index).get("bps", 1000))

    def tier_symbol_rate(self, index: int) -> float:
        """Symbol rate (Bd) of a tier = bps / bits-per-symbol.

        The tier's `bps` is the INFORMATION rate. Changing modulation moves
        the symbol rate and therefore the occupied bandwidth — it must never
        change the bit rate, which is what the old
        ``tm_data_rate = base_rate * bits_per_sym`` did (it reported 128 kbps
        NOMINAL as 256 kbps the moment QPSK was selected).
        """
        t = self.tier(index)
        mod = self.MODULATION_INDEX.get(str(t.get("modulation", "BPSK")).upper(), 0)
        bps_per_sym = self.MODULATION_PARAMS.get(
            mod, {"bits_per_symbol": 1})["bits_per_symbol"]
        return float(t["bps"]) / max(bps_per_sym, 1)

    def tier_occupied_bw_hz(self, index: int,
                            rolloff: float = 0.35) -> float:
        """Occupied bandwidth (Hz) = (1 + alpha) x symbol rate, including the
        RS(255,223) and TM-transfer-frame expansion and the r=1/2
        convolutional expansion when the tier codes with it. Used to check
        the 5.00 MHz per-mission allocation in 2200-2290 MHz."""
        t = self.tier(index)
        rs_expand = 255.0 / 223.0 if str(t.get("coding", "RS")).upper() != "NONE" else 1.0
        frame_expand = 1115.0 / 1107.0
        conv_expand = 2.0 if str(t.get("coding", "RS")).upper() == "CONV_RS" else 1.0
        return (self.tier_symbol_rate(index) * rs_expand * frame_expand
                * conv_expand * (1.0 + rolloff))

    # ── Link-budget helpers (defect #84) ────────────────────────────────

    def _atmospheric_loss_at(self, elevation_deg: float) -> float:
        """Clear-sky atmospheric loss (dB) at the given elevation.

        `atmospheric_loss_db` is the >=30 deg figure; below that it is scaled
        by cosec(elevation) — 0.5 * sin(30) / sin(eps) — giving 1.44 dB at
        10 deg and 2.87 dB at 5 deg. The old model applied no atmospheric,
        polarisation or implementation loss at all, even though rfsim.yaml's
        link_budget section defines all three.
        """
        el = float(elevation_deg)
        if el >= 30.0:
            return self._atmospheric_loss
        el = max(el, 1.0)  # numerically safe at/below the horizon
        return self._atmospheric_loss * math.sin(math.radians(30.0)) / \
            math.sin(math.radians(el))

    def _gt_derate_db(self, elevation_deg: float) -> float:
        """G/T de-rate (dB) for ground/sky noise pickup at low elevation."""
        el = float(elevation_deg)
        if el < 10.0:
            return self._gt_derate_10
        if el < 30.0:
            return self._gt_derate_30
        return 0.0

    # ── EIRP from the commanded PA power (2026-08b, finding T-b) ────────

    def _effective_eirp_dbw(self, power_w: float | None = None) -> float:
        """S-band EIRP (dBW) at the CURRENTLY COMMANDED PA power.

        ``eirp_dbw`` in ttc.yaml is the REFERENCE EIRP, quoted at
        ``pa_ref_power_w`` watts of RF output (2.0 W = 33 dBm, the EnduroSat
        S-band transceiver's top of range and the point POWER_SIZING.md §2.3
        sized the ttc_tx line's HIGH tier against). A PA that is commanded to
        a different operating point radiates a different EIRP, and the
        difference is exactly 10*log10(P/P_ref):

            LOW      1.0 W  ->  -3.01 dB
            NOMINAL  2.0 W  ->   0.00 dB   (the reference)
            HIGH     5.0 W  ->  +3.98 dB

        Before this, ``_eirp`` was a fixed config constant used at :334 and
        :801 and never derived from ``_pa_nominal_power_w``, so
        TTC_SET_TX_POWER (S8 func 68) moved telemetry 0x050D and nothing
        else — margin, Eb/N0, BER and the whole RF pipeline were identical at
        every level, and FDIR's `ttc_reduce_power` "reduce TX power" recovery
        was a no-op on the link it exists to protect.
        """
        p = self._pa_nominal_power_w if power_w is None else float(power_w)
        ref = max(float(self._pa_ref_power_w), 1e-6)
        if p <= 0.0:
            # A PA commanded to zero radiates nothing. Clamp rather than
            # return -inf so downstream arithmetic stays finite; the real
            # "not transmitting" answer is `tx_active`, not a small EIRP.
            return self._eirp - 60.0
        return self._eirp + 10.0 * math.log10(p / ref)

    # ── UHF beacon link budget (Franz's ruling R2) ──────────────────────

    def beacon_link_budget(self, range_km: float,
                           elevation_deg: float) -> dict[str, float]:
        """Deterministic link budget for the UHF beacon transmitter.

        Deliberately a SEPARATE method from `link_margin_for`, with its own
        frequency, EIRP, antenna gain, G/T and loss column, because it is a
        separate radio talking to a separate ground antenna. Sharing the
        S-band routine "with a few parameters overridden" is how the beacon
        came to be modelled as rung 0 of the S-band ladder in the first place.

        The contrast against the S-band budget at the same geometry is the
        teaching point (docs/LINK_BUDGET_AND_RATES.md §9.3): UHF wins ~14 dB
        of path loss and gives back ~28 dB of G/T, ~3 dB of spacecraft
        antenna gain and ~2.7 dB of polarisation mismatch.
        """
        b = self._beacon_cfg
        f_hz = float(b["dl_freq_mhz"]) * 1e6
        fspl = (20 * math.log10(max(range_km, 1e-3) * 1000.0)
                + 20 * math.log10(f_hz) - 147.55)
        # Same cosec(elevation) scaling law as the S-band chain, applied to
        # the beacon's own (much smaller) zenith figure.
        el = max(float(elevation_deg), 1.0)
        atm = float(b["atmospheric_loss_db"])
        if el < 30.0:
            atm = atm * math.sin(math.radians(30.0)) / math.sin(math.radians(el))
        losses = (atm + float(b["polarization_loss_db"])
                  + float(b["implementation_loss_db"]))
        gt_eff = float(b["gs_g_t_db"]) - self._gt_derate_db(elevation_deg)
        eirp = 10.0 * math.log10(max(float(b["rf_power_w"]), 1e-6))
        c_n0 = eirp + float(b["sc_gain_dbi"]) - fspl - losses + gt_eff + 228.6
        eb_n0 = c_n0 - 10 * math.log10(max(float(b["bps"]), 1.0))
        required = float(b["required_eb_n0_db"]) - float(b["coding_gain_db"])
        return {
            "eirp_dbw": eirp,
            "fspl_db": fspl,
            "losses_db": losses,
            "gt_eff_db": gt_eff,
            "c_n0_dbhz": c_n0,
            "eb_n0_db": eb_n0,
            "required_eb_n0_db": required,
            "margin_db": eb_n0 - required,
        }

    def link_margin_for(self, tier_index: int, range_km: float,
                        elevation_deg: float,
                        gs_gt_db: float | None = None) -> dict[str, float]:
        """Deterministic, noise-free link budget for one tier and geometry.

        Exposed so tools/tests can reproduce the researched arithmetic
        (link_rate_research.md sections B.2-B.7) without ticking the model.
        """
        t = self.tier(tier_index)
        gt = self._gs_gt if gs_gt_db is None else float(gs_gt_db)
        fspl = (20 * math.log10(max(range_km, 1e-3) * 1000.0)
                + 20 * math.log10(self._dl_freq_hz) - 147.55)
        losses = (self._atmospheric_loss_at(elevation_deg)
                  + self._polarization_loss + self._implementation_loss)
        gt_eff = gt - self._gt_derate_db(elevation_deg)
        # EIRP at the COMMANDED PA power, not the config constant — so the
        # tool, the tests and the flying model all move together when
        # TTC_SET_TX_POWER is sent (2026-08b, finding T-b).
        c_n0 = (self._effective_eirp_dbw() + self._sc_gain
                - fspl - losses + gt_eff + 228.6)
        eb_n0 = c_n0 - 10 * math.log10(float(t["bps"]))
        cg = float(t.get("coding_gain_db", self._coding_gain))
        mod = self.MODULATION_INDEX.get(
            str(t.get("modulation", "BPSK")).upper(), 0)
        mod_penalty = self.MODULATION_PARAMS.get(
            mod, {"eb_n0_penalty": 0.0})["eb_n0_penalty"]
        required = self._required_eb_n0 + mod_penalty - cg
        return {
            "fspl_db": fspl,
            "losses_db": losses,
            "gt_eff_db": gt_eff,
            "c_n0_dbhz": c_n0,
            "eb_n0_db": eb_n0,
            "coding_gain_db": cg,
            "required_eb_n0_db": required,
            "margin_db": eb_n0 - required,
        }

    def _decode_tier_arg(self, raw: Any) -> "int | None":
        """Decode a `set_tm_rate` argument into a tier index.

        Accepts, in this order:
          * a tier NAME ("NOMINAL", case-insensitive);
          * a tier `command_code` — the uint8 the catalogue advertises;
          * a raw bps value equal to one of the configured tier rates.

        The legacy binary encoding survives verbatim because ttc.yaml gives
        LOW `command_code: 0x00` and HIGH `command_code: 0x01`. Every
        procedure, OBCP and FDIR action that already sends 0 ("low") or 1
        ("high") therefore keeps its meaning, and now lands on 16 kbps /
        128 kbps instead of the unusable 1 kbps beacon and 64 kbps. BEACON's
        code is 0xFF: it is not selectable by telecommand in nominal software.
        """
        if isinstance(raw, str):
            key = raw.strip()
            idx = self.tier_index(key)
            if idx >= 0:
                return idx
            try:
                raw = int(key, 0)
            except ValueError:
                return None
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return None
        for i, t in enumerate(self._tiers):
            if val == int(t.get("command_code", i)):
                return i
        for i, t in enumerate(self._tiers):
            if val == int(t["bps"]):
                return i
        return None

    def _tier_permission_error(self, index: int,
                               s: "TTCState | None" = None) -> str | None:
        """Return a human-readable reason the tier may NOT be selected in the
        current spacecraft state, or None when it is permitted.

        A tier that is not permitted is REJECTED (S1.8, error 0x0007) — never
        silently clamped to something else.
        """
        s = s if s is not None else self._state
        if index < 0 or index >= len(self._tiers):
            return f"Invalid TM rate tier {index}"
        t = self._tiers[index]
        name = t.get("name", index)
        if t.get("beacon_only") and not s.beacon_mode:
            return (f"{name} is a bootloader/beacon-only tier "
                    f"({t['bps']} bps); not selectable in nominal operations")
        if s.beacon_mode and not t.get("beacon_only"):
            return (f"TM rate is forced to the beacon tier in beacon mode "
                    f"({name} refused)")
        if t.get("requires_antenna", True) and not s.antenna_deployed:
            return f"{name} ({t['bps']} bps) requires a deployed antenna"
        if t.get("requires_downlink_mode") and not s.downlink_mode:
            return (f"{name} ({t['bps']} bps) requires DOWNLINK/dump mode "
                    f"(TTC_SET_DOWNLINK_MODE)")
        min_el = float(t.get("min_elevation_deg", 0.0) or 0.0)
        if min_el > 0.0 and s.link_active and s.elevation_deg < min_el:
            return (f"{name} ({t['bps']} bps) requires elevation >= "
                    f"{min_el:.0f} deg (now {s.elevation_deg:.1f} deg)")
        return None

    def _effective_tier(self, s: "TTCState") -> int:
        """Resolve the ACTIVE tier from the commanded tier and the gates.

        The commanded tier (`data_rate_mode`) is never overwritten here — a
        gate only demotes the *active* tier, so the commanded tier resumes as
        soon as the gate clears (e.g. HIGH auto-reverts to NOMINAL at LOS or
        below the elevation gate, and comes back on the next pass).
        """
        if s.beacon_mode:
            return self._beacon_tier
        idx = max(0, min(len(self._tiers) - 1, int(s.data_rate_mode)))
        # Never leave a non-beacon spacecraft sitting on a beacon-only tier.
        while idx > 0 and self._tiers[idx].get("beacon_only"):
            idx -= 1
        if self._tiers[idx].get("beacon_only"):
            idx = self._safe_tier
        # Walk down the ladder until every gate is satisfied.
        while idx > 0 and self._tier_permission_error(idx, s) is not None:
            idx -= 1
        if self._tier_permission_error(idx, s) is not None:
            # Even the bottom rung is gated (beacon-only tier while not in
            # beacon mode) — fall back to the configured safe tier.
            idx = self._safe_tier
        return idx

    def _apply_tier(self, s: "TTCState", index: int) -> None:
        """Make `index` the active tier: bit rate, modulation and coding.

        The tier's modulation is applied only when the ACTIVE tier changes, so
        an explicit ``set_modulation`` (an RF-engineering exercise) is a
        within-tier override that survives subsequent ticks and is reset the
        next time the tier moves.
        """
        t = self.tier(index)
        changed = (index != getattr(self, "_applied_tier", None))
        s.tm_tier = index
        s.tm_data_rate = int(t["bps"])
        if changed:
            s.modulation_mode = self.MODULATION_INDEX.get(
                str(t.get("modulation", "BPSK")).upper(), 0)
            self._applied_tier = index
        self._coding_gain = float(t.get("coding_gain_db", self._coding_gain))

    @property
    def active_coding(self) -> str:
        """FEC scheme of the active tier ('RS' | 'CONV_RS' | 'NONE')."""
        return str(self.tier(self._state.tm_tier).get("coding", "RS")).upper()

    @property
    def active_symbol_rate_bd(self) -> float:
        """Symbol rate now on air: active bit rate / bits-per-symbol of the
        modulation actually in use (which may be a within-tier override)."""
        bps_per_sym = self.MODULATION_PARAMS.get(
            self._state.modulation_mode,
            {"bits_per_symbol": 1})["bits_per_symbol"]
        return float(self._state.tm_data_rate) / max(bps_per_sym, 1)

    @staticmethod
    def _ber_for_modulation(mod_mode: int, eb_n0_linear: float) -> float:
        """Compute BER for the given modulation and Eb/N0 (linear).

        BPSK/QPSK/OQPSK: BER = 0.5 * erfc(sqrt(Eb/N0))
        8PSK:             BER ~ (2/3) * erfc(sqrt((3*Eb/N0)/(2*(M-1))) * sin(pi/M))
                          simplified: ~ erfc(sqrt(0.4 * Eb/N0))
        """
        if eb_n0_linear <= 0:
            return 0.5
        if mod_mode in (0, 1, 3):  # BPSK, QPSK, OQPSK (same BER per bit)
            x = math.sqrt(eb_n0_linear)
            if x > 5.0:
                return 1e-12
            return 0.5 * math.erfc(x)
        elif mod_mode == 2:  # 8PSK
            x = math.sqrt(0.4 * eb_n0_linear)
            if x > 5.0:
                return 1e-12
            return (2.0 / 3.0) * math.erfc(x)
        return 0.5 * math.erfc(math.sqrt(eb_n0_linear))

    @property
    def name(self) -> str:
        return "ttc"

    def configure(self, config: dict[str, Any]) -> None:
        self._eirp = config.get("eirp_dbw", 10.0)
        self._gs_gt = config.get("gs_g_t_db", 20.0)
        self._sc_gain = config.get("sc_gain_dbi", 3.0)
        self._dl_freq_hz = config.get("dl_freq_mhz", 401.5) * 1e6

        # ── TM data-rate ladder (defect #85) ──
        tiers = config.get("tm_rate_tiers") or None
        if tiers:
            parsed: list[dict[str, Any]] = []
            for t in tiers:
                d = t if isinstance(t, dict) else (
                    t.model_dump() if hasattr(t, "model_dump") else dict(t))
                parsed.append(d)
            if parsed:
                self._tiers = parsed
        rates = [int(t["bps"]) for t in self._tiers]
        if rates != sorted(rates):
            raise ValueError(
                f"ttc.tm_rate_tiers must be ascending in bps, got {rates}")

        def _tier_cfg(key: str, fallback: int) -> int:
            name = config.get(key)
            if name is None:
                return fallback
            idx = self.tier_index(str(name))
            if idx < 0:
                raise ValueError(
                    f"ttc.{key}={name!r} is not one of the configured "
                    f"tm_rate_tiers {[t['name'] for t in self._tiers]}")
            return idx

        self._beacon_tier = _tier_cfg("beacon_tier", 0)
        self._safe_tier = _tier_cfg("safe_tier", min(1, len(self._tiers) - 1))
        # Routine operations default to the SAFE tier, never to the top of the
        # ladder: the top rung is a gated dump rate that has to be earned.
        self._default_tier = _tier_cfg("default_tier", self._safe_tier)

        # The telecommand uplink rate is INDEPENDENT of the TM ladder. It used
        # to default to tm_rate_hi_bps, which under the ladder would have made
        # the uplink 1.024 Mbps (16x ESA's typical maximum).
        self._tc_uplink_rate_bps = int(config.get("tc_uplink_rate_bps", 4000))
        self._ul_freq_mhz = config.get("ul_freq_mhz", 449.0)
        self._coding_gain = config.get("coding_gain_db", 3.0)
        # Link-budget losses (mirror rfsim.yaml link_budget)
        self._atmospheric_loss = float(config.get("atmospheric_loss_db", 0.5))
        self._polarization_loss = float(config.get("polarization_loss_db", 0.3))
        self._implementation_loss = float(
            config.get("implementation_loss_db", 2.0))
        self._gt_derate_10 = float(config.get("gt_derate_below_10deg_db", 1.5))
        self._gt_derate_30 = float(config.get("gt_derate_below_30deg_db", 0.5))
        self._required_eb_n0 = float(config.get("required_eb_n0_db", 9.6))
        self._pa_max_power_w = config.get("pa_max_power_w", 5.0)
        self._pa_low_power_w = config.get("pa_low_power_w", 1.0)
        self._pa_nominal_power_w = config.get("pa_nominal_power_w", 2.0)
        # The EIRP reference. Defaults to the NOMINAL operating point so a
        # config that predates this key behaves exactly as before at power-up.
        self._pa_ref_power_w = float(config.get(
            "pa_ref_power_w", self._pa_nominal_power_w) or 2.0)
        self._pa_shutdown_temp = config.get("pa_shutdown_temp_c", 70.0)
        self._pa_tau = config.get("pa_thermal_tau_s", 60.0)

        # ── UHF beacon transmitter (Franz's ruling R2) ──
        beacon = config.get("beacon") or {}
        if hasattr(beacon, "model_dump"):
            beacon = beacon.model_dump()
        self._beacon_cfg = dict(self.DEFAULT_BEACON)
        self._beacon_cfg.update({k: v for k, v in dict(beacon).items()
                                 if v is not None})
        # A disabled beacon transmitter is a spacecraft without the second
        # radio — legal (it is a config choice), and then the S-band chain
        # really is the only downlink and SID 11 dies with the PA.
        self._state.beacon_tx_on = bool(self._beacon_cfg.get("enabled", True))
        # Power up on the default operational tier, not the top of the ladder.
        self._state.data_rate_mode = self._default_tier
        self._apply_tier(self._state, self._default_tier)
        self._state.tx_fwd_power = self._pa_nominal_power_w

        self._param_ids = config.get("param_ids", {
            "ttc_mode": 0x0500, "link_status": 0x0501, "rssi": 0x0502,
            "link_margin": 0x0503, "ul_freq": 0x0504, "dl_freq": 0x0505,
            "tm_data_rate": 0x0506, "xpdr_temp": 0x0507,
            "ranging_status": 0x0508, "range_km": 0x0509,
            "contact_elevation": 0x050A, "contact_az": 0x050B,
            # New params
            "ber": 0x050C, "tx_fwd_power": 0x050D, "pa_temp": 0x050F,
            "carrier_lock": 0x0510, "bit_sync": 0x0511,
            "frame_sync": 0x0512, "cmd_rx_count": 0x0513,
            "pa_on": 0x0516, "eb_n0": 0x0519,
            # Hidden telemetry now exposed
            "antenna_deployed": 0x0520, "beacon_mode": 0x0521,
            "bytes_tx": 0x051E, "bytes_rx": 0x051F,
            "cmd_decode_timer": 0x0522,
            # Downlink gating (T-a) and the UHF beacon transmitter (R2).
            # 0x0523 was already ttc.active_gs, hence 0x0525 for tx_active.
            "tx_active": 0x0525, "beacon_tx_on": 0x0526,
            "beacon_active": 0x0527, "beacon_link_margin": 0x0528,
            "beacon_dl_freq": 0x0529, "beacon_tx_power": 0x052A,
            "tx_gate_status": 0x052B, "eirp_dbw": 0x052C,
        })

    # ─── S2 device-access power gates ────────────────────────────────

    def _apply_device_power_gates(self, s: TTCState) -> None:
        """Force TT&C equipment to a powered-down state for any device
        switched OFF via S2 device access (device_states).

        Mirrors the AOCS/payload/OBDH device gates: before this, every entry
        in ``device_states`` was a dead flag — the S2_TTC_* on/off commands
        were accepted and toggled the dict but nothing in the model ever read
        it, so they had no effect. Each device now collapses its corresponding
        RF chain when powered off and the normal tick logic restores it when
        powered back on.

        Called at the START of ``tick`` so the gates COMBINE with (rather than
        override) the contact/failure-driven lock and link-budget logic that
        follows. All devices default ON, so nominal behaviour — and the
        existing test suite — are unchanged.

        Effects (minimal, using existing state fields):
          * 0x0402 Power amplifier OFF → ``pa_on`` forced False (no TX /
            downlink) while the device is off; the existing thermal-shutdown
            logic is left to manage ``pa_on`` when the device is on.
          * 0x0400 Transponder A / 0x0401 Transponder B — gating done in
            ``tick`` via ``_device_rx_available`` so the OFF device mirrors the
            corresponding ``primary_failed`` / ``redundant_failed`` RX/TX
            availability effect (without setting the failure flag).
          * 0x0403 LNA OFF — receiver dead: gated in ``tick`` so no uplink lock
            can be acquired (carrier/bit/frame lock held False).
          * 0x0404 Antenna drive OFF — gated in ``handle_command`` /
            ``_device_antenna_drive_on`` so deployment cannot be driven (an
            already-deployed antenna is left untouched).
        """
        ds = s.device_states
        # 0x0402 Power amplifier: a powered-off PA cannot transmit. Force the
        # PA off while the device is off; when the device is on we don't touch
        # pa_on here so the thermal-shutdown / operator pa_on/pa_off logic owns
        # it (don't fight the existing logic — only force OFF when off).
        if not ds.get(0x0402, True):
            s.pa_on = False

    def _device_rx_available(self, s: TTCState) -> bool:
        """True if the transponder selected by ``s.mode`` is powered via S2
        device access (0x0400 primary / 0x0401 redundant). An OFF device
        mirrors the ``primary_failed`` / ``redundant_failed`` RX/TX
        availability effect without setting the failure flag."""
        if s.mode == 0:
            return s.device_states.get(0x0400, True)
        if s.mode == 1:
            return s.device_states.get(0x0401, True)
        return True

    def _device_lna_on(self, s: TTCState) -> bool:
        """True if the LNA (0x0403) is powered. With the LNA off the receiver
        is dead — no uplink lock can be acquired."""
        return s.device_states.get(0x0403, True)

    def _device_antenna_drive_on(self, s: TTCState) -> bool:
        """True if the antenna drive (0x0404) is powered. With it off the
        antenna cannot be moved/deployed (already-deployed state is kept)."""
        return s.device_states.get(0x0404, True)

    # ── Beacon emission and safe-mode tier (2026-08b) ───────────────────

    def _tick_beacon_emission(self, dt: float, s: TTCState,
                              shared_params: dict[int, float]) -> None:
        """Put HK SID 11 on the UHF beacon when the S-band chain is down.

        WHY THE MODEL EMITS THIS ITSELF, rather than letting the OBC's HK
        scheduler do it: the engine's `_enqueue_tm` funnels every packet
        through a single `downlink_active` boolean, so there is no per-SID
        seam there. The beacon is a separate radio with its own framing that
        lives in the bootloader — having it emit its own packet on its own
        cadence is the honest model of that box, and it is the only way to
        make SID 11 survive an S-band outage without letting everything else
        survive it too.

        No double-emission: when `tx_active` is true the OBC scheduler is
        already sending SID 11 (subject to HK enable), so the beacon holds
        off. It transmits only in the gap the S-band chain leaves.
        """
        b = self._beacon_cfg
        if not (s.beacon_active and not s.tx_active):
            # Keep the cadence phase-locked to the moment the beacon becomes
            # the only downlink, so the first beacon after an S-band failure
            # arrives one interval later rather than immediately.
            s._beacon_timer = 0.0
            return
        engine = getattr(self, "_engine", None)
        if engine is None:
            return
        sid = int(b.get("hk_sid", 11))
        interval = float(getattr(engine, "_hk_intervals", {}).get(sid, 30.0))
        s._beacon_timer += dt
        if s._beacon_timer < interval:
            return
        s._beacon_timer = 0.0
        # An operator who has disabled SID 11 has disabled the beacon's
        # content; the radio is still keyed but there is nothing to say.
        if not getattr(engine, "_hk_enabled", {}).get(sid, True):
            return
        try:
            from smo_simulator.engine import _put_tm
            pkt = engine.tm_builder.build_hk_packet(
                sid, shared_params,
                hk_structure=getattr(engine, "_hk_structures", {}).get(sid))
            if pkt:
                # Straight onto the outbound queue: engine._enqueue_tm would
                # test downlink_active, which is False by construction here —
                # that is the whole point of the beacon being a second radio.
                _put_tm(engine, pkt)
                logger.debug(
                    "UHF beacon: SID %d emitted on %.1f MHz (%.1f dB margin) "
                    "while the S-band chain is inactive",
                    sid, float(b["dl_freq_mhz"]), s.beacon_margin_db)
        except Exception as e:  # noqa: BLE001 — never let the beacon kill a tick
            logger.debug("UHF beacon emission skipped: %s", e)

    def _apply_safe_mode_tier(self, s: TTCState) -> None:
        """Command the configured safe tier on entry into SC_SAFE.

        Edge-triggered: only the 0 -> 1 transition acts, so an operator can
        still select a different tier while safe mode persists, and leaving
        safe mode does not yank the rate back.
        """
        engine = getattr(self, "_engine", None)
        if engine is None:
            return
        try:
            # 0 == SC_SAFE (engine.py's authoritative sc_mode convention).
            safe = int(getattr(engine, "sc_mode", 1)) == 0
        except (TypeError, ValueError):
            return
        if self._prev_sc_safe is None:
            # First tick: adopt the current mode without commanding anything,
            # so a run that STARTS in SAFE (every run does — sc_mode is SAFE
            # until the spacecraft is commissioned) does not fight the
            # bootloader's beacon tier.
            self._prev_sc_safe = safe
            return
        if safe and not self._prev_sc_safe:
            if not s.beacon_mode and s.data_rate_mode != self._safe_tier:
                logger.info(
                    "SAFE mode entry: TM rate tier commanded to the safe tier "
                    "%s (%d bps) from %s",
                    self.tier_name(self._safe_tier),
                    self.tier_bps(self._safe_tier),
                    self.tier_name(s.data_rate_mode))
                s.data_rate_mode = self._safe_tier
        self._prev_sc_safe = safe

    def tick(self, dt: float, orbit_state: Any,
             shared_params: dict[int, float]) -> None:
        s = self._state

        # ── S2 device-access power gates ──
        # Force any operator-switched-off device down BEFORE the contact /
        # failure-driven lock and link-budget logic runs, so the gates combine
        # with it. All devices default ON → nominal behaviour unchanged.
        self._apply_device_power_gates(s)

        in_contact = orbit_state.in_contact or bool(
            shared_params.get(0x05FF, 0)
        )

        # Transponder RX availability (receiver is on dedicated PDM —
        # independent of TX PA state).  A total transponder failure
        # kills both RX and TX. The selected transponder being switched OFF
        # via S2 device access (0x0400/0x0401) has the same RX/TX-killing
        # effect, driven by the device flag rather than the failure flag.
        rx_available = True
        if s.primary_failed and s.mode == 0:
            rx_available = False
        if s.redundant_failed and s.mode == 1:
            rx_available = False
        if not self._device_rx_available(s):
            rx_available = False
        if not rx_available:
            in_contact = False

        # LNA (0x0403) powered-off → receiver dead: no uplink lock possible.
        # Combines with the contact/failure lock logic below (treated like a
        # loss of the uplink RX chain).
        lna_on = self._device_lna_on(s)

        # ── EPS `ttc_tx` power line (2026-08b, finding T-a) ──
        # 0x0112 is eps.pl_ttc_tx, published every EPS tick straight from
        # `power_lines`. That is the LIVE truth about the line;
        # `eps._state.transponder_tx` is a parallel flag that no code anywhere
        # in the repo reads and that the engine's auto-TX hold does not even
        # update, so it must not be believed. Absent (standalone model, no EPS
        # in the run) => treat the line as present: a spacecraft without an
        # EPS model is not a spacecraft with its transmitter unpowered.
        line = shared_params.get(0x0112)
        s.ttc_tx_line_on = True if line is None else bool(line)

        # ── Leaving the bootloader un-inhibits the S-band PA ─────────────
        # `_enter_bootloader_mode` (engine.py:631) sets `pa_on = False`: in
        # the bootloader the S-band chain is deliberately dark and the UHF
        # beacon is the only downlink. Nothing performed the reverse, so the
        # application booted with its transmitter still inhibited — an
        # asymmetry that was invisible while nothing read `pa_on` (2026-08b,
        # finding 3) and would otherwise have meant every commissioning
        # sequence in the repo boots into silence.
        #
        # Edge-triggered on the 0 -> 1 transition of obdh.sw_image (0x0311),
        # so it is the boot that lifts the inhibit, not a periodic re-arm: an
        # operator who sends TTC_PA_DISABLE afterwards keeps the PA off, and a
        # thermal shutdown is never overridden.
        sw_image = shared_params.get(0x0311)
        if sw_image is not None:
            sw = int(sw_image)
            if (s._prev_sw_image == 0 and sw != 0
                    and not s.pa_on and not s.pa_overheat_shutdown):
                s.pa_on = True
                logger.info(
                    "Application boot: S-band PA inhibit lifted "
                    "(the bootloader had it off; the UHF beacon carried the "
                    "downlink until now)")
            s._prev_sw_image = sw

        # TX capability (PA must be on and not in thermal shutdown).
        # This affects downlink / link budget only — uplink lock
        # acquisition works off the RX chain which is always powered.
        can_transmit = (s.pa_on and not s.pa_overheat_shutdown
                        and rx_available and s.ttc_tx_line_on)
        if can_transmit:
            s.tx_fwd_power = self._pa_nominal_power_w
        else:
            s.tx_fwd_power = 0.0
        s.eirp_dbw = (self._effective_eirp_dbw() if can_transmit
                      else self._effective_eirp_dbw(0.0))

        # ── Uplink timeout tracking ──
        if in_contact and s.frame_sync:
            s._uplink_timeout_counter = 0.0
        else:
            s._uplink_timeout_counter += dt

        # ── Dedicated command channel timer ──
        if s.cmd_channel_active:
            s.cmd_decode_timer -= dt
            if s.cmd_decode_timer > 0:
                # Override: keep PA on and TX active regardless of OBC state —
                # but a PA whose device (0x0402) is switched OFF via S2 device
                # access still cannot transmit (hardware is unpowered), so the
                # override only applies when the PA device is energised.
                if s.device_states.get(0x0402, True):
                    s.pa_on = True
                    s.tx_fwd_power = self._pa_nominal_power_w
            else:
                # Timer expired
                s.cmd_decode_timer = 0.0
                s.cmd_channel_active = False

        # ── Beacon mode ──
        if s.beacon_mode:
            # The bootloader has one rate and one rate only.
            s.data_rate_mode = self._beacon_tier

        # Uplink loss blocks receive
        if s.uplink_lost:
            s.cmd_rx_count = s.cmd_rx_count  # No new commands

        s.link_active = in_contact
        pass_override = bool(shared_params.get(0x05FF, 0))

        if pass_override and not orbit_state.in_contact:
            # Pass override active but no real orbital contact — use
            # a fixed nominal geometry so the link budget is stable.
            s.range_km = 500.0
            s.elevation_deg = 45.0
            s.azimuth_deg = 180.0
        else:
            # Use real orbital geometry
            s.elevation_deg = orbit_state.gs_elevation_deg
            s.azimuth_deg = orbit_state.gs_azimuth_deg
            s.range_km = orbit_state.gs_range_km

        # ── THE S-BAND DOWNLINK GATE (2026-08b, finding T-a) ────────────
        # Everything the prime chain sends — real-time HK, playback, image
        # dumps, S1 verification, on-demand reads — is gated on this one
        # boolean, and it is the first thing in the mission that reads the
        # transmitter state on the TM path at all. Three conditions, each of
        # which an operator can cause and each of which they must be able to
        # see (0x052B carries them individually):
        #   * can_transmit — PA commanded on, not in thermal shutdown, the
        #     selected transponder alive and its S2 device powered, AND the
        #     ttc_tx line hot;
        #   * in_contact  — no station in view means nothing is received, and
        #     the sim's downlink gate has always modelled the ground end;
        # It deliberately does NOT require frame_sync. The downlink is
        # one-way: a spacecraft transmits whether or not the ground has
        # acquired its uplink, and gating TM on the RECEIVE lock chain is
        # precisely the mislabelling this fix removes.
        s.tx_active = bool(can_transmit and in_contact)

        # ── UHF BEACON TRANSMITTER (Franz's ruling R2) ──────────────────
        # The second radio. It shares NONE of the failure modes above: not
        # the S-band PA, not the ttc_tx line, not the deployable patch, not
        # the transponder, not the uplink lock chain. That independence is
        # the entire reason it exists, and it is why `beacon_active` is
        # computed here from its own state rather than derived from
        # `tx_active`. It still needs a station in view — a beacon nobody is
        # listening to is not "active" in any useful sense — and it needs its
        # link to close, which on 1 W at 1 kbps it does everywhere.
        b = self._beacon_cfg
        if (bool(b.get("enabled", True)) and s.beacon_tx_on
                and in_contact and s.range_km > 0):
            budget = self.beacon_link_budget(s.range_km, s.elevation_deg)
            s.beacon_margin_db = budget["margin_db"]
            s.beacon_eb_n0_db = budget["eb_n0_db"]
            s.beacon_active = budget["margin_db"] > 0.0
            s.beacon_tx_power_w = float(b["rf_power_w"])
        else:
            s.beacon_margin_db = 0.0
            s.beacon_eb_n0_db = 0.0
            s.beacon_active = False
            s.beacon_tx_power_w = (float(b["rf_power_w"])
                                   if (bool(b.get("enabled", True))
                                       and s.beacon_tx_on) else 0.0)

        # ── Beacon housekeeping emission ───────────────────────────────
        # The beacon carries HK SID 11 and nothing else. When the S-band
        # chain is up the OBC's own HK scheduler emits SID 11 along with
        # everything else, so the beacon stays quiet and there is no
        # duplication; when the S-band chain is DOWN the beacon is the only
        # thing on the air, and it keeps SID 11 coming. That is the exact
        # behaviour ruling R2 asks for: "gates ONLY the beacon housekeeping
        # (SID 11); everything else stays on the S-band chain and dies with
        # the PA."
        self._tick_beacon_emission(dt, s, shared_params)

        # ── Safe mode commands the safe tier (2026-08b, finding 4) ──────
        # ttc.yaml documents `safe_tier: LOW` as "safe mode, FDIR fallback,
        # antenna stowed", but `_safe_tier` was only ever read in
        # `_effective_tier`'s last-resort branch, so entering SC_SAFE left the
        # COMMANDED tier wherever the operator had put it — a spacecraft that
        # dropped into safe mode mid-dump stayed commanded to HIGH and jumped
        # straight back to 128 kbps the moment the elevation gate reopened.
        # Edge-triggered on entry only, so an operator may still command a
        # different tier while safe mode persists.
        self._apply_safe_mode_tier(s)

        # ── TM data-rate ladder: resolve the ACTIVE tier (defect #85) ──
        # Runs AFTER link_active/elevation are updated so the HIGH tier's
        # elevation gate sees this tick's geometry.
        # `data_rate_mode` holds the COMMANDED tier and is left untouched
        # (defect #74 — the commanded rate is authoritative). The gates
        # (beacon mode, antenna stowed, DOWNLINK mode, elevation) can demote
        # the ACTIVE tier; the commanded tier resumes when the gate clears —
        # so HIGH auto-reverts to NOMINAL at LOS or below 10 deg and comes
        # back on the next pass without being re-commanded.
        # tm_data_rate is the tier's information rate exactly: modulation
        # moves the symbol rate and the occupied bandwidth, not the bit rate.
        self._apply_tier(s, self._effective_tier(s))

        # ── Lock acquisition sequence ──
        # Defect #28: while the uplink is lost, hold the link unlocked so the
        # downlink (which gates on frame_sync below) actually goes quiet — this
        # is what produces the "no telemetry at AOS" symptom the scenario teaches.
        # Previously uplink_lost only suppressed the command-RX counter, so the
        # link re-locked on the next tick and telemetry kept flowing.
        # ``lna_on`` gates the receiver: with the LNA (0x0403) switched off the
        # RX chain is dead, so lock can never be acquired (analogous to
        # uplink loss). Default ON → unchanged.
        if in_contact and s.range_km > 0 and not s.uplink_lost and lna_on:
            if not self._was_in_contact:
                # AOS: reset lock sequence
                s._lock_timer = 0.0
                s.carrier_lock = False
                s.bit_sync = False
                s.frame_sync = False

            s._lock_timer += dt

            # Lock timing depends on the UPLINK command rate, not the
            # downlink TM rate — the carrier->bit->frame sequence is the
            # ground station acquiring the spacecraft's uplink, so it is
            # paced by the uplink rate. Decoupled from s.tm_data_rate.
            # Thresholds are expressed against the fixed 4 kbps TC uplink
            # (Sentinel-1's TC rate) rather than against the TM ladder, which
            # the uplink no longer inherits from.
            ul_rate = self._tc_uplink_rate_bps
            rate_factor = 1.0
            if ul_rate <= self.TC_SLOW_LOCK_RATE_BPS:
                rate_factor = 3.0  # low uplink rate: 3x slower lock
            elif ul_rate >= self.TC_FAST_LOCK_RATE_BPS:
                rate_factor = 0.7  # SW-upload uplink rate: slightly faster

            s.carrier_lock = s._lock_timer >= s._carrier_lock_delay * rate_factor
            s.bit_sync = (
                s.carrier_lock
                and s._lock_timer >= s._bit_sync_delay * rate_factor
            )
            s.frame_sync = (
                s.bit_sync
                and s._lock_timer >= s._frame_sync_delay * rate_factor
            )
        else:
            # LOS
            s.carrier_lock = False
            s.bit_sync = False
            s.frame_sync = False
            s._lock_timer = 0.0

        # ── Link budget (downlink: spacecraft → ground station) ──
        # Requires TX PA operational AND uplink locked (frame_sync).
        if in_contact and can_transmit and s.range_km > 0 and s.frame_sync:
            fspl = (
                20 * math.log10(s.range_km * 1000)
                + 20 * math.log10(self._dl_freq_hz)
                - 147.55
            )
            s.rssi_dbm = (
                # EIRP at the COMMANDED PA power (2026-08b, T-b): selecting
                # LOW really does drop the received level by 3 dB now.
                self._effective_eirp_dbw()
                + self._sc_gain
                - fspl
                + 30.0
                + self.rng.gauss(0, 0.5)
            )

            # ── Eb/N0 (defect #84: the G/T sign was inverted) ──
            #
            #   C/N0  = EIRP + G_sc - FSPL - L_atm - L_pol - L_impl
            #           + G/T + 228.6
            #   Eb/N0 = C/N0 - 10*log10(R_b) + coding gain
            #
            # The old code computed
            #   noise_floor = -228.6 + gs_gt + 10log10(R)
            #   snr         = rssi - 30 - noise_floor
            # which expands to EIRP - FSPL + 228.6 **- G/T** - 10log10(R):
            # the ground-station G/T was SUBTRACTED where it must be added, a
            # 2 x G/T = 40 dB error that made a better station give a WORSE
            # link (Inuvik 22.0 dB/K scored below Troll 18.5 dB/K).
            noise_bw = 10 * math.log10(s.tm_data_rate)
            gt_eff = self._gs_gt - self._gt_derate_db(s.elevation_deg)
            losses = (self._atmospheric_loss_at(s.elevation_deg)
                      + self._polarization_loss
                      + self._implementation_loss)
            c_n0 = (s.rssi_dbm - 30.0) - losses + gt_eff + 228.6
            snr = c_n0 - noise_bw
            # Ground segment penalties (from instructor-injected ground failures)
            gs_penalty = (s.gs_lna_degrade_db + s.gs_antenna_mispoint_db
                          + s.gs_feed_loss_db + s.gs_rfi_db
                          + s.gs_hpa_degrade_db + s.gs_ref_osc_drift_db
                          + s.gs_tracking_loss_db)
            s.eb_n0 = (
                snr
                + self._coding_gain
                - s.ber_inject_offset
                - s.receiver_nf_degrade
                - gs_penalty
                + self.rng.gauss(0, 0.2)
            )
            # Margin against the UNCODED requirement (9.6 dB for BPSK/QPSK at
            # BER 1e-5, plus the modulation's own penalty). The active tier's
            # coding gain has already been credited into eb_n0, so the
            # effective requirement is 9.6 - 6.0 = 3.6 dB with RS(255,223) and
            # 9.6 - 7.5 = 2.1 dB with conv(7,1/2)+RS. The old hard-coded
            # `eb_n0 - 12.0` double-counted the coding gain against an
            # arbitrary 12 dB threshold.
            mod_penalty = self.MODULATION_PARAMS.get(
                s.modulation_mode, {"eb_n0_penalty": 0.0})["eb_n0_penalty"]
            s.link_margin_db = (s.eb_n0 - self._required_eb_n0 - mod_penalty
                                + self.rng.gauss(0, 0.2))

            # ── Antenna deployment effect on link margin ──
            if not s.antenna_deployed:
                s.link_margin_db -= 6.0  # Stowed antenna penalty

            # BER from Eb/N0 — modulation-dependent
            eb_n0_linear = 10.0 ** (s.eb_n0 / 10.0)
            if eb_n0_linear > 0:
                ber_val = self._ber_for_modulation(s.modulation_mode, eb_n0_linear)
                s.ber = max(-12.0, math.log10(max(ber_val, 1e-12)))
            else:
                s.ber = -1.0  # Very high BER
        else:
            s.rssi_dbm = -120.0 + self.rng.gauss(0, 0.5)
            s.link_margin_db = 0.0
            s.eb_n0 = 0.0
            s.ber = -10.0

        s.ranging_active = in_contact and s.range_km > 0 and s.frame_sync

        # ── AGC, Doppler, Range Rate (Phase 4) ──
        if in_contact and s.range_km > 0:
            # AGC tracks received signal level
            s.agc_level_db = s.rssi_dbm + 60.0 + self.rng.gauss(0, 0.3)

            # Doppler shift from range rate
            # V_r = d(range)/dt approximated from elevation change
            # For LEO at 7.5 km/s orbital velocity, max Doppler ~±11 kHz at UHF (401 MHz)
            el_rad = math.radians(max(0, s.elevation_deg))
            # Range rate approximation: V_orbit * cos(elevation) * sign
            v_orbit = 7500.0  # m/s typical LEO
            # Negative range rate = approaching, positive = receding
            # At low elevation: large |range rate|, at zenith: ~0
            s.range_rate_m_s = v_orbit * math.cos(el_rad) * (
                -1.0 if s.elevation_deg < 45.0 else 1.0
            ) + self.rng.gauss(0, 5.0)
            # Doppler: f_d = f_0 * v_r / c
            c = 3e8
            s.doppler_hz = self._dl_freq_hz * s.range_rate_m_s / c
            s.doppler_hz += self.rng.gauss(0, 2.0)

            # Bytes transmitted this pass
            if s.frame_sync:
                s.total_bytes_tx += int(s.tm_data_rate / 8 * dt)
                # Uplink accrues at the TC rate (4 kbps), not at any TM tier.
                s.total_bytes_rx += int(self._tc_uplink_rate_bps / 8 * dt)
        else:
            s.agc_level_db = -120.0
            s.doppler_hz = 0.0
            s.range_rate_m_s = 0.0
            # Reset pass counters on LOS
            if not in_contact and self._was_in_contact:
                s.total_bytes_tx = 0
                s.total_bytes_rx = 0

        # ── PA thermal model ──
        tx_load = 1.0 if (in_contact and s.pa_on) else 0.2
        pa_heat = tx_load * 5.0 + s.pa_heat_inject  # Watts dissipated
        pa_ambient = 25.0
        s.pa_temp += (
            (pa_ambient + pa_heat * 2.0 - s.pa_temp) / self._pa_tau * dt
            + self.rng.gauss(0, 0.03)
        )

        # Auto-shutdown at threshold
        if s.pa_temp >= self._pa_shutdown_temp:
            s.pa_overheat_shutdown = True
            s.pa_on = False
        # Re-enable when cooled (with hysteresis)
        if s.pa_overheat_shutdown and s.pa_temp < (self._pa_shutdown_temp - 15.0):
            s.pa_overheat_shutdown = False

        # Transponder temperature (slower)
        xpdr_tx = 1.0 if in_contact else 0.2
        s.xpdr_temp += (
            (28.0 + 8.0 * xpdr_tx - s.xpdr_temp) / 300.0 * dt
            + self.rng.gauss(0, 0.02)
        )

        # ── UHF beacon thermal node (ruling R2) ──
        # Its own node, because it is its own box. The dissipation is
        # (DC in - RF out) while keying, and the duty is tiny: SID 11 is
        # 28 bytes at 1000 bps = 0.224 s in every 30 s beacon interval, i.e.
        # 0.75 %. So the beacon runs a couple of degrees above ambient and
        # never approaches a thermal limit — which is exactly the point of a
        # lifeline transmitter, and the contrast with the S-band PA's 70 C
        # auto-shutdown is worth a student's attention.
        bcfg = self._beacon_cfg
        beacon_duty = 0.0
        if s.beacon_active:
            interval = 30.0
            engine = getattr(self, "_engine", None)
            if engine is not None:
                interval = float(getattr(engine, "_hk_intervals", {}).get(
                    int(bcfg.get("hk_sid", 11)), 30.0)) or 30.0
            beacon_duty = min(1.0, (28.0 * 8.0 / max(float(bcfg["bps"]), 1.0))
                              / max(interval, 1e-3))
        beacon_diss = beacon_duty * max(
            float(bcfg["dc_power_w"]) - float(bcfg["rf_power_w"]), 0.0)
        s.beacon_temp += (
            (25.0 + beacon_diss * 3.0 - s.beacon_temp) / 300.0 * dt
            + self.rng.gauss(0, 0.01)
        )

        # ── Event generation (edge detection) ──
        self._generate_ttc_events()

        self._was_in_contact = in_contact

        # ── Write params ──
        p = self._param_ids
        shared_params[p.get("ttc_mode", 0x0500)] = s.mode
        # link_status: 0=NO_LINK, 1=ACQUIRING (carrier lock but no frame sync),
        # 2=LOCKED (full frame sync achieved)
        if s.frame_sync:
            link_status = 2  # LOCKED
        elif s.carrier_lock:
            link_status = 1  # ACQUIRING
        else:
            link_status = 0  # NO_LINK
        shared_params[p.get("link_status", 0x0501)] = link_status
        shared_params[p.get("rssi", 0x0502)] = s.rssi_dbm
        shared_params[p.get("link_margin", 0x0503)] = s.link_margin_db
        shared_params[p.get("ul_freq", 0x0504)] = self._ul_freq_mhz
        shared_params[p.get("dl_freq", 0x0505)] = self._dl_freq_hz / 1e6
        shared_params[p.get("tm_data_rate", 0x0506)] = s.tm_data_rate
        shared_params[p.get("xpdr_temp", 0x0507)] = s.xpdr_temp
        shared_params[p.get("ranging_status", 0x0508)] = (
            1 if s.ranging_active else 0
        )
        shared_params[p.get("range_km", 0x0509)] = s.range_km
        shared_params[p.get("contact_elevation", 0x050A)] = s.elevation_deg
        shared_params[p.get("contact_az", 0x050B)] = s.azimuth_deg
        # New params
        shared_params[p.get("ber", 0x050C)] = s.ber
        shared_params[p.get("tx_fwd_power", 0x050D)] = s.tx_fwd_power
        shared_params[p.get("pa_temp", 0x050F)] = s.pa_temp
        shared_params[p.get("carrier_lock", 0x0510)] = (
            1 if s.carrier_lock else 0
        )
        shared_params[p.get("bit_sync", 0x0511)] = 1 if s.bit_sync else 0
        shared_params[p.get("frame_sync", 0x0512)] = (
            1 if s.frame_sync else 0
        )
        shared_params[p.get("cmd_rx_count", 0x0513)] = s.cmd_rx_count
        shared_params[p.get("pa_on", 0x0516)] = 1 if s.pa_on else 0
        shared_params[p.get("eb_n0", 0x0519)] = s.eb_n0
        # ── Downlink gate (T-a) and the UHF beacon transmitter (R2) ──
        # 0x0525 is THE parameter the engine's downlink_active gates on. It
        # must be published unconditionally and every tick: an absent 0x0525
        # means "the TT&C model has not ticked", and the engine treats that as
        # NOT transmitting (fails closed), which is the safe direction and the
        # opposite of what the old link-status gate did.
        shared_params[p.get("tx_active", 0x0525)] = 1 if s.tx_active else 0
        shared_params[p.get("beacon_tx_on", 0x0526)] = (
            1 if s.beacon_tx_on else 0)
        shared_params[p.get("beacon_active", 0x0527)] = (
            1 if s.beacon_active else 0)
        shared_params[p.get("beacon_link_margin", 0x0528)] = s.beacon_margin_db
        shared_params[p.get("beacon_dl_freq", 0x0529)] = float(
            self._beacon_cfg["dl_freq_mhz"])
        shared_params[p.get("beacon_tx_power", 0x052A)] = s.beacon_tx_power_w
        # 0x052B — the WHY behind 0x0525, in one byte, so a console can render
        # "INHIBITED (ttc_tx LINE OFF)" rather than a bare red light. A SET
        # bit is the healthy/permissive state:
        #   bit0 PA commanded on          bit4 UHF beacon commanded on
        #   bit1 EPS ttc_tx line hot      bit5 UHF beacon radiating
        #   bit2 station in view          bit6 S-band antenna deployed
        #   bit3 S-band chain radiating   bit7 beacon MODE (rate ladder) OFF
        gate = 0
        if s.pa_on and not s.pa_overheat_shutdown:
            gate |= 0x01
        if s.ttc_tx_line_on:
            gate |= 0x02
        if in_contact:
            gate |= 0x04
        if s.tx_active:
            gate |= 0x08
        if s.beacon_tx_on:
            gate |= 0x10
        if s.beacon_active:
            gate |= 0x20
        if s.antenna_deployed:
            gate |= 0x40
        if not s.beacon_mode:
            gate |= 0x80
        shared_params[p.get("tx_gate_status", 0x052B)] = float(gate)
        shared_params[p.get("eirp_dbw", 0x052C)] = s.eirp_dbw
        # Phase 4: Flight hardware params
        shared_params[0x051A] = s.agc_level_db
        shared_params[0x051B] = s.doppler_hz
        shared_params[0x051C] = s.range_rate_m_s
        shared_params[0x051D] = float(s.cmd_auth_status)
        shared_params[0x051E] = float(s.total_bytes_tx)
        shared_params[0x051F] = float(s.total_bytes_rx)
        # Dedicated command channel / deployment params
        shared_params[0x0520] = 1.0 if s.antenna_deployed else 0.0
        shared_params[0x0521] = 1.0 if s.beacon_mode else 0.0
        shared_params[0x0522] = s.cmd_decode_timer
        # DEFECT FIX #4 (ttc.md): Antenna deployment sensor telemetry
        shared_params[0x0535] = 1.0 if s.antenna_deployment_ready else 0.0
        shared_params[0x0536] = float(s.antenna_deployment_sensor)
        # Modulation mode (0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK)
        shared_params[0x0537] = float(s.modulation_mode)
        # ── TM data-rate ladder (defect #85) ──
        # 0x0506 (tm_data_rate) already carries the active tier's bps; these
        # make the tier itself, the commanded tier and the resulting symbol
        # rate / occupied bandwidth observable from the ground.
        shared_params[0x0539] = float(s.tm_tier)          # active tier index
        shared_params[0x053A] = float(s.data_rate_mode)   # commanded tier index
        shared_params[0x053B] = self.active_symbol_rate_bd
        shared_params[0x053C] = 1.0 if s.downlink_mode else 0.0
        # Ground segment total Eb/N0 penalty (dB)
        shared_params[0x0538] = (s.gs_lna_degrade_db + s.gs_antenna_mispoint_db
                                 + s.gs_feed_loss_db + s.gs_rfi_db
                                 + s.gs_hpa_degrade_db + s.gs_ref_osc_drift_db
                                 + s.gs_tracking_loss_db)
        # Ground equipment health bitmask (0x0524). Catalogued since the first
        # release and written by nothing, so an S20 read returned 0.0 — "every
        # chain broken" — whatever the ground segment was doing (brief 08
        # finding 12). A SET bit means that chain is healthy:
        #   bit0 antenna   — pointing loss and tracking system
        #   bit1 LNA       — receive low-noise amplifier
        #   bit2 HPA       — uplink high-power amplifier
        #   bit3 frequency — reference oscillator and feed/waveguide
        # 0x0538 stays the aggregate dB penalty; this is the per-chain answer
        # to "which box is sick", which is what the operator actually needs.
        mask = 0
        if s.gs_antenna_mispoint_db <= 0.0 and s.gs_tracking_loss_db <= 0.0:
            mask |= 0x1
        if s.gs_lna_degrade_db <= 0.0:
            mask |= 0x2
        if s.gs_hpa_degrade_db <= 0.0:
            mask |= 0x4
        if s.gs_ref_osc_drift_db <= 0.0 and s.gs_feed_loss_db <= 0.0:
            mask |= 0x8
        shared_params[0x0524] = float(mask)

    def _generate_ttc_events(self) -> None:
        """Generate TTC events based on state transitions and thresholds."""
        s = self._state
        events = []

        # ── Lock acquisition events (edge detection) ──
        if s.carrier_lock and not s._prev_carrier_lock:
            events.append({
                'event_id': 0x0500,
                'severity': 'INFO',
                'description': "Carrier lock acquired"
            })
        elif not s.carrier_lock and s._prev_carrier_lock:
            events.append({
                'event_id': 0x0501,
                'severity': 'MEDIUM',
                'description': "Carrier lock lost"
            })

        if s.bit_sync and not s._prev_bit_sync:
            events.append({
                'event_id': 0x0502,
                'severity': 'INFO',
                'description': "Bit sync acquired"
            })
        elif not s.bit_sync and s._prev_bit_sync:
            events.append({
                'event_id': 0x0503,
                'severity': 'MEDIUM',
                'description': "Bit sync lost"
            })

        if s.frame_sync and not s._prev_frame_sync:
            events.append({
                'event_id': 0x0504,
                'severity': 'INFO',
                'description': "Frame sync acquired"
            })
        elif not s.frame_sync and s._prev_frame_sync:
            events.append({
                'event_id': 0x0505,
                'severity': 'MEDIUM',
                'description': "Frame sync lost"
            })

        # Update lock edge-detection state. Without this the three _prev_ flags
        # stayed False forever, so every "acquired" event re-fired on EVERY tick
        # while locked (and the matching "lost" events could never fire). This is
        # the only place these flags are written.
        s._prev_carrier_lock = s.carrier_lock
        s._prev_bit_sync = s.bit_sync
        s._prev_frame_sync = s.frame_sync

        # ── Link margin thresholds ──
        link_margin_warning = (s.link_active and s.eb_n0 < 6.0)
        if link_margin_warning and not s._prev_link_margin_warning:
            events.append({
                'event_id': 0x0506,
                'severity': 'MEDIUM',
                'description': f"Link margin warning: Eb/N0 = {s.eb_n0:.1f} dB"
            })
        s._prev_link_margin_warning = link_margin_warning

        link_margin_critical = (s.link_active and s.eb_n0 < 3.0)
        if link_margin_critical and not s._prev_link_margin_critical:
            events.append({
                'event_id': 0x0507,
                'severity': 'HIGH',
                'description': f"Link margin critical: Eb/N0 = {s.eb_n0:.1f} dB"
            })
        s._prev_link_margin_critical = link_margin_critical

        # ── PA temperature events ──
        pa_overtemp_warning = (s.pa_temp > 55.0 and not s.pa_overheat_shutdown)
        if pa_overtemp_warning and not s._prev_pa_temp_warning:
            events.append({
                'event_id': 0x0508,
                'severity': 'MEDIUM',
                'description': f"PA overtemp warning: {s.pa_temp:.1f}C"
            })
        s._prev_pa_temp_warning = pa_overtemp_warning

        # Edge-guarded: fire ONCE on entry into shutdown, not every tick while held.
        if s.pa_overheat_shutdown and not s._prev_pa_overheat_shutdown:
            events.append({
                'event_id': 0x0509,
                'severity': 'HIGH',
                'description': f"PA overheat shutdown at {s.pa_temp:.1f}C"
            })
        s._prev_pa_overheat_shutdown = s.pa_overheat_shutdown

        if s._prev_pa_temp_warning and s.pa_temp < 50.0 and not s.pa_overheat_shutdown:
            events.append({
                'event_id': 0x050A,
                'severity': 'INFO',
                'description': "PA temperature recovered"
            })

        # ── Transponder mode switch ──
        if s.mode != s._prev_mode:
            mode_str = "primary" if s.mode == 0 else "redundant"
            events.append({
                'event_id': 0x050B,
                'severity': 'MEDIUM',
                'description': f"Transponder switched to {mode_str}"
            })
            s._prev_mode = s.mode

        # ── BER threshold exceedance ──
        ber_exceeded = (s.ber < -5.0)  # BER > 1e-5
        if ber_exceeded and not s._prev_ber_exceeded:
            events.append({
                'event_id': 0x050C,
                'severity': 'MEDIUM',
                'description': f"BER threshold exceeded: {10**s.ber:.2e}"
            })
        s._prev_ber_exceeded = ber_exceeded

        # ── Antenna deployed ──
        if s.antenna_deployed and not getattr(self, '_antenna_deployed_event_sent', False):
            events.append({
                'event_id': 0x050D,
                'severity': 'INFO',
                'description': "Antenna deployment confirmed"
            })
            self._antenna_deployed_event_sent = True

        # ── Antenna deployment sensor status (DEFECT FIX #4) ──
        if not s.antenna_deployment_ready and not getattr(self, '_antenna_deploy_fault_sent', False):
            events.append({
                'event_id': 0x050D,
                'severity': 'HIGH',
                'description': "Antenna deployment fault: pyro continuity or sensor failure"
            })
            self._antenna_deploy_fault_sent = True
        elif s.antenna_deployment_ready and getattr(self, '_antenna_deploy_fault_sent', False):
            events.append({
                'event_id': 0x0512,
                'severity': 'INFO',
                'description': "Antenna deployment readiness recovered"
            })
            self._antenna_deploy_fault_sent = False

        # ── Ranging state changes ──
        if s.ranging_active and not s._prev_ranging_active:
            events.append({
                'event_id': 0x050E,
                'severity': 'INFO',
                'description': "Ranging acquired"
            })
        elif not s.ranging_active and s._prev_ranging_active:
            events.append({
                'event_id': 0x050F,
                'severity': 'INFO',
                'description': "Ranging lost"
            })
        s._prev_ranging_active = s.ranging_active

        # ── AGC saturation ──
        agc_saturated = (s.agc_level_db > 0.0) if s.link_active else False
        if agc_saturated and not getattr(self, '_prev_agc_saturated', False):
            events.append({
                'event_id': 0x0510,
                'severity': 'LOW',
                'description': f"AGC saturation detected: {s.agc_level_db:.1f} dB"
            })
        self._prev_agc_saturated = agc_saturated

        # ── Uplink timeout ──
        uplink_timeout = (s._uplink_timeout_counter > s.uplink_timeout_threshold)
        if uplink_timeout and not getattr(self, '_prev_uplink_timeout', False):
            events.append({
                'event_id': 0x0511,
                'severity': 'HIGH',
                'description': f"Uplink timeout: no valid TC for {s.uplink_timeout_threshold}s"
            })
        self._prev_uplink_timeout = uplink_timeout

        # ── Emit events to shared store and engine event queue ──
        for evt in events:
            logger.info(
                "TTC event 0x%04X (%s): %s",
                evt['event_id'], evt['severity'], evt['description']
            )
            # Also queue to engine if available (defect #23)
            if hasattr(self, '_engine') and self._engine:
                try:
                    self._engine._model_event_queue.put_nowait({
                        'event_id': evt['event_id'],
                        'severity': evt['severity'],
                        'subsystem': 'ttc',
                        'description': evt['description']
                    })
                except Exception:
                    pass  # event queue full or format error

    def get_telemetry(self) -> dict[int, float]:
        return {
            0x0501: float(self._state.link_active),
            0x0502: self._state.rssi_dbm,
        }

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")

        if command == "switch_redundant":
            if self._state.redundant_failed:
                return {"success": False, "message": "Redundant XPDR failed"}
            self._state.mode = 1
            return {"success": True}

        elif command == "switch_primary":
            if self._state.primary_failed:
                return {"success": False, "message": "Primary XPDR failed"}
            self._state.mode = 0
            return {"success": True}

        elif command == "set_tm_rate":
            # The commanded tier is authoritative: it sets data_rate_mode,
            # which is what tick() resolves the active tier from. Setting only
            # tm_data_rate (the old behaviour) was overwritten on the very next
            # tick, so the high rate was unreachable from the ground
            # (roadmap N-8 / defect #74).
            #
            # Accepts a tier `command_code` (the uint8 the catalogue
            # advertises: 0=LOW, 1=HIGH, 0xFF=BEACON), a tier NAME, or a raw
            # bps value equal to one of the configured tier rates. The legacy
            # 0/1 binary encoding decodes verbatim as "the low rate" / "the
            # high rate" (defect #85).
            raw = cmd.get("rate", self._default_tier)
            idx = self._decode_tier_arg(raw)
            if idx is None:
                return {"success": False,
                        "message": f"Invalid TM rate/tier {raw!r}. Valid: "
                                   + ", ".join(
                                       f"{i}={t['name']}({t['bps']} bps)"
                                       for i, t in enumerate(self._tiers)),
                        "error_code": 0x0003}
            # Permission rules are enforced by REJECTION, never by silent
            # clamping: an operator who asks for a tier they may not have
            # gets an S1.8 execution failure saying why.
            why = self._tier_permission_error(idx)
            if why is not None:
                return {"success": False, "message": why, "error_code": 0x0007}
            self._state.data_rate_mode = idx
            self._apply_tier(self._state, idx)
            t = self.tier(idx)
            logger.info("TTC TM rate tier -> %s (%d bps, %s, %s)",
                        t["name"], t["bps"], t.get("modulation", "BPSK"),
                        t.get("coding", "RS"))
            return {"success": True, "data_rate_mode": idx,
                    "tier": t["name"], "tier_index": idx,
                    "modulation": t.get("modulation", "BPSK"),
                    "coding": t.get("coding", "RS"),
                    "symbol_rate_bd": self.tier_symbol_rate(idx),
                    "tm_data_rate": self._state.tm_data_rate}

        elif command == "set_data_rate_mode":
            # Explicit alias so procedures / OBCPs can name the tier directly.
            return self.handle_command(
                {"command": "set_tm_rate",
                 "rate": cmd.get("mode", self._default_tier)})

        elif command == "set_downlink_mode":
            # DOWNLINK / payload-dump mode. The HIGH tier is gated on this so
            # bulk downlink has to be entered deliberately, not drifted into.
            on = bool(cmd.get("on", True))
            self._state.downlink_mode = on
            if not on:
                # Leaving DOWNLINK mode re-resolves the active tier on the
                # next tick; the commanded tier is deliberately preserved.
                pass
            return {"success": True, "downlink_mode": on}

        elif command == "pa_on":
            if self._state.pa_overheat_shutdown:
                return {
                    "success": False,
                    "message": "PA shutdown due to overheat, wait for cooldown",
                }
            self._state.pa_on = True
            return {"success": True}

        elif command == "pa_off":
            self._state.pa_on = False
            self._state.tx_fwd_power = 0.0
            return {"success": True}

        elif command == "set_tx_power":
            # Phase E WP-2: enumerated SSPA operating points. An SSPA is
            # commanded to a gain state, not a continuous watt setpoint;
            # the levels map to the config's pa power ladder. The corrected
            # watt table (the documented "2 = 8 W" was never executable —
            # pa_max_power_w is 5.0): 0=LOW 1.0 W, 1=NOMINAL 2.0 W,
            # 2=HIGH 5.0 W. Unknown codes REJECTED (0x0003), never clamped.
            if "level" in cmd:
                # 2026-08b: the watts come from ttc.yaml (pa_low/nominal/max
                # _power_w) instead of being code constants, because the EIRP
                # is now DERIVED from them — retuning the ladder to the
                # researched 0.25/1/2 W is then a config change and the link
                # budget follows automatically.
                level_table = {
                    0: self._pa_low_power_w,     # LOW
                    1: self._pa_ref_power_w,     # NOMINAL == the EIRP ref
                    2: self._pa_max_power_w,     # HIGH (ceiling)
                }
                lvl = int(cmd.get("level", 1))
                if lvl not in level_table:
                    return {"success": False, "error_code": 0x0003,
                            "message": f"Unknown TX power level {lvl} "
                                       f"(0=LOW {level_table[0]:g} W, "
                                       f"1=NOMINAL {level_table[1]:g} W, "
                                       f"2=HIGH {level_table[2]:g} W)"}
                self._pa_nominal_power_w = level_table[lvl]
                self._state.tx_power_level = lvl
                # The link moves with the PA now: report the EIRP the
                # operator just bought so S1.7 says something real.
                eirp = self._effective_eirp_dbw()
                logger.info(
                    "TTC TX power level %d = %.2f W RF -> EIRP %.2f dBW "
                    "(%+.2f dB about the %.2f W reference)",
                    lvl, level_table[lvl], eirp, eirp - self._eirp,
                    self._pa_ref_power_w)
                return {"success": True, "power_w": level_table[lvl],
                        "eirp_dbw": eirp,
                        "delta_db": eirp - self._eirp}
            # Legacy internal form (watts) — kept for scenario scripts.
            power = float(cmd.get("power_w", self._pa_nominal_power_w))
            if 0.0 < power <= self._pa_max_power_w:
                self._pa_nominal_power_w = power
                return {"success": True}
            return {"success": False, "error_code": 0x0003,
                    "message": "Power out of range"}

        elif command == "cmd_channel_start":
            self._state.cmd_decode_timer = 900.0
            self._state.cmd_channel_active = True
            self._state.pa_on = True
            return {"success": True}

        elif command == "deploy_antennas":
            # DEFECT FIX #4 (ttc.md): Update antenna deployment sensor on command
            # S2 device gate: the antenna drive (0x0404) must be powered to
            # move/deploy the antenna. With it switched off the burn-wire/drive
            # cannot be commanded (an already-deployed antenna is untouched).
            if not self._device_antenna_drive_on(self._state):
                return {
                    "success": False,
                    "message": "Antenna drive (device 0x0404) is powered off"
                }
            if not self._state.antenna_deployment_ready:
                return {
                    "success": False,
                    "message": "Antenna deployment not ready (pyro continuity or sensor fault)"
                }
            self._state.antenna_deployed = True
            self._state.antenna_deployment_sensor = 2  # Mark as deployed
            logger.info("Antennas deployed (burn-wire fired)")
            return {"success": True}

        elif command == "set_beacon_mode":
            # BEACON *MODE* — a rung of the S-band TM rate ladder, NOT the UHF
            # beacon transmitter (that is beacon_tx_on / TTC_BEACON_TX_ON).
            on = bool(cmd.get("on", True))
            was = self._state.beacon_mode
            self._state.beacon_mode = on
            if on:
                self._state.data_rate_mode = self._beacon_tier
            elif was:
                # LEAVING beacon mode must reset the COMMANDED tier, not just
                # the flag (2026-08b, finding 3). Before this, exit left
                # data_rate_mode pinned at BEACON: `_exit_bootloader_mode`
                # only resets it `if not beacon_mode`, which is False at the
                # moment of exit, and nothing else ever wrote it. The console
                # then reported commanded=BEACON / active=LOW indefinitely and
                # the demotion display could never clear. The safe tier is the
                # right landing point — LOW is the routine operations rate and
                # works with the antenna still stowed.
                self._state.data_rate_mode = self._safe_tier
                logger.info(
                    "Beacon mode OFF: commanded TM tier reset to %s (%d bps)",
                    self.tier_name(self._safe_tier),
                    self.tier_bps(self._safe_tier))
            return {"success": True, "beacon_mode": on,
                    "data_rate_mode": self._state.data_rate_mode}

        elif command in ("beacon_tx_on", "beacon_tx_off", "set_beacon_tx"):
            # The UHF BEACON TRANSMITTER (Franz's ruling R2) — the second
            # radio, not the rate-ladder rung above. Switching it off silences
            # HK SID 11 when the S-band chain is down, which is the only thing
            # it carries; it has no effect at all while the S-band chain is up.
            if command == "beacon_tx_on":
                on = True
            elif command == "beacon_tx_off":
                on = False
            else:
                on = bool(cmd.get("on", True))
            if on and not bool(self._beacon_cfg.get("enabled", True)):
                return {"success": False, "error_code": 0x0007,
                        "message": "No UHF beacon transmitter is fitted "
                                   "(ttc.yaml beacon.enabled = false)"}
            self._state.beacon_tx_on = on
            if not on:
                self._state.beacon_active = False
                self._state.beacon_tx_power_w = 0.0
                logger.warning(
                    "UHF beacon transmitter OFF — HK SID 11 will not survive "
                    "an S-band outage while this is commanded off")
            return {"success": True, "beacon_tx_on": on,
                    "dl_freq_mhz": float(self._beacon_cfg["dl_freq_mhz"]),
                    "rf_power_w": float(self._beacon_cfg["rf_power_w"])}

        elif command == "set_ul_freq":
            # S-band near-Earth allocation (CCSDS 401.0-B), matching
            # configs/eosat1/subsystems/ttc.yaml, planning/ground_stations.yaml
            # and rfsim.yaml. The old UHF 440–460 MHz band agreed with nothing
            # else in the repo (roadmap N-4).
            freq_mhz = float(cmd.get("freq_mhz", self._ul_freq_mhz))
            if self.UL_BAND_MHZ[0] <= freq_mhz <= self.UL_BAND_MHZ[1]:
                self._ul_freq_mhz = freq_mhz
                return {"success": True}
            return {"success": False,
                    "message": f"Uplink frequency out of range "
                               f"({self.UL_BAND_MHZ[0]:.0f}–{self.UL_BAND_MHZ[1]:.0f} MHz, S-band)"}

        elif command == "set_dl_freq":
            freq_mhz = float(cmd.get("freq_mhz", self._dl_freq_hz / 1e6))
            if self.DL_BAND_MHZ[0] <= freq_mhz <= self.DL_BAND_MHZ[1]:
                self._dl_freq_hz = freq_mhz * 1e6
                return {"success": True}
            return {"success": False,
                    "message": f"Downlink frequency out of range "
                               f"({self.DL_BAND_MHZ[0]:.0f}–{self.DL_BAND_MHZ[1]:.0f} MHz, S-band)"}

        elif command == "set_modulation":
            mod_mode = int(cmd.get("mode", 0))
            if mod_mode not in self.MODULATION_PARAMS:
                return {"success": False,
                        "message": f"Invalid modulation mode {mod_mode}. "
                        f"Valid: 0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK"}
            old_mode = self._state.modulation_mode
            self._state.modulation_mode = mod_mode
            mp = self.MODULATION_PARAMS[mod_mode]
            # The tier's bps is the INFORMATION rate. Modulation changes the
            # SYMBOL rate and therefore the occupied bandwidth — never the bit
            # rate. The old `tm_data_rate = base_rate * bits_per_symbol` meant
            # selecting QPSK silently doubled the reported bit rate, so a
            # "128 kbps NOMINAL" tier would have reported 256 kbps.
            bit_rate = self._state.tm_data_rate
            symbol_rate = float(bit_rate) / max(mp["bits_per_symbol"], 1)
            logger.info("TTC modulation: %s → %s (%d bps unchanged, "
                        "symbol rate %.0f Bd)",
                        self.MODULATION_PARAMS[old_mode]["name"],
                        mp["name"], bit_rate, symbol_rate)
            # NOTE: tick() re-applies the ACTIVE TIER's modulation, so this is
            # a within-tier override that survives until the tier changes.
            return {"success": True, "modulation": mp["name"],
                    "data_rate_bps": bit_rate,
                    "symbol_rate_bd": symbol_rate}

        elif command == "set_rx_gain":
            agc_target = float(cmd.get("agc_db", -60.0))
            if -100.0 <= agc_target <= 0.0:
                self._state.agc_level_db = agc_target
                return {"success": True}
            return {"success": False, "message": "AGC target out of range"}

        elif command == "ranging_start":
            if self._state.link_active and self._state.frame_sync:
                self._state.ranging_active = True
                return {"success": True}
            return {"success": False, "message": "Link must be active with frame sync"}

        elif command == "ranging_stop":
            self._state.ranging_active = False
            return {"success": True}

        elif command == "set_coherent_mode":
            coherent = bool(cmd.get("on", True))
            setattr(self._state, "coherent_mode", coherent)
            return {"success": True}

        return {"success": False, "message": f"Unknown: {command}"}

    def record_cmd_received(self):
        """Increment command receive counter."""
        if not self._state.uplink_lost:
            self._state.cmd_rx_count += 1

    def inject_failure(self, failure: str, magnitude: float = 1.0,
                       **kw) -> None:
        s = self._state

        if failure == "primary_failure":
            s.primary_failed = bool(magnitude)
            if magnitude and s.mode == 0:
                s.link_active = False

        elif failure == "redundant_failure":
            s.redundant_failed = bool(magnitude)

        elif failure == "high_ber":
            s.ber_inject_offset = float(kw.get("offset", magnitude * 10.0))

        elif failure == "pa_overheat":
            s.pa_heat_inject = float(kw.get("heat_w", 20.0))

        elif failure == "uplink_loss":
            s.uplink_lost = True
            s.carrier_lock = False

        elif failure == "receiver_degrade":
            s.receiver_nf_degrade = float(kw.get("nf_db", magnitude * 5.0))

        elif failure == "antenna_deploy_failed":
            # Burn-wire failed: antenna stuck stowed/jammed.
            s.antenna_deployed = False
            s.antenna_deployment_ready = False
            s.antenna_deployment_sensor = 3  # partial/jammed

        # ── Ground segment failures ──
        elif failure == "gs_lna_degradation":
            s.gs_lna_degrade_db = magnitude * 6.0
        elif failure == "gs_antenna_mispoint":
            s.gs_antenna_mispoint_db = magnitude * 10.0
        elif failure == "gs_feed_loss":
            s.gs_feed_loss_db = magnitude * 4.0
        elif failure == "gs_rfi_interference":
            s.gs_rfi_db = magnitude * 8.0
        elif failure == "gs_hpa_degradation":
            s.gs_hpa_degrade_db = magnitude * 5.0
        elif failure == "gs_ref_osc_drift":
            s.gs_ref_osc_drift_db = magnitude * 3.0
        elif failure == "gs_tracking_loss":
            s.gs_tracking_loss_db = magnitude * 15.0

    def clear_failure(self, failure: str, **kw) -> None:
        s = self._state

        if failure == "primary_failure":
            s.primary_failed = False

        elif failure == "redundant_failure":
            s.redundant_failed = False

        elif failure == "high_ber":
            s.ber_inject_offset = 0.0

        elif failure == "pa_overheat":
            s.pa_heat_inject = 0.0

        elif failure == "uplink_loss":
            s.uplink_lost = False

        elif failure == "receiver_degrade":
            s.receiver_nf_degrade = 0.0

        elif failure == "antenna_deploy_failed":
            s.antenna_deployment_ready = True
            s.antenna_deployment_sensor = 1  # back to stowed (operator must redeploy)

        # ── Ground segment failures ──
        elif failure == "gs_lna_degradation":
            s.gs_lna_degrade_db = 0.0
        elif failure == "gs_antenna_mispoint":
            s.gs_antenna_mispoint_db = 0.0
        elif failure == "gs_feed_loss":
            s.gs_feed_loss_db = 0.0
        elif failure == "gs_rfi_interference":
            s.gs_rfi_db = 0.0
        elif failure == "gs_hpa_degradation":
            s.gs_hpa_degrade_db = 0.0
        elif failure == "gs_ref_osc_drift":
            s.gs_ref_osc_drift_db = 0.0
        elif failure == "gs_tracking_loss":
            s.gs_tracking_loss_db = 0.0

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        from smo_common.state import rng_state_to_json
        d = dataclasses.asdict(self._state)
        d["_rng_state"] = rng_state_to_json(self.rng.getstate())
        return d

    def set_state(self, state: dict[str, Any]) -> None:
        # Container fields are deep-copied by restore_dataclass_state: the
        # plain setattr this used to do aliased the snapshot's own dicts and
        # lists into live model state (G2-f).
        from smo_common.state import restore_dataclass_state, restore_rng
        restore_rng(self.rng, state.get("_rng_state"))
        restore_dataclass_state(self._state, state, skip=("_rng_state",))

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
