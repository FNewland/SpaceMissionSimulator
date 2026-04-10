"""SMO Common — Configuration Schemas (Pydantic v2)."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class MissionConfig(BaseModel):
    """Top-level mission identity."""
    name: str = "EOSAT-1"
    spacecraft_apid: int = Field(default=0x01, description="Application-software APID for TM packets (OBC app running)")
    bootloader_apid: int = Field(default=0x02, description="Bootloader APID — distinct from application APID; used for beacon/bootloader TM while OBC is in bootloader")
    start_in_bootloader: bool = Field(default=True, description="If True, engine powers up in bootloader/beacon mode (phase 3) with only SID 11 active and bootloader APID")
    pus_version: int = 2
    time_epoch: str = "2000-01-01T12:00:00"

class NetworkConfig(BaseModel):
    """Simulation server network ports."""
    tc_port: int = 8001
    tm_port: int = 8002
    instr_port: int = 8003
    http_port: int = 8080
    sim_tick_hz: float = 1.0
    tm_max_clients: int = 8

class GroundStationConfig(BaseModel):
    """Ground station definition."""
    name: str = "Svalbard"
    lat_deg: float = 78.229
    lon_deg: float = 15.407
    alt_km: float = 0.458
    min_elevation_deg: float = 5.0

class OrbitConfig(BaseModel):
    """Orbit configuration (TLE-based)."""
    tle_line1: str
    tle_line2: str
    altitude_km: float = 500.0
    inclination_deg: float = 97.4
    earth_radius_km: float = 6371.0
    ground_stations: list[GroundStationConfig] = Field(default_factory=list)

class SolarArrayConfig(BaseModel):
    name: str
    area_m2: float = 0.314
    efficiency: float = 0.295

class BatteryConfig(BaseModel):
    type: str = "li_ion"
    capacity_wh: float = 120.0
    voltage_nom: float = 26.4
    soc_100_v: float = 29.2
    soc_0_v: float = 21.5
    internal_r_ohm: float = 0.05

class EPSConfig(BaseModel):
    model: str = "eps_basic"
    technology: str = ""
    arrays: list[SolarArrayConfig] = Field(default_factory=list)
    battery: BatteryConfig = Field(default_factory=BatteryConfig)
    platform_idle_power_w: float = 95.0
    payload_power_w: float = 45.0
    payload_standby_power_w: float = 8.0
    fpa_cooler_power_w: float = 15.0
    transponder_power_w: float = 20.0
    transponder_rx_power_w: float = 5.0
    param_ids: dict[str, int] = Field(default_factory=dict)

class ReactionWheelConfig(BaseModel):
    max_speed_rpm: int = 5500
    nominal_speed_rpm: int = 1200
    desaturation_speed_rpm: int = 200

class AOCSConfig(BaseModel):
    model: str = "aocs_basic"
    modes: list[str] = Field(default_factory=lambda: ["nominal", "detumble", "safe", "wheel_desat", "slew"])
    reaction_wheels: ReactionWheelConfig = Field(default_factory=ReactionWheelConfig)
    num_wheels: int = 4
    attitude_error_deadband_deg: float = 0.01
    param_ids: dict[str, int] = Field(default_factory=dict)

class HeaterCircuitConfig(BaseModel):
    name: str
    power_w: float
    on_temp_c: float
    off_temp_c: float

class ThermalZoneConfig(BaseModel):
    name: str
    capacitance_j_per_c: float
    time_constant_s: float
    initial_temp_c: float = 15.0

class TCSConfig(BaseModel):
    model: str = "tcs_basic"
    zones: list[ThermalZoneConfig] = Field(default_factory=list)
    heaters: list[HeaterCircuitConfig] = Field(default_factory=list)
    fpa_cooler_target_c: float = -5.0
    param_ids: dict[str, int] = Field(default_factory=dict)

class OBDHConfig(BaseModel):
    model: str = "obdh_basic"
    modes: list[str] = Field(default_factory=lambda: ["nominal", "safe", "emergency"])
    watchdog_period_ticks: int = 30
    cpu_baseline_pct: float = 35.0
    param_ids: dict[str, int] = Field(default_factory=dict)

class TTCConfig(BaseModel):
    model: str = "ttc_basic"
    ul_freq_mhz: float = 2025.5
    dl_freq_mhz: float = 2200.5
    tm_rate_hi_bps: int = 64000
    tm_rate_lo_bps: int = 1000
    eirp_dbw: float = 10.0
    gs_g_t_db: float = 20.0
    sc_gain_dbi: float = 3.0
    param_ids: dict[str, int] = Field(default_factory=dict)

class PayloadConfig(BaseModel):
    model: str = "payload_basic"
    fpa_cooler_target_c: float = -5.0
    fpa_ambient_c: float = 5.0
    fpa_tau_cooling_s: float = 100.0
    fpa_tau_warming_s: float = 120.0
    fpa_cooler_power_w: float = 15.0
    image_size_mb: float = 800.0
    total_storage_mb: float = 20000.0
    line_rate_hz: float = 500.0
    data_rate_mbps: float = 80.0
    param_ids: dict[str, int] = Field(default_factory=dict)

class FDIRRuleConfig(BaseModel):
    parameter: str
    condition: str
    threshold: float | None = None
    level: int = 1
    action: str

class FDIRConfig(BaseModel):
    enabled: bool = True
    rules: list[FDIRRuleConfig] = Field(default_factory=list)

class HKParameterDef(BaseModel):
    param_id: int
    pack_format: str
    scale: float = 1.0

class HKStructureDef(BaseModel):
    sid: int
    name: str
    interval_s: float = 4.0
    parameters: list[HKParameterDef] = Field(default_factory=list)

class ParameterDef(BaseModel):
    id: int
    name: str
    subsystem: str
    type: str = "float"
    units: str = ""
    description: str = ""

class LimitDef(BaseModel):
    param_id: int
    yellow_low: float | None = None
    yellow_high: float | None = None
    red_low: float | None = None
    red_high: float | None = None
    check_interval_s: float = 4.0

class TCFieldDef(BaseModel):
    name: str
    type: str = "uint8"
    description: str = ""

class TCCommandDef(BaseModel):
    name: str
    service: int
    subtype: int
    description: str = ""
    fields: list[TCFieldDef] = Field(default_factory=list)

class EventDefinition(BaseModel):
    """Event catalog entry."""
    id: int
    name: str
    severity: str = "INFO"
    subsystem: str = ""
    description: str = ""

class EventCatalog(BaseModel):
    """Event catalog."""
    events: list[EventDefinition] = Field(default_factory=list)

class PowerLineConfig(BaseModel):
    """Power line definition."""
    name: str
    switchable: bool = True
    default_on: bool = True
    power_w: float = 0.0
    equipment: str = ""

class ScenarioEventConfig(BaseModel):
    time_offset_s: float | None = None
    condition: str | None = None
    action: str
    params: dict[str, Any] = Field(default_factory=dict)

class ScenarioConfig(BaseModel):
    name: str
    difficulty: str = "BASIC"
    duration_s: float = 1800.0
    briefing: str = ""
    events: list[ScenarioEventConfig] = Field(default_factory=list)
    expected_responses: list[dict[str, str]] = Field(default_factory=list)

class DisplayWidgetConfig(BaseModel):
    type: str
    parameter: str | None = None
    parameters: list[str] | None = None
    label: str = ""
    range: list[float] | None = None
    units: str = ""
    limits_ref: str | None = None
    duration_s: float | None = None

class DisplayPageConfig(BaseModel):
    name: str
    widgets: list[DisplayWidgetConfig] = Field(default_factory=list)

class PositionConfig(BaseModel):
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

class MCSDisplayConfig(BaseModel):
    positions: dict[str, PositionConfig] = Field(default_factory=dict)


# ===== Phase 3: Config-Driven Schemas =====

class MemoryRegionConfig(BaseModel):
    """Onboard memory region definition for S6 Memory Management."""
    name: str
    start: int = Field(description="Start address (hex-encoded in YAML)")
    size: int = Field(description="Region size in bytes")
    type: str = Field(default="flash", description="Memory type: readonly, flash, ram")
    description: str = ""


class MemoryMapConfig(BaseModel):
    """Complete onboard memory map."""
    memory_regions: list[MemoryRegionConfig] = Field(default_factory=list)


class PUSStoreConfig(BaseModel):
    """S15 onboard storage store definition."""
    id: int
    name: str
    capacity_bytes: int


class PUSServiceConfig(BaseModel):
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
    stores: list[PUSStoreConfig] | None = None


class PUSServicesConfig(BaseModel):
    """All PUS service configurations."""
    services: dict[str, PUSServiceConfig] = Field(default_factory=dict)


class ProcedureIndexEntry(BaseModel):
    """Procedure catalog entry linking procedures to files and roles."""
    id: str
    name: str
    file: str
    category: str = ""
    required_positions: list[str] = Field(default_factory=list)
    command_services: list[int] = Field(default_factory=list)


class ProcedureIndex(BaseModel):
    """Master index of all procedures."""
    procedures: list[ProcedureIndexEntry] = Field(default_factory=list)


class CommandStepConfig(BaseModel):
    """A single step in an activity command sequence."""
    service: int | None = None
    subtype: int | None = None
    func_id: int | None = None
    store_id: int | None = None
    sid: int | None = None
    description: str = ""
    wait_s: float | None = None
    wait_for: dict[str, Any] | None = None


class ActivityTypeConfig(BaseModel):
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


class ActivityTypesConfig(BaseModel):
    """All activity type definitions."""
    activity_types: list[ActivityTypeConfig] = Field(default_factory=list)
