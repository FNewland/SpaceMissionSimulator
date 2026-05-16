"""RF simulation bridge configuration."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .mode import RFSimMode

logger = logging.getLogger(__name__)

# CCSDS TM Transfer Frame defaults (CCSDS 132.0-B-3)
DEFAULT_TM_FRAME_LENGTH = 1115  # bytes (including ASM)
DEFAULT_SCID = 1
DEFAULT_VC_REALTIME = 0
DEFAULT_VC_PLAYBACK = 1
DEFAULT_VC_IDLE = 7


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


@dataclass
class ChannelConfig:
    """Channel model parameters."""
    eb_n0_db: float = 10.0          # Eb/N0 in dB
    ber_target: float = 1e-6        # target BER for bit-flip injection
    path_loss_db: float = 150.0     # free-space path loss
    doppler_hz: float = 0.0         # Doppler frequency shift
    delay_ms: float = 3.0           # one-way propagation delay (450 km)
    range_km: float = 0.0           # slant range (updated from sim)
    awgn_enabled: bool = True


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


@dataclass
class RFSimConfig:
    """Top-level RF simulation configuration."""
    mode: RFSimMode = RFSimMode.PACKET
    ccsds: CCSDSConfig = field(default_factory=CCSDSConfig)
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> "RFSimConfig":
        """Load configuration from a YAML file."""
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        cfg = cls()
        if "mode" in raw:
            cfg.mode = RFSimMode(raw["mode"].upper())
        if "ccsds" in raw:
            for k, v in raw["ccsds"].items():
                if hasattr(cfg.ccsds, k):
                    setattr(cfg.ccsds, k, v)
        if "channel" in raw:
            for k, v in raw["channel"].items():
                if hasattr(cfg.channel, k):
                    setattr(cfg.channel, k, v)
        if "network" in raw:
            for k, v in raw["network"].items():
                if hasattr(cfg.network, k):
                    setattr(cfg.network, k, v)
        return cfg
