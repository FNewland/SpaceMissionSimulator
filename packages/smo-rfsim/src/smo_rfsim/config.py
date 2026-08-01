"""RF simulation bridge configuration.

Sections are plain dataclasses with an explicit ``validate()`` so that a
bad value fails at load time with a message naming the key — the same
contract a pydantic model would give, without adding a dependency the
package does not otherwise carry.

Unknown keys are rejected rather than silently dropped: the previous
``hasattr`` filter is what made ``channel.frequency_hz`` dead config for
the whole life of the project (defect #55).
"""

import logging
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any, Optional

import yaml

from .mode import RFSimMode

logger = logging.getLogger(__name__)

# CCSDS TM Transfer Frame defaults (CCSDS 132.0-B-3)
DEFAULT_TM_FRAME_LENGTH = 1115  # bytes (including ASM)
DEFAULT_SCID = 1
DEFAULT_VC_REALTIME = 0
DEFAULT_VC_PLAYBACK = 1
DEFAULT_VC_IDLE = 7

# EOSAT-1 flies an S-band TT&C link (CCSDS 401.0-B near-Earth allocations):
# 2025–2110 MHz uplink, 2200–2290 MHz downlink. This matches
# configs/eosat1/planning/ground_stations.yaml and the course textbook.
DEFAULT_UPLINK_HZ = 2025.0e6
DEFAULT_DOWNLINK_HZ = 2200.0e6


class ConfigError(ValueError):
    """Raised for an unknown key or an out-of-range value in rfsim.yaml."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _coerce(value: Any, declared: Any, key: str) -> Any:
    """Coerce a YAML scalar to the field's declared type.

    Needed because YAML 1.1 (PyYAML) only recognises a float exponent when
    it carries an explicit sign — ``2200.0e6`` parses as the *string*
    "2200.0e6", not a number. Writing that in a config and getting a
    TypeError deep in the channel model is exactly the class of silent
    breakage this module is meant to prevent.
    """
    if value is None or declared is None:
        return value
    try:
        if declared is bool:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if declared is float and not isinstance(value, bool):
            return float(value)
        if declared is int and not isinstance(value, bool):
            return int(value)
        if declared is str:
            return str(value)
    except (TypeError, ValueError):
        raise ConfigError(
            f"'{key}' must be a {getattr(declared, '__name__', declared)}, "
            f"got {value!r}")
    return value


def _apply_section(section: Any, raw: dict, name: str) -> None:
    """Copy YAML keys onto a dataclass section, rejecting unknown keys."""
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ConfigError(f"'{name}' must be a mapping, got {type(raw).__name__}")
    declared = {f.name: f.type for f in fields(section)}
    for k, v in raw.items():
        if k not in declared:
            raise ConfigError(
                f"Unknown key '{name}.{k}' in RF sim config. "
                f"Valid keys: {sorted(declared)}")
        setattr(section, k, _coerce(v, declared[k], f"{name}.{k}"))


@dataclass
class CCSDSConfig:
    """CCSDS Transfer Frame parameters."""
    tm_frame_length: int = DEFAULT_TM_FRAME_LENGTH
    scid: int = DEFAULT_SCID
    vc_realtime: int = DEFAULT_VC_REALTIME
    vc_playback: int = DEFAULT_VC_PLAYBACK
    vc_idle: int = DEFAULT_VC_IDLE
    fecf_present: bool = True
    rs_enabled: bool = True
    convolutional_enabled: bool = True
    # Follow the spacecraft's active TM rate tier's FEC (defect #85): the
    # BEACON and LOW tiers code conv(7,1/2)+RS, NOMINAL and HIGH code RS only.
    # Opt-in because the pure-Python Viterbi decoder (~5 s/frame) is only
    # affordable at the 1-16 kbps tiers; leave it False to run RS-only
    # everywhere and cost the low tiers ~1.5 dB, which their >25 dB margins
    # absorb without trouble.
    convolutional_follow_tier: bool = False

    def validate(self) -> None:
        _require(64 <= self.tm_frame_length <= 65535,
                 "ccsds.tm_frame_length must be 64..65535")
        _require(0 <= self.scid <= 1023, "ccsds.scid must be 0..1023")
        for key in ("vc_realtime", "vc_playback", "vc_idle"):
            _require(0 <= getattr(self, key) <= 7, f"ccsds.{key} must be 0..7")


@dataclass
class PipelineConfig:
    """Continuous RF pipeline (TX → channel → RX) parameters.

    Read by ``PipelineCoordinator``. Before the 2026-07 fixes this section
    did not exist on ``RFSimConfig`` at all, so the coordinator's
    ``getattr(config, 'pipeline', None)`` always yielded ``None`` and the
    pipeline ran permanently at 32 kBd / BPSK regardless of the commanded
    telemetry rate (defect #55).
    """
    modulation: int = 0             # 0=BPSK 1=QPSK 2=8PSK 3=OQPSK
                                    # 4=16-APSK 5=π/4-DQPSK 6=GMSK 7=GFSK
    symbol_rate: float = 32000.0    # baud
    sps: int = 8                    # samples per symbol
    rrc_rolloff: float = 0.35
    gmsk_bt: float = 0.5            # Gaussian BT product (GMSK/GFSK)
    gfsk_mod_index: float = 0.5     # GFSK modulation index h
    gray_coding: bool = True        # Gray-code the PSK symbol maps
    sample_buffer_depth: int = 128
    tc_symbol_rate: float = 4000.0
    tc_sps: int = 16
    rx_loop_bw: float = 0.08
    tc_uplink_eb_n0_db: float = 25.0
    # Track the commanded TM bit rate by retuning the symbol rate.
    track_data_rate: bool = True
    min_symbol_rate: float = 250.0
    # Re-derived from the regulation rather than picked (defect #85). NASA's
    # S-band table caps a mission at 5.00 MHz occupied in 2200-2290 MHz, and
    # occupied BW = (1 + rrc_rolloff) x symbol rate, so at alpha = 0.35 the
    # legal ceiling is 5.00e6 / 1.35 = 3.70 MBd. Anything the mission may
    # lawfully transmit is therefore accepted, and anything rejected is
    # rejected because it would breach the channel allocation — not because
    # of an arbitrary constant. (The shipped HIGH tier is 128 kbps QPSK =
    # 64 kBd, 1.7 % of this; the old 200 kBd ceiling was below what the
    # four-tier draft needed and would silently clamp instead of failing.)
    #
    # NOTE: baseband cost is sample_rate = symbol_rate x sps. At the ceiling
    # that is ~30 MHz with sps=8, which is a CPU concern, not a legality one;
    # drop `sps` if you ever run near the top of this range.
    max_symbol_rate: float = 3700000.0

    def validate(self) -> None:
        _require(self.modulation in range(8),
                 "pipeline.modulation must be 0..7")
        _require(self.min_symbol_rate <= self.symbol_rate <= self.max_symbol_rate,
                 f"pipeline.symbol_rate must be "
                 f"{self.min_symbol_rate}..{self.max_symbol_rate} Bd")
        _require(2 <= self.sps <= 64, "pipeline.sps must be 2..64")
        _require(0.0 <= self.rrc_rolloff <= 1.0,
                 "pipeline.rrc_rolloff must be 0.0..1.0")
        _require(0.1 <= self.gmsk_bt <= 1.0, "pipeline.gmsk_bt must be 0.1..1.0")
        _require(0.0 < self.gfsk_mod_index <= 2.0,
                 "pipeline.gfsk_mod_index must be >0 and <=2")
        _require(self.sample_buffer_depth >= 4,
                 "pipeline.sample_buffer_depth must be >= 4")
        _require(self.tc_symbol_rate > 0, "pipeline.tc_symbol_rate must be > 0")
        _require(2 <= self.tc_sps <= 64, "pipeline.tc_sps must be 2..64")
        _require(0.0 <= self.rx_loop_bw < 1.0,
                 "pipeline.rx_loop_bw must be 0.0..1.0")


@dataclass
class ChannelConfig:
    """Channel model parameters (AWGN + RF impairments).

    Everything below ``awgn_enabled`` maps onto ``SpaceLinkConfig`` and is
    applied to the live sample stream by ``ChannelStage``.
    """
    eb_n0_db: float = 10.0          # Eb/N0 in dB
    ber_target: float = 1e-6        # target BER for bit-flip injection (FRAME mode)
    path_loss_db: float = 152.4     # free-space path loss, 450 km @ 2200 MHz
    doppler_hz: float = 0.0         # Doppler frequency shift
    delay_ms: float = 1.5           # one-way propagation delay (450 km)
    range_km: float = 0.0           # slant range (updated from sim)
    awgn_enabled: bool = True
    # RF plan (S-band, CCSDS 401.0-B near-Earth)
    frequency_hz: float = DEFAULT_DOWNLINK_HZ    # downlink centre frequency
    uplink_frequency_hz: float = DEFAULT_UPLINK_HZ
    # Oscillator phase noise (Wiener process)
    phase_noise_enabled: bool = False
    phase_noise_linewidth_hz: float = 10.0
    # Rician fading (Jakes model)
    fading_enabled: bool = False
    fading_k_factor_db: float = 10.0
    fading_max_doppler_hz: float = 50.0
    # Multipath delay spread
    multipath_enabled: bool = False
    multipath_taps: list = field(default_factory=list)
    # Interferers: [{type: cw, freq_offset_hz: 5000, power_dbm: -90}, ...]
    interferers: list = field(default_factory=list)
    # Deterministic impairment RNG (tests / reproducible lessons)
    seed: int = 42

    def validate(self) -> None:
        _require(-20.0 <= self.eb_n0_db <= 100.0,
                 "channel.eb_n0_db must be -20..100 dB")
        _require(0.0 < self.ber_target < 1.0,
                 "channel.ber_target must be in (0, 1)")
        _require(1.0e6 <= self.frequency_hz <= 60.0e9,
                 "channel.frequency_hz must be 1 MHz..60 GHz")
        _require(1.0e6 <= self.uplink_frequency_hz <= 60.0e9,
                 "channel.uplink_frequency_hz must be 1 MHz..60 GHz")
        _require(self.phase_noise_linewidth_hz >= 0.0,
                 "channel.phase_noise_linewidth_hz must be >= 0")
        _require(-30.0 <= self.fading_k_factor_db <= 40.0,
                 "channel.fading_k_factor_db must be -30..40 dB")
        _require(self.fading_max_doppler_hz >= 0.0,
                 "channel.fading_max_doppler_hz must be >= 0")
        _require(isinstance(self.interferers, list),
                 "channel.interferers must be a list")
        for i, intf in enumerate(self.interferers):
            _require(isinstance(intf, dict),
                     f"channel.interferers[{i}] must be a mapping")
            itype = intf.get("type", "cw")
            _require(isinstance(itype, str),
                     f"channel.interferers[{i}].type must be a string")


@dataclass
class LinkBudgetConfig:
    """CCSDS 401.0-B link budget inputs (mirrors ``LinkBudget.__init__``)."""
    enabled: bool = True
    eirp_dbw: float = 10.0
    gs_gt_dbk: float = 20.0
    atmospheric_loss_db: float = 0.5
    implementation_loss_db: float = 2.0
    polarization_loss_db: float = 0.3
    coding_gain_db: float = 6.0
    # Per-station G/T comes from configs/eosat1/planning/ground_stations.yaml.
    # Path is relative to the rfsim.yaml file that names it.
    ground_stations_file: Optional[str] = None
    default_ground_station: Optional[str] = None
    #: {station name: G/T dB/K}, populated from ground_stations_file at load.
    station_gt_dbk: dict = field(default_factory=dict)

    def validate(self) -> None:
        _require(-30.0 <= self.eirp_dbw <= 90.0,
                 "link_budget.eirp_dbw must be -30..90 dBW")
        _require(-30.0 <= self.gs_gt_dbk <= 60.0,
                 "link_budget.gs_gt_dbk must be -30..60 dB/K")
        for key in ("atmospheric_loss_db", "implementation_loss_db",
                    "polarization_loss_db"):
            _require(getattr(self, key) >= 0.0, f"link_budget.{key} must be >= 0")
        _require(0.0 <= self.coding_gain_db <= 15.0,
                 "link_budget.coding_gain_db must be 0..15 dB")

    def gt_for_station(self, name: Optional[str]) -> float:
        """G/T for a named ground station, falling back to the default."""
        if name and name in self.station_gt_dbk:
            return float(self.station_gt_dbk[name])
        if (self.default_ground_station
                and self.default_ground_station in self.station_gt_dbk):
            return float(self.station_gt_dbk[self.default_ground_station])
        return float(self.gs_gt_dbk)

    def load_ground_stations(self, base_dir: Path) -> None:
        """Read per-station G/T from the planner's ground-station list.

        The planner owns that file; this only *reads* ``name`` and
        ``g_t_db`` so the link budget reflects the station actually in
        contact instead of a single hard-coded number.
        """
        if not self.ground_stations_file:
            return
        path = Path(self.ground_stations_file)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        if not path.exists():
            logger.warning("link_budget.ground_stations_file not found: %s", path)
            return
        try:
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            logger.warning("Cannot read ground stations file %s: %s", path, e)
            return
        stations = raw.get("ground_stations") or []
        for st in stations:
            if not isinstance(st, dict):
                continue
            name = st.get("name")
            gt = st.get("g_t_db")
            if name is not None and gt is not None:
                self.station_gt_dbk[str(name)] = float(gt)
        if self.station_gt_dbk and not self.default_ground_station:
            self.default_ground_station = next(iter(self.station_gt_dbk))
        logger.info("Link budget: loaded G/T for %d ground stations from %s",
                    len(self.station_gt_dbk), path)


@dataclass
class NetworkConfig:
    """Network port assignments."""
    # Upstream (simulator) connections
    sim_tm_host: str = "127.0.0.1"
    sim_tm_port: int = 8002
    sim_tc_host: str = "127.0.0.1"
    sim_tc_port: int = 8001
    sim_ws_url: str = "ws://127.0.0.1:8080/ws"
    # Downstream (MCS) listen ports
    mcs_tm_port: int = 8012
    mcs_tc_port: int = 8011
    mcs_bind: str = "0.0.0.0"
    # Radio web UI
    radio_port: int = 8094
    # ZMQ PUB port for raw baseband sample stream (0 = disabled)
    zmq_samples_port: int = 5555
    # Enable the radio UI control plane (POST routes). Off by default so
    # the "purely observational" contract holds unless opted into.
    radio_control_enabled: bool = False

    def validate(self) -> None:
        for key in ("sim_tm_port", "sim_tc_port", "mcs_tm_port",
                    "mcs_tc_port", "radio_port"):
            port = getattr(self, key)
            _require(1 <= port <= 65535, f"network.{key} must be 1..65535")
        _require(0 <= self.zmq_samples_port <= 65535,
                 "network.zmq_samples_port must be 0..65535")


@dataclass
class RFSimConfig:
    """Top-level RF simulation configuration."""
    mode: RFSimMode = RFSimMode.PACKET
    ccsds: CCSDSConfig = field(default_factory=CCSDSConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    link_budget: LinkBudgetConfig = field(default_factory=LinkBudgetConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)

    _SECTIONS = ("ccsds", "pipeline", "channel", "link_budget", "network")

    def validate(self) -> None:
        """Validate every section; raises ConfigError on the first problem."""
        for name in self._SECTIONS:
            getattr(self, name).validate()

    @property
    def sample_rate_hz(self) -> float:
        """Baseband sample rate of the live RF pipeline."""
        return self.pipeline.symbol_rate * self.pipeline.sps

    def to_dict(self) -> dict:
        """Serialisable snapshot of the effective configuration."""
        out = {"mode": self.mode.value}
        for name in self._SECTIONS:
            out[name] = asdict(getattr(self, name))
        out["sample_rate_hz"] = self.sample_rate_hz
        return out

    @classmethod
    def from_yaml(cls, path: Path, strict: bool = True) -> "RFSimConfig":
        """Load configuration from a YAML file.

        With ``strict`` (the default) an unrecognised key raises
        ``ConfigError`` instead of being silently discarded.
        """
        path = Path(path)
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        cfg = cls()
        known_top = set(cls._SECTIONS) | {"mode"}
        for key in raw:
            if key not in known_top:
                msg = (f"Unknown top-level key '{key}' in {path}. "
                       f"Valid: {sorted(known_top)}")
                if strict:
                    raise ConfigError(msg)
                logger.warning("%s", msg)
        if "mode" in raw:
            cfg.mode = RFSimMode(str(raw["mode"]).upper())
        for name in cls._SECTIONS:
            if name in raw:
                try:
                    _apply_section(getattr(cfg, name), raw[name], name)
                except ConfigError:
                    if strict:
                        raise
                    logger.warning("Ignoring bad '%s' section in %s", name, path)
        cfg.link_budget.load_ground_stations(path.parent)
        cfg.validate()
        return cfg
