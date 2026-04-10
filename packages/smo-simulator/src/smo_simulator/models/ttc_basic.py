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
    tm_data_rate: int = 64000
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
    data_rate_mode: int = 1              # 0=low-rate (1 kbps), 1=high-rate (64 kbps)

    # ── Antenna deployment sensors (DEFECT FIX #4) ──
    antenna_deployment_ready: bool = True  # True if pyro continuity OK
    antenna_deployment_sensor: int = 1     # 0=unknown, 1=stowed, 2=deployed, 3=partial/jammed
    _antenna_deploy_last_time: float = 0.0  # Timestamp of last deploy attempt

    # ── Link quality thresholds for events ──
    _prev_pa_temp_warning: bool = False
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
        self._param_ids: dict[str, int] = {}
        self._eirp = 10.0
        self._gs_gt = 20.0
        self._sc_gain = 3.0
        self._dl_freq_hz = 2200.5e6
        self._min_el = 5.0
        self._tm_rate_hi = 64000
        self._tm_rate_lo = 1000
        self._ul_freq_mhz = 449.0
        self._coding_gain = 3.0  # dB (convolutional + RS)
        self._pa_max_power_w = 5.0
        self._pa_nominal_power_w = 2.0
        self._pa_shutdown_temp = 70.0
        self._pa_tau = 60.0  # PA thermal time constant (seconds)
        self._was_in_contact = False

    @property
    def name(self) -> str:
        return "ttc"

    def configure(self, config: dict[str, Any]) -> None:
        self._eirp = config.get("eirp_dbw", 10.0)
        self._gs_gt = config.get("gs_g_t_db", 20.0)
        self._sc_gain = config.get("sc_gain_dbi", 3.0)
        self._dl_freq_hz = config.get("dl_freq_mhz", 401.5) * 1e6
        self._tm_rate_hi = config.get("tm_rate_hi_bps", 64000)
        self._tm_rate_lo = config.get("tm_rate_lo_bps", 1000)
        self._ul_freq_mhz = config.get("ul_freq_mhz", 449.0)
        self._coding_gain = config.get("coding_gain_db", 3.0)
        self._pa_max_power_w = config.get("pa_max_power_w", 5.0)
        self._pa_nominal_power_w = config.get("pa_nominal_power_w", 2.0)
        self._pa_shutdown_temp = config.get("pa_shutdown_temp_c", 70.0)
        self._pa_tau = config.get("pa_thermal_tau_s", 60.0)
        self._state.tm_data_rate = self._tm_rate_hi
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
        })

    def tick(self, dt: float, orbit_state: Any,
             shared_params: dict[int, float]) -> None:
        s = self._state
        in_contact = orbit_state.in_contact or bool(
            shared_params.get(0x05FF, 0)
        )

        # Transponder RX availability (receiver is on dedicated PDM —
        # independent of TX PA state).  A total transponder failure
        # kills both RX and TX.
        rx_available = True
        if s.primary_failed and s.mode == 0:
            rx_available = False
        if s.redundant_failed and s.mode == 1:
            rx_available = False
        if not rx_available:
            in_contact = False

        # TX capability (PA must be on and not in thermal shutdown).
        # This affects downlink / link budget only — uplink lock
        # acquisition works off the RX chain which is always powered.
        can_transmit = s.pa_on and not s.pa_overheat_shutdown and rx_available
        if can_transmit:
            s.tx_fwd_power = self._pa_nominal_power_w
        else:
            s.tx_fwd_power = 0.0

        # ── Uplink timeout tracking ──
        if in_contact and s.frame_sync:
            s._uplink_timeout_counter = 0.0
        else:
            s._uplink_timeout_counter += dt

        # ── Dedicated command channel timer ──
        if s.cmd_channel_active:
            s.cmd_decode_timer -= dt
            if s.cmd_decode_timer > 0:
                # Override: keep PA on and TX active regardless of OBC state
                s.pa_on = True
                s.tx_fwd_power = self._pa_nominal_power_w
            else:
                # Timer expired
                s.cmd_decode_timer = 0.0
                s.cmd_channel_active = False

        # ── Beacon mode ──
        if s.beacon_mode:
            s.data_rate_mode = 0
            s.tm_data_rate = self._tm_rate_lo

        # ── Data rate selection (antenna deployment) ──
        if not s.antenna_deployed:
            # Pre-deployment: force low-rate
            s.tm_data_rate = self._tm_rate_lo
        elif not s.beacon_mode:
            # Post-deployment, normal mode: use configured high rate
            s.tm_data_rate = self._tm_rate_hi

        # Uplink loss blocks receive
        if s.uplink_lost:
            s.cmd_rx_count = s.cmd_rx_count  # No new commands

        s.link_active = in_contact
        s.elevation_deg = orbit_state.gs_elevation_deg
        s.azimuth_deg = orbit_state.gs_azimuth_deg
        s.range_km = orbit_state.gs_range_km

        # When passes are forced via override but no orbital geometry
        # is available (range_km == 0), use a nominal mid-pass slant
        # range so the link budget produces healthy signal values
        # instead of triggering spurious alarms.
        pass_override = bool(shared_params.get(0x05FF, 0))
        if pass_override and s.range_km <= 0:
            s.range_km = 500.0          # ~500 km nominal mid-pass slant range
            s.elevation_deg = max(s.elevation_deg, 45.0)

        # ── Lock acquisition sequence ──
        if in_contact and s.range_km > 0:
            if not self._was_in_contact:
                # AOS: reset lock sequence
                s._lock_timer = 0.0
                s.carrier_lock = False
                s.bit_sync = False
                s.frame_sync = False

            s._lock_timer += dt

            s.carrier_lock = s._lock_timer >= s._carrier_lock_delay
            s.bit_sync = (
                s.carrier_lock and s._lock_timer >= s._bit_sync_delay
            )
            s.frame_sync = (
                s.bit_sync and s._lock_timer >= s._frame_sync_delay
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
                self._eirp
                + self._sc_gain
                - fspl
                + 30.0
                + random.gauss(0, 0.5)
            )

            noise_bw = 10 * math.log10(s.tm_data_rate)
            # Eb/N0 = EIRP + Gain - FSPL - kTB + coding gain
            noise_floor = -228.6 + self._gs_gt + noise_bw
            snr = s.rssi_dbm - 30 - noise_floor
            s.eb_n0 = (
                snr
                + self._coding_gain
                - s.ber_inject_offset
                - s.receiver_nf_degrade
                + random.gauss(0, 0.2)
            )
            s.link_margin_db = s.eb_n0 - 12.0 + random.gauss(0, 0.2)

            # ── Antenna deployment effect on link margin ──
            if not s.antenna_deployed:
                s.link_margin_db -= 6.0  # Stowed antenna penalty

            # BER from Eb/N0 (simplified BPSK/QPSK)
            eb_n0_linear = 10.0 ** (s.eb_n0 / 10.0)
            if eb_n0_linear > 0:
                # Q-function approximation: BER ~ 0.5 * erfc(sqrt(Eb/N0))
                x = math.sqrt(eb_n0_linear)
                # erfc approximation for large x
                if x > 5.0:
                    ber_val = 1e-12  # Effectively error-free
                else:
                    ber_val = 0.5 * math.erfc(x)
                s.ber = max(-12.0, math.log10(max(ber_val, 1e-12)))
            else:
                s.ber = -1.0  # Very high BER
        else:
            s.rssi_dbm = -120.0 + random.gauss(0, 0.5)
            s.link_margin_db = 0.0
            s.eb_n0 = 0.0
            s.ber = -10.0

        s.ranging_active = in_contact and s.range_km > 0 and s.frame_sync

        # ── AGC, Doppler, Range Rate (Phase 4) ──
        if in_contact and s.range_km > 0:
            # AGC tracks received signal level
            s.agc_level_db = s.rssi_dbm + 60.0 + random.gauss(0, 0.3)

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
            ) + random.gauss(0, 5.0)
            # Doppler: f_d = f_0 * v_r / c
            c = 3e8
            s.doppler_hz = self._dl_freq_hz * s.range_rate_m_s / c
            s.doppler_hz += random.gauss(0, 2.0)

            # Bytes transmitted this pass
            if s.frame_sync:
                s.total_bytes_tx += int(s.tm_data_rate / 8 * dt)
                s.total_bytes_rx += int(1000 * dt)  # ~1kbps uplink
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
            + random.gauss(0, 0.03)
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
            + random.gauss(0, 0.02)
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

        if s.pa_overheat_shutdown:
            events.append({
                'event_id': 0x0509,
                'severity': 'HIGH',
                'description': f"PA overheat shutdown at {s.pa_temp:.1f}C"
            })

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
            # Also queue to engine if available
            if hasattr(self, '_engine') and self._engine:
                try:
                    self._engine.event_queue.put_nowait({
                        'event_id': evt['event_id'],
                        'severity': evt['severity'],
                        'subsystem': 'ttc',
                        'description': evt['description']
                    })
                except:
                    pass

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
            rate = int(cmd.get("rate", self._tm_rate_hi))
            if rate in (self._tm_rate_hi, self._tm_rate_lo):
                self._state.tm_data_rate = rate
                return {"success": True}
            return {"success": False, "message": "Invalid rate"}

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
            level = float(cmd.get("power_w", self._pa_nominal_power_w))
            if 0.0 < level <= self._pa_max_power_w:
                self._pa_nominal_power_w = level
                return {"success": True}
            return {"success": False, "message": "Power out of range"}

        elif command == "cmd_channel_start":
            self._state.cmd_decode_timer = 900.0
            self._state.cmd_channel_active = True
            self._state.pa_on = True
            return {"success": True}

        elif command == "deploy_antennas":
            # DEFECT FIX #4 (ttc.md): Update antenna deployment sensor on command
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
            self._state.beacon_mode = bool(cmd.get("on", True))
            return {"success": True}

        elif command == "set_ul_freq":
            freq_mhz = float(cmd.get("freq_mhz", self._ul_freq_mhz))
            if 440.0 <= freq_mhz <= 460.0:
                self._ul_freq_mhz = freq_mhz
                return {"success": True}
            return {"success": False, "message": "Uplink frequency out of range"}

        elif command == "set_dl_freq":
            freq_mhz = float(cmd.get("freq_mhz", self._dl_freq_hz / 1e6))
            # UHF downlink range 400–410 MHz
            if 400.0 <= freq_mhz <= 410.0:
                self._dl_freq_hz = freq_mhz * 1e6
                return {"success": True}
            return {"success": False, "message": "Downlink frequency out of range (400–410 MHz for UHF)"}

        elif command == "set_modulation":
            mod_mode = int(cmd.get("mode", 0))
            if mod_mode in (0, 1):  # 0=BPSK, 1=QPSK
                # Store modulation mode for future use
                setattr(self._state, "modulation_mode", mod_mode)
                return {"success": True}
            return {"success": False, "message": "Invalid modulation mode"}

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
            # Burn-wire failed: antenna stuck stowed/jammed. Drives the
            # gs_antenna_failure / no_telemetry_at_pass contingency procedures.
            s.antenna_deployed = False
            s.antenna_deployment_ready = False
            s.antenna_deployment_sensor = 3  # partial/jammed

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
            s.antenna_deployment_sensor = 1  # stowed (operator must redeploy)

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self._state)

    def set_state(self, state: dict[str, Any]) -> None:
        for k, v in state.items():
            if hasattr(self._state, k):
                setattr(self._state, k, v)

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
