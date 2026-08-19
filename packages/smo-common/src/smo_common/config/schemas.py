"""SMO Common — Configuration Schemas (Pydantic v2).

Every schema in this file inherits :class:`StrictModel`, i.e.
``model_config = ConfigDict(extra="forbid")`` (brief 08 WP-8 / G2-g).

Pydantic's default is ``extra="ignore"``: a key the model does not declare is
silently dropped and the simulation runs on the default instead. That is the
origin of several of this project's defects — ``mission.yaml: ground_segment``
sat there for months read by nothing, and a mistyped key (``therhold:`` for
``threshold:``) produced a working-but-wrong spacecraft with no diagnostic
anywhere. Forbidding extras turns both into a load-time error naming the file,
the key and the schema.

The corollary is that a config block MUST be declared here before it can be
used, even when the consumer reads the raw YAML rather than the model (the
AOCS dynamics blocks, for instance). Declaring them is the point: this file is
the single inventory of what the mission configuration is allowed to contain.
"""
from __future__ import annotations
from typing import Any, ClassVar, Optional
from pydantic import (AliasChoices, BaseModel, ConfigDict, Field, field_validator,
                      model_validator)


class StrictModel(BaseModel):
    """Base for every config schema: unknown keys are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid")


class MissionConfig(StrictModel):
    """Top-level mission identity."""

    name: str = "EOSAT-1"
    description: str = ""
    spacecraft_apid: int = Field(default=0x01, description="Application-software APID for TM packets (OBC app running)")
    bootloader_apid: int = Field(default=0x02, description="Bootloader APID — distinct from application APID; used for beacon/bootloader TM while OBC is in bootloader")
    start_in_bootloader: bool = Field(default=True, description="If True, engine powers up in bootloader/beacon mode (phase 3) with only SID 11 active and bootloader APID")
    pus_version: int = 2
    # LIVE config: the single definition of the CUC epoch on the wire.
    # ``loader.load_mission_config`` pushes it into
    # ``smo_common.protocol.ecss_packet`` via ``init_time_epoch``; the
    # ``TIME_EPOCH_DEFAULT`` literal there is only the fallback for code that
    # never loads a mission config. A gate test asserts the two agree.
    time_epoch: str = Field(
        default="2000-01-01T12:00:00",
        description="CUC epoch on the wire, ISO-8601 UTC (J2000 = 2000-01-01T12:00:00).",
    )
    dump_dir: str | None = Field(default=None, description="Directory for archived S15 delayed-TM dumps; absolute or relative; may point to a shared/server location. Overridden by SMO_DUMP_DIR env. Defaults to workspace/dumps.")
    # ── Time source (MCS ground UTC + Planner "now") ──
    # Selects whether the MCS/Planner follow SIMULATION time (closed-loop
    # synced to the simulator's sim_time/speed) or real wall-clock UTC.
    # This does NOT affect spacecraft OBC time, which always comes from
    # telemetry (TM param 0x0309), nor the simulator's own internal clock.
    # Precedence (each service): CLI flag > env var (SMO_TIME_SOURCE) >
    # this config field > built-in default ("sim").
    time_source: str = Field(default="sim", description='Ground/Planner clock source: "sim" (follow simulator sim_time/speed) or "real" (wall-clock UTC).')
    sim_state_url: str | None = Field(default=None, description="URL of the simulator state endpoint to poll for sim_time/speed when time_source=sim. Overridden by SMO_SIM_STATE_URL env. Defaults to http://<connect_host>:8080/api/state.")
    # ── UI generation switch (design_2026-08 plan §1.2) ──
    # Which console every server's `/` serves: the classic single-file pages
    # or the modern design-system pages. Precedence mirrors time_source:
    # CLI --ui > SMO_UI env > this field > built-in default. A per-request
    # `?ui=` query (and the /classic and /modern routes) overrides everything
    # for that one response. None = defer to env / built-in default.
    ui: str | None = Field(
        default=None,
        description='Console generation served at "/": "classic" or "modern". '
                    'Overridden by SMO_UI env and --ui CLI; per-request ?ui= '
                    'wins over all.')
    # Declared so mission.yaml's `network:` block (ports + sim_tick_hz) is
    # actually parsed instead of being dropped as an undeclared key. NetworkConfig
    # is defined below; the forward reference is resolved at class build time.
    network: "NetworkConfig" = Field(default_factory=lambda: NetworkConfig())
    # Descriptive blocks. Declared (rather than silently dropped) so that
    # extra="forbid" can do its job: the mission narrative belongs in this
    # file, but a typo in it must fail at load like any other key.
    orbit: "MissionOrbitSummary" = Field(
        default_factory=lambda: MissionOrbitSummary(),
        description="Human-readable orbit summary; the authoritative elements "
                    "are the TLE in orbit.yaml")
    payload: "MissionPayloadSummary" = Field(
        default_factory=lambda: MissionPayloadSummary(),
        description="Human-readable payload summary; the model's band table "
                    "is in subsystems/payload.yaml")


class MissionOrbitSummary(StrictModel):
    """Narrative orbit summary in mission.yaml (not the propagator's input)."""

    type: str = ""
    altitude_km: float | None = None
    inclination_deg: float | None = None


class MissionPayloadSummary(StrictModel):
    """Narrative payload summary in mission.yaml."""

    type: str = ""
    bands: list[float] = Field(default_factory=list)
    target: str = ""

class NetworkConfig(StrictModel):
    """Simulation server network ports and main-loop tick rate."""

    tc_port: int = 8001
    tm_port: int = 8002
    instr_port: int = 8003
    http_port: int = 8080
    sim_tick_hz: float = 1.0
    tm_max_clients: int = 8

class GroundStationConfig(StrictModel):
    """Ground station definition."""
    name: str = "Svalbard"
    lat_deg: float = 78.229
    lon_deg: float = 15.407
    alt_km: float = 0.458
    min_elevation_deg: float = 5.0

class OrbitConfig(StrictModel):
    """Orbit configuration (TLE-based)."""

    tle_line1: str
    tle_line2: str
    altitude_km: float = 500.0
    inclination_deg: float = 97.4
    earth_radius_km: float = 6371.0
    # Reference epoch the run starts at, e.g. "2026-03-10T00:00:00Z". Was
    # present in orbit.yaml but dropped by the loader, so the engine started
    # the propagator at wall-clock time and no two runs saw the same passes or
    # eclipses (roadmap N-5 / DI-16). tools/pass_gap_analysis.py had to re-read
    # the raw YAML for exactly this reason.
    t0_epoch: str | None = None
    ground_stations: list[GroundStationConfig] = Field(default_factory=list)

class SolarArrayConfig(StrictModel):
    name: str
    area_m2: float = 0.314
    efficiency: float = 0.295

class BatteryConfig(StrictModel):
    type: str = "li_ion"
    capacity_wh: float = 120.0
    voltage_nom: float = 26.4
    soc_100_v: float = 29.2
    soc_0_v: float = 21.5
    internal_r_ohm: float = 0.05
    # Coulombic (charge-acceptance) efficiency — Li-ion 0.92-0.96 (brief 01 F19).
    charge_efficiency: float = 1.0
    discharge_efficiency: float = 1.0
    # Constant-voltage taper: charge power tapers linearly above this SoC.
    # None/absent = no taper (legacy behaviour).
    cv_taper_start_soc_pct: float | None = None

class EPSConfig(StrictModel):
    model: str = "eps_basic"
    technology: str = ""
    # Legacy two-array model — retained for back-compat with older mission
    # configs; eosat1 no longer declares it (brief 01 F11/F13, WP-13). Only
    # `efficiency` is still consumed (cell-efficiency fallback).
    arrays: list[SolarArrayConfig] = Field(default_factory=list)
    battery: BatteryConfig = Field(default_factory=BatteryConfig)
    # Bare cell efficiency for the 6-panel model (previously only reachable
    # through the legacy `arrays:` list).
    cell_efficiency: float = 0.295
    # Array performance loss stack (brief 01 WP-1). Empty => legacy BOL
    # behaviour (cell_eff x MPPT only, K = 389.5 W/m2 of projected area).
    array_losses: dict[str, float] = Field(default_factory=dict)
    payload_power_w: float = 45.0
    payload_standby_power_w: float = 8.0
    fpa_cooler_power_w: float = 15.0
    transponder_power_w: float = 20.0
    # HIGH-tier (QPSK dump) transmit power — billed when the active TM tier's
    # bps exceeds the LOW tier (brief 01 F20 / WP-12).
    transponder_hi_power_w: float = 20.0
    transponder_rx_power_w: float = 5.0
    obc_power_w: float = 15.0
    # Body-panel + deployable-wing areas. Declared here because pydantic drops
    # undeclared keys: without these fields eps.yaml's solar_panels/power_lines
    # sections never reached the EPS model at all (defect #51).
    # sada_axis is a string and sada_present/tracking are bools, hence Any.
    solar_panels: dict[str, Any] = Field(default_factory=dict)
    power_lines: dict[str, bool] = Field(default_factory=dict)
    param_ids: dict[str, int] = Field(default_factory=dict)

class ReactionWheelConfig(StrictModel):
    max_speed_rpm: int = 5500
    nominal_speed_rpm: int = 1200
    desaturation_speed_rpm: int = 200
    # Phase C wheel-assembly physics (read by AOCSBasic from the raw YAML).
    # Undeclared here, extra="forbid" would reject the shipped aocs.yaml.
    count: int = 4
    geometry: str = "pyramid"
    rotor_inertia_kgm2: float = 5.2e-5
    max_torque_nm: float = 0.002
    motor_tau_s: float = 0.05
    coulomb_friction_nm: float = 2.0e-6
    viscous_friction_nm_per_rads: float = 1.0e-8

class AOCSConfig(StrictModel):
    model: str = "aocs_basic"
    modes: list[str] = Field(default_factory=lambda: ["nominal", "detumble", "safe", "wheel_desat", "slew"])
    reaction_wheels: ReactionWheelConfig = Field(default_factory=ReactionWheelConfig)
    num_wheels: int = 4
    attitude_error_deadband_deg: float = 0.01
    param_ids: dict[str, int] = Field(default_factory=dict)
    # Phase C rigid-body blocks. `AOCSBasic` reads these straight off the raw
    # YAML dict, so they were never declared and pydantic dropped them from
    # the validated model; with extra="forbid" they must be declared or the
    # file will not load at all. Typed as free-form mappings because the model
    # owns their internal shape (inertia matrix, sensor sub-blocks).
    mass_properties: dict[str, Any] = Field(
        default_factory=dict,
        description="mass_kg + 3x3 inertia_kgm2 (kg m^2)")
    magnetorquers: dict[str, Any] = Field(
        default_factory=dict, description="count + max_dipole_am2")
    disturbances: dict[str, Any] = Field(
        default_factory=dict,
        description="residual dipole, drag/SRP areas, Cd/Cr, rho")
    integration: dict[str, Any] = Field(
        default_factory=dict,
        description="RK4 step h_s, max_substeps, secular_above_speed")
    sensors: dict[str, Any] = Field(
        default_factory=dict, description="star_tracker + gyro noise models")

class HeaterCircuitConfig(StrictModel):
    name: str
    power_w: float
    on_temp_c: float
    off_temp_c: float

class ThermalZoneConfig(StrictModel):
    name: str
    capacitance_j_per_c: float
    time_constant_s: float
    initial_temp_c: float = 15.0

class FPAEnvelopeConfig(StrictModel):
    """The single source for the FPA thermal envelope (DECISIONS §3 / G3-d).

    TCS owns the FPA node, so the envelope lives here, and every limit band,
    S12 monitor, planner constraint and manual table is *derived* from it by
    `smo_common.config.fpa_envelope.FPAEnvelope`. Five files used to carry
    five different answers for the same detector;
    `tests/test_content/test_fpa_envelope_single_source.py` recomputes them
    all from these numbers and fails on any drift.
    """
    #: Commandable regulation envelope. Must match PAYLOAD_COOLER_SETPOINT
    #: (S8 func 36) `range` in commands/tc_catalog.yaml.
    setpoint_min_c: float = -20.0
    setpoint_max_c: float = 0.0
    setpoint_default_c: float = -15.0
    #: Measured 3-sigma of the regulated node about its setpoint.
    control_3sigma_k: float = 0.5
    #: Measured warmest uncooled equilibrium. A legitimate state, so it has
    #: to sit inside the limit bands.
    uncooled_max_c: float = 12.5
    #: 0x0601 cold finger, relative to the 0x0408 bracket node.
    detector_offset_c: float = -0.5
    #: 0x0617 detector die, relative to the cold finger.
    die_offset_c: float = 0.5
    #: Warmest the detector may be and still produce a usable image.
    imaging_max_c: float = -3.0
    ready_band_below_k: float = 1.0
    ready_band_above_k: float = 5.0
    warning_margin_k: float = 1.5
    alarm_margin_k: float = 5.0


class TCSConfig(StrictModel):
    model: str = "tcs_basic"
    zones: list[ThermalZoneConfig] = Field(default_factory=list)
    heaters: list[HeaterCircuitConfig] = Field(default_factory=list)
    fpa_cooler_target_c: float = -5.0
    #: DECISIONS §3 / G3-d — the authoritative FPA envelope.
    fpa_envelope: FPAEnvelopeConfig = Field(default_factory=FPAEnvelopeConfig)
    # Maximum cooler heat lift below the ambient sink (K) — bounds the FPA
    # setpoint control so the cooler is not an unbounded heat pump (brief 05
    # WP-1 / AF-6).
    fpa_cooler_max_lift_k: float = 40.0
    # Cooler efficiency model (2026-08b, DISPLAY_REQUESTS_PAYLOAD.md §8.2).
    # `tcs_basic._fpa_achievable_lift_k` reads all three; until they were
    # declared here the StrictModel rejected them in tcs.yaml and the model's
    # own literals were the only live values — three knobs that looked
    # configurable and were not. The Carnot fraction in particular is the knob
    # an instructor wants for a "the cooler is degrading" scenario.
    fpa_cooler_power_w: float = 15.0          # matches subsystems/payload.yaml
    fpa_cooler_carnot_fraction: float = 0.23  # miniature Stirling class
    fpa_imaging_load_w: float = 2.0           # detector/ROIC dissipation
    # Declared so tcs.yaml's thermal_coupling / event_thresholds sections
    # actually reach the TCS model (pydantic drops undeclared keys — that is
    # why these sections read as "never used", defect #51).
    thermal_coupling: dict[str, Any] = Field(default_factory=dict)
    event_thresholds: dict[str, Any] = Field(default_factory=dict)
    param_ids: dict[str, int] = Field(default_factory=dict)

class OBDHConfig(StrictModel):
    model: str = "obdh_basic"
    modes: list[str] = Field(default_factory=lambda: ["nominal", "safe", "emergency"])
    watchdog_period_ticks: int = 30
    cpu_baseline_pct: float = 35.0
    param_ids: dict[str, int] = Field(default_factory=dict)

    # ── Onboard clock (docs/plan_2026-08c/TIME_ARCHITECTURE.md Part B) ──
    # StrictModel forbids extra keys, so these have to be declared here before
    # subsystems/obdh.yaml is allowed to carry them.
    #: Mean oscillator drift of the fleet. 12 ppm ≈ 1 s/day — a plausible
    #: TCXO: big enough that a shift-long run needs a sync, small enough not
    #: to be absurd.
    clock_drift_ppm_nominal: float = 12.0
    #: Per-unit spread. ``clock_drift_ppm = nominal + N(0, sigma)``, drawn once
    #: per run from the seeded per-model RNG so determinism holds.
    clock_drift_sigma_ppm: float = 4.0
    #: Whether a GPS sync latches into disciplined mode (time_ref = GPS, no
    #: drift while the fix is good). False ⇒ a sync is a one-shot set and the
    #: clock free-runs from there.
    clock_gps_discipline: bool = True
    #: Minimum |jump| in seconds that raises an onboard-clock S5 event.
    clock_jump_event_threshold_s: float = 1.0
    #: ppm forced by the ``clock_drift`` failure injection at magnitude 1.0.
    clock_drift_failure_ppm: float = 500.0


class TMRateTier(StrictModel):
    """One rung of the TM data-rate ladder (defect #85).

    The ladder replaces the old binary 1 kbps / 64 kbps switch. Every number
    is configuration — `configs/eosat1/subsystems/ttc.yaml` is the source of
    truth and this class only supplies defaults and validation.
    """
    name: str
    bps: int
    #: uint8 value of TTC_SET_DATA_RATE (S8 func 65)'s `rate` field that
    #: selects this tier. LOW=0 ("the low rate") / HIGH=1 ("the high rate")
    #: preserve the legacy binary encoding verbatim; SAFE=0xFF is not
    #: commandable in nominal software.
    command_code: int = 0
    modulation: str = "BPSK"          # BPSK | QPSK | 8PSK | OQPSK
    coding: str = "RS"                # RS | CONV_RS | NONE
    coding_gain_db: float = 6.0
    # ON-BOARD permission gates. A tier whose gate is not satisfied is
    # REJECTED when commanded (S1.8, error 0x0007) — never silently clamped.
    # These are things the spacecraft CAN know about itself.
    #: selectable only in bootloader/beacon mode. Renamed from `beacon_only`
    #: (2026-08c: "beacon" always means the UHF whip transmitter now); the
    #: old key is accepted as a validation alias.
    bootloader_only: bool = Field(
        default=False,
        validation_alias=AliasChoices("bootloader_only", "beacon_only"))
    requires_antenna: bool = True     # needs the antenna deployed
    requires_downlink_mode: bool = False   # needs DOWNLINK/dump mode
    #: GROUND POLICY, not an on-board gate (2026-08c item 4): the minimum
    #: pass elevation at which the ground segment plans (smo-planner) or
    #: commands (MCS warning) this tier. A real spacecraft does not know its
    #: elevation as seen from a station, so flight software ignores this.
    min_elevation_deg: float = 0.0

    #: Bits per symbol for each supported modulation, used to derive the
    #: symbol rate (and therefore the occupied bandwidth) from ``bps``.
    _BITS_PER_SYMBOL: ClassVar[dict[str, int]] = {
        "BPSK": 1, "QPSK": 2, "8PSK": 3, "OQPSK": 2,
    }
    #: Modulation index as used by ttc_basic.MODULATION_PARAMS / rfsim.
    _MOD_INDEX: ClassVar[dict[str, int]] = {
        "BPSK": 0, "QPSK": 1, "8PSK": 2, "OQPSK": 3,
    }

    @property
    def bits_per_symbol(self) -> int:
        return self._BITS_PER_SYMBOL.get(self.modulation.upper(), 1)

    @property
    def modulation_index(self) -> int:
        return self._MOD_INDEX.get(self.modulation.upper(), 0)

    @field_validator("bps")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("tm_rate_tiers[].bps must be > 0")
        return v

    @field_validator("modulation")
    @classmethod
    def _known_modulation(cls, v: str) -> str:
        if v.upper() not in cls._BITS_PER_SYMBOL:
            raise ValueError(
                f"tm_rate_tiers[].modulation {v!r} unknown; "
                f"valid: {sorted(cls._BITS_PER_SYMBOL)}")
        return v.upper()

    @field_validator("coding")
    @classmethod
    def _known_coding(cls, v: str) -> str:
        if v.upper() not in {"RS", "CONV_RS", "NONE"}:
            raise ValueError(
                f"tm_rate_tiers[].coding {v!r} unknown; valid: RS, CONV_RS, NONE")
        return v.upper()


#: Shipped ladder — 1 / 16 / 128 kbps. Steps of +12.04 dB then +9.03 dB, i.e.
#: exactly the -10*log10(R) term in the link budget. LOW is the ROUTINE
#: operations rate; HIGH is for delayed telemetry and image dumps only.
_DEFAULT_TM_RATE_TIERS: list[dict[str, Any]] = [
    {"name": "SAFE", "bps": 1000, "command_code": 0xFF,
     "modulation": "BPSK", "coding": "CONV_RS",
     "coding_gain_db": 7.5, "bootloader_only": True,
     "requires_antenna": False},
    {"name": "LOW", "bps": 16000, "command_code": 0x00,
     "modulation": "BPSK", "coding": "CONV_RS",
     "coding_gain_db": 7.5, "requires_antenna": False},
    {"name": "HIGH", "bps": 128000, "command_code": 0x01,
     "modulation": "QPSK", "coding": "RS",
     "coding_gain_db": 6.0, "requires_antenna": True,
     "requires_downlink_mode": True, "min_elevation_deg": 10.0},
]


class BeaconConfig(StrictModel):
    """The UHF bootloader beacon TRANSMITTER — a second radio (ruling R2).

    Distinct from `TTCConfig.beacon_tier`, which names a rung of the S-band
    TM rate ladder. Conflating the two is what left the mission with no
    beacon transmitter at all: BEACON was rung 0 of the S-band ladder, so
    switching the S-band PA off silenced the lifeline as well as the prime
    chain. Every default below is the shipped EO-Sat-1 value and is sourced
    to flight hardware in `configs/eosat1/subsystems/ttc.yaml`.
    """

    enabled: bool = True
    dl_freq_mhz: float = 437.5              # 435-438 MHz amateur-satellite
    band_mhz: tuple[float, float] = (435.0, 438.0)
    rf_power_w: float = 1.0                 # 30 dBm — EnduroSat UHF Type II
    sc_gain_dbi: float = 0.0                # deployable tape-spring dipole
    gs_g_t_db: float = -8.0                 # ~16 dBic Yagi over T_sys ~300 K
    bps: int = 1000
    coding_gain_db: float = 7.5             # conv(7,1/2) + RS(255,223)
    required_eb_n0_db: float = 9.6
    atmospheric_loss_db: float = 0.2        # UHF, well below the water line
    polarization_loss_db: float = 3.0       # linear dipole vs circular ground
    implementation_loss_db: float = 2.0
    dc_power_w: float = 1.8                 # while keying
    standby_dc_power_w: float = 0.2
    eps_line: str = "ttc_rx"                # non-switchable receiver line
    hk_sid: int = 11                        # the ONE structure it carries


class TTCConfig(StrictModel):
    model: str = "ttc_basic"
    ul_freq_mhz: float = 2025.5
    dl_freq_mhz: float = 2200.5

    # ── TM data-rate ladder (defect #85) ──
    tm_rate_tiers: list[TMRateTier] = Field(
        default_factory=lambda: [TMRateTier(**t) for t in _DEFAULT_TM_RATE_TIERS])
    default_tier: str = "LOW"       # routine operations
    beacon_tier: str = "SAFE"       # bootloader / beacon mode — the SAFE (1 kbps) rung
    safe_tier: str = "LOW"          # safe mode, FDIR fallback, antenna stowed

    # Deprecated aliases kept so out-of-lane readers (smo-planner
    # budget_tracker, MCS) keep resolving while they migrate to the ladder.
    # When absent from the YAML they are derived from it: lo = safe_tier,
    # hi = the top tier.
    tm_rate_hi_bps: Optional[int] = None
    tm_rate_lo_bps: Optional[int] = None

    # Telecommand uplink rate. Explicit — it used to default to
    # tm_rate_hi_bps, which would have made the uplink 1.024 Mbps.
    tc_uplink_rate_bps: int = 4000

    eirp_dbw: float = 10.0
    # RF watts at which `eirp_dbw` is quoted. The TT&C model derives the live
    # EIRP as eirp_dbw + 10*log10(P_commanded / pa_ref_power_w), so
    # TTC_SET_TX_POWER moves the link budget instead of only moving telemetry
    # 0x050D (2026-08b finding T-b). Defaults to the NOMINAL operating point.
    pa_ref_power_w: float = 2.0
    gs_g_t_db: float = 20.0
    sc_gain_dbi: float = 3.0
    coding_gain_db: float = 6.0

    # ── S-band SSPA operating points, RF watts (TTC_SET_TX_POWER levels) ──
    # Configuration rather than code constants, because the EIRP above is
    # derived from them: a hardware number that drives a link budget belongs
    # in the hardware sheet, not in a model default.
    pa_low_power_w: float = 1.0        # level 0 = LOW
    pa_nominal_power_w: float = 2.0    # level 1 = NOMINAL
    pa_max_power_w: float = 5.0        # level 2 = HIGH
    pa_shutdown_temp_c: float = 70.0
    pa_thermal_tau_s: float = 60.0

    # ── Link-budget losses (mirror rfsim.yaml link_budget) ──
    atmospheric_loss_db: float = 0.5
    polarization_loss_db: float = 0.3
    implementation_loss_db: float = 2.0
    gt_derate_below_10deg_db: float = 1.5
    gt_derate_below_30deg_db: float = 0.5
    required_eb_n0_db: float = 9.6

    # ── UHF bootloader beacon transmitter (Franz's ruling R2, 2026-08b) ──
    # A second, physically separate radio: its own band, PA, antenna, budget
    # and power draw. Carries HK SID 11 only and survives the S-band PA being
    # switched off. See configs/eosat1/subsystems/ttc.yaml for the flight
    # hardware each number comes from.
    beacon: "BeaconConfig" = Field(default_factory=lambda: BeaconConfig())

    param_ids: dict[str, int] = Field(default_factory=dict)

    @field_validator("tm_rate_tiers")
    @classmethod
    def _ladder_ascending(cls, v: list[TMRateTier]) -> list[TMRateTier]:
        if not v:
            raise ValueError("ttc.tm_rate_tiers must not be empty")
        names = [t.name for t in v]
        if len(set(names)) != len(names):
            raise ValueError(f"ttc.tm_rate_tiers names must be unique: {names}")
        rates = [t.bps for t in v]
        if rates != sorted(rates) or len(set(rates)) != len(rates):
            raise ValueError(
                f"ttc.tm_rate_tiers must be strictly ascending in bps: {rates}")
        return v

    @model_validator(mode="after")
    def _resolve_tier_names_and_aliases(self) -> "TTCConfig":
        names = {t.name for t in self.tm_rate_tiers}
        for key in ("default_tier", "beacon_tier", "safe_tier"):
            val = getattr(self, key)
            if val not in names:
                raise ValueError(
                    f"ttc.{key}={val!r} is not one of the tm_rate_tiers "
                    f"{sorted(names)}")
        if self.tm_rate_hi_bps is None:
            self.tm_rate_hi_bps = self.tm_rate_tiers[-1].bps
        if self.tm_rate_lo_bps is None:
            self.tm_rate_lo_bps = self.tier(self.safe_tier).bps
        return self

    def tier(self, name_or_index) -> TMRateTier:
        """Look a tier up by name (case-insensitive) or by index."""
        if isinstance(name_or_index, int):
            return self.tm_rate_tiers[name_or_index]
        key = str(name_or_index).strip().upper()
        for t in self.tm_rate_tiers:
            if t.name.upper() == key:
                return t
        raise KeyError(f"no TM rate tier named {name_or_index!r}")

    def tier_index(self, name: str) -> int:
        key = str(name).strip().upper()
        for i, t in enumerate(self.tm_rate_tiers):
            if t.name.upper() == key:
                return i
        raise KeyError(f"no TM rate tier named {name!r}")

class PayloadConfig(StrictModel):
    model: str = "payload_basic"
    fpa_cooler_target_c: float = -5.0
    fpa_ambient_c: float = 5.0
    fpa_tau_cooling_s: float = 100.0
    fpa_tau_warming_s: float = 120.0
    # Detector (cold-finger) offset from the TCS FPA bracket node while the
    # cooler runs (brief 05 WP-1).
    detector_offset_c: float = -0.5
    fpa_cooler_power_w: float = 15.0
    image_size_mb: float = 4.0   # subsystems/payload.yaml is canonical (defect #82)
    total_storage_mb: float = 20000.0
    # Phase E WP-7: declared so the YAML value reaches the model (pydantic
    # drops undeclared keys). 8 segments -> PAYLOAD_MARK_BAD_SEGMENT 0-7.
    num_memory_segments: int = 8
    # ── Optics: the single source of swath (0x0619) and GSD (0x0628) ──
    # OWNER RULING 2026-08: the swath is 240 km. 8192 px x 5.5 um on an
    # 84.5 mm focal length gives FOV = 2*atan(45.056/2/84.5) = 29.856 deg and
    # swath = 2 * 450 km * tan(FOV/2) = 239.9 km, GSD = 450 km * 5.5/84.5e3
    # = 29.3 m. Previously the model hard-coded 20 urad x 5000 px -> 45 km.
    pixels_across_track: int = 8192
    pixel_pitch_um: float = 5.5
    focal_length_mm: float = 84.5
    bits_per_pixel: int = 12
    # Readout rates derived from the optics: line_rate = v_ground/GSD =
    # 7140/29.29 = 244 Hz; data_rate = 8192 x 4 bands x 12 bit x 244 Hz =
    # 96 Mbps (was 500 Hz / 80 Mbps, consistent with neither geometry).
    line_rate_hz: float = 244.0
    data_rate_mbps: float = 96.0
    param_ids: dict[str, int] = Field(default_factory=dict)

class PropConfig(StrictModel):
    """subsystems/prop.yaml — 1U green-monoprop blowdown module (Phase D).

    Sized for debris-avoidance manoeuvres and limited orbit maintenance
    (drag make-up); NOT an end-of-life deorbit system — disposal is passive
    decay from 450 km, inside the FCC 5-year rule.
    """
    model: str = "prop_basic"
    propellant: str = "LMP-103S/ADN"
    sc_dry_mass_kg: float = 12.0        # spacecraft dry mass incl. prop dry
    propellant_mass_kg: float = 0.400
    propellant_density_kg_m3: float = 1240.0
    tank_volume_cm3: float = 560.0
    tank_pressure_bol_bar: float = 22.0
    tank_pressure_eol_bar: float = 9.33
    thrust_bol_n: float = 0.33
    isp_bol_s: float = 220.0
    isp_eol_s: float = 196.0
    thrust_axis: list[float] = Field(default_factory=lambda: [1.0, 0.0, 0.0])
    thrust_offset_mm: float = 5.0       # cp offset -> disturbance torque
    valve_power_w: float = 2.0          # latch/FCV drivers while armed/firing
    catbed_heater_w: float = 12.0       # billed via TCS thruster circuit
    arm_code: int = 0xA5C3
    arm_window_s: float = 300.0         # PROP_SET_BURN -> PROP_ARM window
    interlocks: dict[str, Any] = Field(default_factory=dict)
    abort: dict[str, Any] = Field(default_factory=dict)
    param_ids: dict[str, int] = Field(default_factory=dict)


class FDIRRuleConfig(StrictModel):
    parameter: str
    description: str = ""
    condition: str
    threshold: float | None = None
    level: int = 1
    action: str
    # Phase C: seconds the condition must persist before the rule fires.
    # 0 = immediate (legacy). Needed once transients are real physics: an
    # AOCS acquisition legitimately crosses att_error thresholds for minutes.
    persist_s: float = 0.0
    # Lowest spacecraft phase in which this rule is ARMED. ``None`` (the
    # default) = armed in every phase, which is the legacy behaviour and the
    # right answer for hardware-protection rules (battery SoC/voltage,
    # thermal, reboot count, CAN bus): those protect the spacecraft whether or
    # not it is commissioned, and blanket-gating the whole rule set would
    # disable genuine LEOP protections.
    #
    # A rule needs a ``min_phase`` only when its trigger condition IS the
    # expected configuration of the spacecraft earlier in the mission.
    # ``aocs.wheels_enabled < 2`` is the archetype: the bootloader
    # legitimately leaves all four wheels off, so the rule latched SAFE on the
    # first tick of every run and nothing could clear it — the same deadlock
    # the advanced fault-propagation model already avoids by arming only at
    # phase >= 6 (see ``Engine._tick_fdir_advanced``).
    #
    # A gated-out rule is DISARMED, not merely suppressed: any latch it holds
    # is dropped and its persistence accumulator is reset, so falling back
    # below ``min_phase`` cannot leave a stale contingency behind.
    min_phase: int | None = None

class FDIRConfig(StrictModel):
    enabled: bool = True
    rules: list[FDIRRuleConfig] = Field(default_factory=list)

class HKParameterDef(StrictModel):
    param_id: int
    pack_format: str
    scale: float = 1.0

class HKStructureDef(StrictModel):
    sid: int
    name: str
    interval_s: float = 4.0
    parameters: list[HKParameterDef] = Field(default_factory=list)

class ParameterDef(StrictModel):
    id: int
    name: str
    subsystem: str
    type: str = "float"
    units: str = ""
    description: str = ""
    #: Not in any HK SID by design — retrieved by an S20 (parameter
    #: management) read rather than routine housekeeping. Read by the MCS
    #: parameter browser and tools/procedure_audit.py; it was undeclared
    #: here, so `load_parameters()` dropped it and the two sides disagreed
    #: about which parameters were pollable.
    on_demand: bool = False
    #: The on-board value is a fixed placeholder, not a modelled quantity.
    #: Surfaced by the MCS so a student is not told a stub is measurement.
    stub: bool = False

class LimitDef(StrictModel):
    param_id: int
    yellow_low: float | None = None
    yellow_high: float | None = None
    red_low: float | None = None
    red_high: float | None = None
    check_interval_s: float = 4.0

class TCFieldCalibration(StrictModel):
    """Raw <-> engineering conversion for one TC argument (Phase E WP-0).

    ``type``:
      * ``none``     — identity; the raw integer IS the engineering value.
      * ``linear``   — ``eng = raw * scale + offset``.
      * ``polynomial`` — ``eng = sum(coefficients[i] * raw**i)``. Reverse
        conversion is only supported for degree <= 1.
      * ``enum``     — the raw value is one of ``TCFieldDef.enum[].value``.
      * ``bitfield`` — the raw value is a mask over ``TCFieldDef.bits[]``.
    """
    type: str = "none"                # none | linear | polynomial | enum | bitfield
    scale: float = 1.0
    offset: float = 0.0
    coefficients: list[float] = Field(default_factory=list)


class TCFieldRange(StrictModel):
    """Inclusive valid range (engineering units for `range`, raw for `raw_range`)."""
    min: float
    max: float


class TCFieldEnumValue(StrictModel):
    value: int
    label: str
    description: str = ""


class TCFieldBitDef(StrictModel):
    bit: int
    name: str
    description: str = ""
    reserved: bool = False


class TCFieldDef(StrictModel):
    name: str
    type: str = "uint8"
    description: str = ""
    # ── Phase E argument schema (brief 04 WP-0) — all optional/back-compat ──
    #: Octet offset within the TC data field (S8.1: the func_id byte is
    #: octet 0, so the first argument sits at offset 1). When absent it is
    #: derived from field order; when present it must MATCH the derivation
    #: (asserted by tests/test_common/test_arg_schema.py).
    offset: int | None = None
    #: Byte order. Declared, never assumed. 'big' everywhere on EO-Sat-1.
    endian: str = "big"
    #: Engineering unit the OPERATOR types/reads ("degC", "ms", "").
    unit: str = ""
    #: Raw on-the-wire unit when it differs from `unit` ("cK", "cs").
    raw_unit: str = ""
    #: Raw -> engineering conversion. None => identity.
    calibration: TCFieldCalibration | None = None
    #: Valid range in ENGINEERING units, inclusive.
    range: TCFieldRange | None = None
    #: Derived raw-value range; asserted by test, not hand-kept.
    raw_range: TCFieldRange | None = None
    #: Default in engineering units (int, float or bool-ish int).
    default: float | int | None = None
    #: Enumerated values (calibration.type == 'enum').
    enum: list[TCFieldEnumValue] = Field(default_factory=list)
    #: Bit definitions (calibration.type == 'bitfield').
    bits: list[TCFieldBitDef] = Field(default_factory=list)
    reserved: bool = False
    criticality: str | None = None


class TCReadbackDef(StrictModel):
    """Which TM parameter verifies this command took effect."""
    param_id: int
    description: str = ""


class TCCommandDef(StrictModel):
    name: str
    service: int
    subtype: int
    description: str = ""
    fields: list[TCFieldDef] = Field(default_factory=list)
    # Optional S8 function id (carried through so the MCS catalog/position
    # access-control endpoints can filter S8 commands by func_id) and the
    # operator position/subsystem grouping used by catalog-driven displays.
    func_id: int | None = None
    position: str | None = None
    aliases: list[str] = Field(default_factory=list)
    criticality: str | None = None
    # ── Phase E argument schema (brief 04 WP-0) ──
    arg_schema_version: int | None = None
    #: TM parameter that verifies the command (rendered by the builder as
    #: "Readback: payload.fpa_setpoint (0x0631)").
    readback: TCReadbackDef | None = None
    #: Deliberate legacy alternative wire forms (S8 func 65's 4-byte raw-bps
    #: form). Documentation-only: consumers encode the primary form.
    deprecated_aliases: list[dict[str, Any]] = Field(default_factory=list)

class EventDefinition(StrictModel):
    """Event catalog entry."""
    id: int
    name: str
    severity: str = "INFO"
    subsystem: str = ""
    description: str = ""

class EventCatalog(StrictModel):
    """Event catalog."""
    events: list[EventDefinition] = Field(default_factory=list)

class PowerLineConfig(StrictModel):
    """Power line definition."""
    name: str
    switchable: bool = True
    default_on: bool = True
    power_w: float = 0.0
    equipment: str = ""

# ---------------------------------------------------------------------------
# Simulation tuning configuration (configs/eosat1/simulation.yaml)
#
# Single, editable home for the simulation/scenario parameters that used to be
# Python literals (defect #51). Every field defaults to the previous hard-coded
# value, so a config directory WITHOUT simulation.yaml behaves exactly as
# before. See docs/SIMULATION_CONFIG.md for the key → meaning → effect table.
# ---------------------------------------------------------------------------

class SimSpeedConfig(StrictModel):
    """Instructor time-acceleration limits."""
    default: float = 1.0
    max: float = 1000.0


class SimPhaseConfig(StrictModel):
    """Spacecraft phase state-machine timing."""
    separation_timer_s: float = 1800.0


class SimTTCConfig(StrictModel):
    """TTC autonomy timing owned by the engine (not the TTC model)."""
    auto_tx_hold_s: float = 900.0
    # Real-time TM downlink pacing. Before this, `tm_data_rate` was read only
    # by the S15 dump pacer, so switching between 1 kbps and 64 kbps changed
    # nothing about real-time HK/event/verification TM (defect #74).
    #   "auto"  — pace only while the engine owns the clock (start()/_tick_once)
    #   "on"    — always pace
    #   "off"   — never pace (previous behaviour)
    rt_tm_pacing: str = "auto"
    # Backlog cap; packets beyond this are dropped as the on-board downlink
    # buffer would overflow.
    rt_tm_backlog_max: int = 2000


class SimHousekeepingConfig(StrictModel):
    """Default enable state of the HK SIDs at power-up.

    The operations manual says HK packet generation is enabled *by command*
    (S3.5 HK_ENABLE), but the engine used to switch every SID on at startup and
    again on application boot, which made HK_ENABLE a no-op and hid the
    commissioning step entirely (defect #75).
    """
    # SIDs enabled at power-up / on application boot. Everything else must be
    # enabled from the ground.
    default_enabled_sids: list[int] = Field(default_factory=lambda: [1, 11])
    # True restores the old "everything on" behaviour.
    enable_all_by_default: bool = False


class SimEPSInitialConfig(StrictModel):
    """EPS initial conditions at engine start."""
    soc_pct: float = 75.0
    bat_temp_c: float = 15.0


class SimEPSLoadShedConfig(StrictModel):
    """In-model bus-voltage load shed + FDIR SoC shed ladder."""
    order: list[str] = Field(
        default_factory=lambda: ["payload", "fpa_cooler", "ttc_tx", "aocs_wheels"])
    bus_voltage_v: float = 26.5
    restore_hysteresis_v: float = 0.5
    # Automatic restore of shed lines once the bus recovers above
    # bus_voltage_v + restore_hysteresis_v — one line per tick, reverse shed
    # order (brief 01 F15 / WP-7). False = legacy (operator restores).
    auto_restore: bool = False
    # FDIR load-shed stages 1/2/3 entry thresholds (battery SoC %), highest
    # first. Canonical source for LoadSheddingManager.get_required_stage.
    soc_stage_thresholds_pct: list[float] = Field(
        default_factory=lambda: [30.0, 20.0, 10.0])


class SimEPSOvercurrentConfig(StrictModel):
    """Per-line overcurrent protection."""
    trip_time_s: float = 0.05
    thresholds_a: dict[str, float] = Field(default_factory=dict)


class SimEPSEventThresholds(StrictModel):
    """EPS onboard event (S5) trigger levels."""
    soc_low_pct: float = 20.0
    soc_critical_pct: float = 10.0
    soc_charge_complete_pct: float = 99.0
    bus_warning_v: float = 27.0
    bus_critical_v: float = 25.0
    bat_overtemp_c: float = 45.0
    bat_undertemp_c: float = -5.0
    sa_degraded_fraction: float = 0.7


class SimEPSConfig(StrictModel):
    """EPS simulation parameters (merged over subsystems/eps.yaml)."""
    initial: SimEPSInitialConfig = Field(default_factory=SimEPSInitialConfig)
    # Line wattages that the tick code bills directly and that have no home in
    # eps.yaml. Lines absent here fall back to the in-code defaults.
    line_power_w: dict[str, float] = Field(default_factory=dict)
    # Bill heater lines from the ACTUAL thermostat/heater state published by
    # TCS (0x040A/0x040B) instead of 100 % duty whenever the power line is ON
    # (brief 01 F10 / WP-5, brief 05 WP-5). False = legacy behaviour.
    heater_duty_from_tcs: bool = False
    uv_threshold_v: float = 26.5
    ov_threshold_v: float = 29.5
    load_shed: SimEPSLoadShedConfig = Field(default_factory=SimEPSLoadShedConfig)
    overcurrent: SimEPSOvercurrentConfig = Field(default_factory=SimEPSOvercurrentConfig)
    event_thresholds: SimEPSEventThresholds = Field(default_factory=SimEPSEventThresholds)


class SimTCSEnvironmentConfig(StrictModel):
    """Thermal environment driving zone temperatures."""
    eclipse_external_c: float = -30.0
    eclipse_internal_c: float = 10.0
    sunlit_external_base_c: float = -10.0
    sunlit_external_beta_gain_c: float = 50.0
    sunlit_internal_c: float = 12.0
    # Battery-zone internal sink. The battery pack sits against the radiator
    # side of the bus, colder than the electronics stack, so the battery
    # thermostat is genuinely exercised (brief 05 defect 2 / WP-3). None =
    # inherit eclipse_internal_c / sunlit_internal_c (legacy behaviour).
    eclipse_battery_c: float | None = None
    sunlit_battery_c: float | None = None
    panel_solar_heat_fraction: float = 0.3
    # TRIM multiplier on the physically-derived panel solar gain (1/UA per
    # node). The old value 10.0 was applied ON TOP of an implicit 1/UA and
    # made the gain ~80x too large (brief 05 finding 1 / WP-8): panel
    # equilibrium temperatures reached +362 C. 1.0 = physical.
    panel_solar_heat_gain: float = 1.0


class SimTCSConfig(StrictModel):
    environment: SimTCSEnvironmentConfig = Field(default_factory=SimTCSEnvironmentConfig)


class SimPropConfig(StrictModel):
    """simulation.yaml prop: — propulsion sim-behaviour tuning (Phase D)."""
    # Engine clamps time acceleration to this while a burn is FIRING
    # (event 0x0811 PROP_SPEED_CLAMPED) — at 1000x a whole burn would fit
    # inside one tick and the abort lessons would be unobservable.
    max_speed_during_burn: float = 10.0
    # Teaching override: allow arming/burning in eclipse (scenario knob).
    allow_eclipse_burn: bool = False
    # Override the loaded propellant (kg); None = prop.yaml propellant_mass_kg
    initial_propellant_kg: float | None = None


class SimFailureIntermittentConfig(StrictModel):
    first_flicker_s: list[float] = Field(default_factory=lambda: [2.0, 6.0])
    on_s: list[float] = Field(default_factory=lambda: [2.0, 8.0])
    off_s: list[float] = Field(default_factory=lambda: [5.0, 20.0])


class SimFailureConfig(StrictModel):
    default_onset_duration_s: float = 90.0
    intermittent: SimFailureIntermittentConfig = Field(
        default_factory=SimFailureIntermittentConfig)


class SimScenarioDefaultsConfig(StrictModel):
    default_duration_s: float = 1800.0
    default_difficulty: str = "BASIC"
    eq_tolerance: float = 0.01
    valid_difficulties: list[str] = Field(default_factory=lambda: [
        "BASIC", "BEGINNER", "INTERMEDIATE", "ADVANCED"])


class SimTickConfig(StrictModel):
    """Sub-stepped tick integration limits (plan A-3).

    When one tick's simulated timestep dt_sim = dt_real x speed exceeds
    ``dt_max_s``, the engine splits the physics chain into
    N = ceil(dt_sim / dt_max_s) equal sub-steps (capped at ``max_substeps``;
    if the cap binds, the per-substep dt grows above dt_max_s rather than
    truncating simulated time). Per-tick outputs (HK sampling, TM release,
    dump pacing) stay at the outer tick cadence.
    """
    dt_max_s: float = 1.0
    max_substeps: int = 600


class SimulationConfig(StrictModel):
    """configs/<mission>/simulation.yaml — simulation/scenario tuning layer."""

    # None = inherit mission.yaml network.sim_tick_hz (which itself defaults 1.0)
    tick_hz: float | None = None
    # Master RNG seed (plan A-2). One seeded random.Random per engine
    # component is derived from this, so same seed + same t0_epoch + same
    # command script => identical parameter trace.
    seed: int = 20260731
    tick: SimTickConfig = Field(default_factory=SimTickConfig)
    speed: SimSpeedConfig = Field(default_factory=SimSpeedConfig)
    phases: SimPhaseConfig = Field(default_factory=SimPhaseConfig)
    ttc: SimTTCConfig = Field(default_factory=SimTTCConfig)
    housekeeping: SimHousekeepingConfig = Field(
        default_factory=SimHousekeepingConfig)
    # "t0"   — start the propagator at orbit.yaml t0_epoch (reproducible runs)
    # "wall" — start at the current wall clock (previous behaviour)
    epoch: str = "t0"
    eps: SimEPSConfig = Field(default_factory=SimEPSConfig)
    tcs: SimTCSConfig = Field(default_factory=SimTCSConfig)
    prop: SimPropConfig = Field(default_factory=SimPropConfig)
    failures: SimFailureConfig = Field(default_factory=SimFailureConfig)
    scenario_defaults: SimScenarioDefaultsConfig = Field(
        default_factory=SimScenarioDefaultsConfig)


class ScenarioEventConfig(StrictModel):
    time_offset_s: float | None = None
    condition: str | None = None
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    #: Instructor-facing rationale for the event. Documentation, not an
    #: input — but it has to be declared to be allowed to exist.
    note: str = ""

class ScenarioConfig(StrictModel):
    name: str
    difficulty: str = "BASIC"
    duration_s: float = 1800.0
    briefing: str = ""
    events: list[ScenarioEventConfig] = Field(default_factory=list)
    expected_responses: list[dict[str, str]] = Field(default_factory=list)
    #: Spacecraft state the scenario starts from (phase, SoC, mode, ...).
    #: Applied by `ScenarioEngine.activate`; the shape is scenario-specific.
    initial_conditions: dict[str, Any] = Field(default_factory=dict)

class DisplayWidgetConfig(StrictModel):
    type: str
    parameter: str | None = None
    parameters: list[str] | None = None
    label: str = ""
    range: list[float] | None = None
    units: str = ""
    limits_ref: str | None = None
    duration_s: float | None = None
    #: Trend widgets: width of the plotted window in seconds.
    window_s: float | None = None

class DisplayPageConfig(StrictModel):
    name: str
    widgets: list[DisplayWidgetConfig] = Field(default_factory=list)

class PositionConfig(StrictModel):
    label: str = ""
    display_name: str = ""
    subsystems: list[str] = Field(default_factory=list)
    pages: list[DisplayPageConfig] = Field(default_factory=list)
    allowed_commands: str | None = None  # "all" or None
    allowed_subsystems: list[str] = Field(default_factory=list)
    allowed_services: list[int] = Field(default_factory=list)
    allowed_func_ids: list[int] = Field(default_factory=list)
    visible_tabs: list[str] = Field(default_factory=list)
    overview_subsystems: list[str] = Field(default_factory=list)
    manual_sections: str | list[str] = Field(default_factory=list)  # "all" or list

class MCSDisplayConfig(StrictModel):
    positions: dict[str, PositionConfig] = Field(default_factory=dict)
    #: Legacy config-driven display pages (`mcs/displays.yaml: displays:`).
    #: Their only consumer was `smo_mcs.displays.DisplayEngine`, deleted as
    #: dead code (brief 08 finding 9 / WP-12); the block is declared so the
    #: file still validates, and is a deletion candidate for the MCS lane.
    displays: dict[str, PositionConfig] = Field(default_factory=dict)


# ===== Phase 3: Config-Driven Schemas =====

class MemoryRegionConfig(StrictModel):
    """Onboard memory region definition for S6 Memory Management."""
    name: str
    start: int = Field(description="Start address (hex-encoded in YAML)")
    size: int = Field(description="Region size in bytes")
    type: str = Field(default="flash", description="Memory type: readonly, flash, ram")
    description: str = ""


class MemoryMapConfig(StrictModel):
    """Complete onboard memory map.

    `memory_regions` is the pydantic-era key; the shipped
    `memory/memory_map.yaml` uses `regions`, which is what
    `smo_simulator.memory_map.MemoryMap.load()` reads, plus a `limits` block
    for the S6 request-size caps. Both are declared so the file validates as
    written rather than being half-dropped.
    """
    memory_regions: list[MemoryRegionConfig] = Field(default_factory=list)
    regions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Regions as consumed by MemoryMap.load(): name, start, "
                    "size, access, fill_kind, fill, patchable, binding")
    limits: dict[str, Any] = Field(
        default_factory=dict,
        description="S6 request caps (max load/dump/check bytes)")


class PUSStoreConfig(StrictModel):
    """S15 onboard storage store definition."""
    id: int
    name: str
    capacity_bytes: int


class PUSServiceConfig(StrictModel):
    """PUS service configuration entry."""
    label: str = ""
    enabled: bool = True
    description: str = ""
    default_interval_s: float | None = None
    max_sids: int | None = None
    max_log_entries: int | None = None
    max_dump_bytes: int | None = None
    max_commands: int | None = None
    max_definitions: int | None = None
    max_rules: int | None = None
    max_procedures: int | None = None
    max_steps_per_procedure: int | None = None
    stores: list[PUSStoreConfig] | None = None


class PUSServicesConfig(StrictModel):
    """All PUS service configurations."""
    services: dict[str, PUSServiceConfig] = Field(default_factory=dict)


class ProcedureIndexEntry(StrictModel):
    """Procedure catalog entry linking procedures to files and roles."""
    id: str
    name: str
    file: str
    category: str = ""
    required_positions: list[str] = Field(default_factory=list)
    command_services: list[int] = Field(default_factory=list)
    #: Per-position role text shown beside the procedure in the MCS
    #: procedure browser (who does what during the run).
    position_roles: dict[str, str] = Field(default_factory=dict)
    #: Machine-runnable form of the procedure:
    #: ``{kind: activity_type|steps_file|markdown, ref: <id or path>}``
    #: (roadmap X-12 / defect #83). `smo_mcs.server` resolves it and exposes
    #: `runnable: true/false` to the Procedures tab.
    runnable: dict[str, str] | None = None
    #: The card DECLARES that it commands nothing: it is a document, and an
    #: empty `command_services` is a stated property rather than the 2026-08b
    #: defect in which commanding was hidden behind `**Action:**` prose and the
    #: card was silently inert. Enforced by
    #: `tests/test_content/test_procedure_gates.py` and
    #: `tools/procedure_audit.py` (ZERO_COMMAND_PROCEDURE), both of which
    #: require `advisory_reason` to be non-empty whenever `advisory` is set.
    advisory: bool = False
    #: Why the card commands nothing. Required when `advisory` is true.
    advisory_reason: str = ""


class ProcedureIndex(StrictModel):
    """Master index of all procedures."""
    procedures: list[ProcedureIndexEntry] = Field(default_factory=list)


class CommandStepConfig(StrictModel):
    """A single step in an activity command sequence."""
    service: int | None = None
    subtype: int | None = None
    func_id: int | None = None
    store_id: int | None = None
    sid: int | None = None
    description: str = ""
    wait_s: float | None = None
    wait_for: dict[str, Any] | None = None


class ActivityTypeConfig(StrictModel):
    """Enhanced activity type with procedure references and command sequences."""
    name: str
    description: str = ""
    duration_s: float = 300
    power_w: float = 0
    data_volume_mb: float = 0
    requires_attitude: str | None = None
    requires_daylight: bool = False
    requires_contact: bool = False
    priority: str = "medium"
    procedure_ref: str | None = None
    command_sequence: list[dict[str, Any]] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    pre_conditions: list[str] = Field(default_factory=list)
    # Declared, but not yet consumed by the scheduler — see the KNOWN_UNUSED
    # entries in tests/test_content/test_config_keys_have_consumers.py. They
    # stay in the file (they are the planner's next work package), so they
    # have to be in the schema for extra="forbid" to be satisfiable.
    min_duration_s: float | None = None
    max_duration_s: float | None = None
    requires_subsystems: list[str] = Field(default_factory=list)
    thermal_constraints: dict[str, Any] = Field(default_factory=dict)
    aocs_constraints: dict[str, Any] = Field(default_factory=dict)


class ActivityTypesConfig(StrictModel):
    """All activity type definitions."""
    activity_types: list[ActivityTypeConfig] = Field(default_factory=list)
