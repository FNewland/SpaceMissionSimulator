#!/usr/bin/env python3
"""
Orbit Tools: TLE and State Vector to Telecommand (TC) Converter

Converts orbital elements (TLEs or Cartesian state vectors) to TC command
sequences for uploading orbit state to spacecraft. Supports CLI and web interface.

Spacecraft Parameter IDs (SET_PARAM S20.1):
  - 0x0231: GPS ECEF X position (meters)
  - 0x0232: GPS ECEF Y position (meters)
  - 0x0233: GPS ECEF Z position (meters)
  - 0x0234: GPS ECEF X velocity (m/s)
  - 0x0235: GPS ECEF Y velocity (m/s)
  - 0x0236: GPS ECEF Z velocity (m/s)
"""

import argparse
import asyncio
import struct
import math
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Tuple, List, Dict, Optional

try:
    from sgp4.api import Satrec, jday
except ImportError:
    import sys
    print("ERROR: sgp4 library required: pip install sgp4", file=sys.stderr)
    sys.exit(1)

try:
    import aiohttp
    from aiohttp import web
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


# Constants
_DEG = math.pi / 180.0
_RAD = 180.0 / math.pi
_EARTH_RADIUS_KM = 6371.0
_EARTH_FLATTENING = 1.0 / 298.257223563
_AU = 1.495978707e8  # km per AU
_MU_EARTH = 398600.4418  # km^3/s^2


# Telecommand Structure
TC_SERVICE_ID = 20
TC_SUBTYPE_ID = 1
PARAM_IDS = {
    'x_pos': 0x0231,
    'y_pos': 0x0232,
    'z_pos': 0x0233,
    'x_vel': 0x0234,
    'y_vel': 0x0235,
    'z_vel': 0x0236,
}


class OrbitAnalyzer:
    """Compute orbital elements from state vectors."""

    @staticmethod
    def compute_orbital_elements(pos_km: np.ndarray, vel_km_s: np.ndarray,
                                 mu: float = _MU_EARTH) -> Dict[str, float]:
        """
        Compute classical orbital elements from Cartesian state.

        Args:
            pos_km: ECI position [km]
            vel_km_s: ECI velocity [km/s]
            mu: Gravitational parameter [km^3/s^2]

        Returns:
            Dictionary with a, e, i, RAAN, AOP, TA, period
        """
        r = np.linalg.norm(pos_km)
        v = np.linalg.norm(vel_km_s)

        # Semi-major axis
        a = 1.0 / (2.0/r - v*v/mu)

        # Angular momentum
        h = np.cross(pos_km, vel_km_s)
        h_mag = np.linalg.norm(h)

        # Eccentricity
        ecc_vec = ((v*v - mu/r) * pos_km - np.dot(pos_km, vel_km_s) * vel_km_s) / mu
        e = np.linalg.norm(ecc_vec)

        # Inclination
        i = math.acos(np.clip(h[2] / h_mag, -1.0, 1.0))

        # RAAN (Omega)
        n_vec = np.array([-h[1], h[0], 0.0])
        n_mag = np.linalg.norm(n_vec)
        if n_mag > 1e-9:
            raan = math.atan2(n_vec[0], -n_vec[1])
        else:
            raan = 0.0

        # Argument of perigee (omega)
        if n_mag > 1e-9:
            aop = math.atan2(ecc_vec[2], np.dot(n_vec, ecc_vec)) - math.pi/2
        else:
            aop = 0.0

        # True anomaly
        if e > 1e-9:
            ta = math.atan2(np.dot(np.cross(ecc_vec, pos_km), h)/h_mag,
                           np.dot(ecc_vec, pos_km))
        else:
            ta = math.atan2(pos_km[1], pos_km[0])

        # Orbital period
        if a > 0:
            period = 2.0 * math.pi * math.sqrt(a**3 / mu)
        else:
            period = 0.0

        # Altitude at this point
        alt = r - _EARTH_RADIUS_KM

        return {
            'a_km': a,
            'e': e,
            'i_deg': i * _RAD,
            'raan_deg': (raan * _RAD) % 360.0,
            'aop_deg': (aop * _RAD) % 360.0,
            'ta_deg': (ta * _RAD) % 360.0,
            'period_s': period,
            'period_min': period / 60.0,
            'altitude_km': alt,
            'velocity_km_s': v,
        }


class EciEcefConverter:
    """Convert between ECI and ECEF frames."""

    @staticmethod
    def gmst(jd_ut1: float) -> float:
        """Greenwich Mean Sidereal Time in radians."""
        T = (jd_ut1 - 2451545.0) / 36525.0
        gmst_deg = (280.46061837 + 360.98564736629 * (jd_ut1 - 2451545.0)
                    + 0.000387933 * T * T - T * T * T / 38710000.0) % 360.0
        return gmst_deg * _DEG

    @staticmethod
    def eci_to_ecef(r_eci: np.ndarray, gmst: float) -> np.ndarray:
        """
        Convert ECI position/velocity to ECEF using GMST rotation.

        Args:
            r_eci: 3-element vector (position or velocity)
            gmst: Greenwich Mean Sidereal Time in radians

        Returns:
            ECEF vector
        """
        c, s = math.cos(gmst), math.sin(gmst)
        return np.array([
            c * r_eci[0] + s * r_eci[1],
            -s * r_eci[0] + c * r_eci[1],
            r_eci[2]
        ])

    @staticmethod
    def ecef_to_eci(r_ecef: np.ndarray, gmst: float) -> np.ndarray:
        """Convert ECEF position/velocity to ECI."""
        c, s = math.cos(gmst), math.sin(gmst)
        return np.array([
            c * r_ecef[0] - s * r_ecef[1],
            s * r_ecef[0] + c * r_ecef[1],
            r_ecef[2]
        ])


class TelecommandEncoder:
    """Encode orbital state into SET_PARAM telecommand bytes."""

    @staticmethod
    def float32_to_bytes(value: float) -> bytes:
        """Convert Python float to IEEE 754 float32 big-endian bytes."""
        return struct.pack('>f', value)

    @staticmethod
    def param_to_hex_cmd(param_id: int, value: float) -> str:
        """
        Generate a single SET_PARAM command.

        Service 20, Subtype 1: SET_PARAM
        Data format: param_id (2 bytes BE) + value (4 bytes IEEE754 BE)

        Args:
            param_id: Parameter ID (0x0231-0x0236)
            value: Float value

        Returns:
            Hex string without 0x prefix (ready for MCS input)
        """
        # Service and subtype bytes
        service = bytes([TC_SERVICE_ID])
        subtype = bytes([TC_SUBTYPE_ID])

        # Param ID (2 bytes, big-endian)
        param_bytes = struct.pack('>H', param_id)

        # Value (4 bytes, IEEE754, big-endian)
        value_bytes = TelecommandEncoder.float32_to_bytes(value)

        # Full command: service + subtype + param_id + value
        cmd = service + subtype + param_bytes + value_bytes

        return cmd.hex().upper()

    @staticmethod
    def state_to_commands(pos_ecef_m: np.ndarray, vel_ecef_ms: np.ndarray,
                         param_order: List[str] = None) -> Dict[str, str]:
        """
        Convert ECEF state vector to SET_PARAM hex commands.

        Args:
            pos_ecef_m: ECEF position [m] [x, y, z]
            vel_ecef_ms: ECEF velocity [m/s] [vx, vy, vz]
            param_order: Order of parameters (default: x, y, z, vx, vy, vz)

        Returns:
            Dictionary mapping parameter names to hex command strings
        """
        if param_order is None:
            param_order = ['x_pos', 'y_pos', 'z_pos', 'x_vel', 'y_vel', 'z_vel']

        state = {
            'x_pos': pos_ecef_m[0],
            'y_pos': pos_ecef_m[1],
            'z_pos': pos_ecef_m[2],
            'x_vel': vel_ecef_ms[0],
            'y_vel': vel_ecef_ms[1],
            'z_vel': vel_ecef_ms[2],
        }

        commands = {}
        for param_name in param_order:
            value = state[param_name]
            param_id = PARAM_IDS[param_name]
            cmd = TelecommandEncoder.param_to_hex_cmd(param_id, value)
            commands[param_name] = cmd

        return commands


class TleToStateConverter:
    """Convert TLE to state vector at specified epoch."""

    @staticmethod
    def propagate(tle_line1: str, tle_line2: str, epoch_utc: datetime) -> Tuple[np.ndarray, np.ndarray]:
        """
        Propagate TLE to specified epoch.

        Args:
            tle_line1: TLE line 1
            tle_line2: TLE line 2
            epoch_utc: UTC datetime to propagate to

        Returns:
            (pos_km, vel_km_s) in ECI frame
        """
        sat = Satrec.twoline2rv(tle_line1, tle_line2)

        jd, fr = jday(epoch_utc.year, epoch_utc.month, epoch_utc.day,
                      epoch_utc.hour, epoch_utc.minute,
                      epoch_utc.second + epoch_utc.microsecond * 1e-6)

        e, r, v = sat.sgp4(jd, fr)

        if e != 0:
            raise ValueError(f"SGP4 propagation error: {e}")

        return np.array(r), np.array(v)  # km and km/s


class OrbitSummary:
    """Generate human-readable orbit summary."""

    @staticmethod
    def format_summary(elements: Dict[str, float], utc: datetime) -> str:
        """Format orbital elements as readable text."""
        lines = [
            "=== ORBIT SUMMARY ===",
            f"Epoch: {utc.isoformat()} UTC",
            "",
            "CLASSICAL ELEMENTS:",
            f"  Semi-major axis:  {elements['a_km']:.2f} km",
            f"  Eccentricity:     {elements['e']:.6f}",
            f"  Inclination:      {elements['i_deg']:.4f}°",
            f"  RAAN:             {elements['raan_deg']:.4f}°",
            f"  Argument of Peri: {elements['aop_deg']:.4f}°",
            f"  True Anomaly:     {elements['ta_deg']:.4f}°",
            "",
            "DERIVED:",
            f"  Altitude:         {elements['altitude_km']:.2f} km",
            f"  Velocity:         {elements['velocity_km_s']:.4f} km/s",
            f"  Orbital Period:   {elements['period_min']:.2f} min ({elements['period_s']:.0f} s)",
            f"  Perigee:          {elements['a_km'] * (1 - elements['e']) - _EARTH_RADIUS_KM:.2f} km",
            f"  Apogee:           {elements['a_km'] * (1 + elements['e']) - _EARTH_RADIUS_KM:.2f} km",
        ]
        return "\n".join(lines)


# CLI Interface

def cli_tle_mode(args) -> None:
    """Handle --tle command line mode."""
    tle1 = args.tle[0]
    tle2 = args.tle[1]

    try:
        epoch_utc = datetime.fromisoformat(args.epoch)
    except ValueError:
        raise ValueError(f"Invalid epoch format. Use ISO format: 2024-01-15T12:30:00")

    if epoch_utc.tzinfo is None:
        epoch_utc = epoch_utc.replace(tzinfo=timezone.utc)

    print(f"\n[TLE to TC Command Converter]")
    print(f"Epoch: {epoch_utc.isoformat()}")

    # Propagate TLE
    try:
        pos_eci_km, vel_eci_km_s = TleToStateConverter.propagate(tle1, tle2, epoch_utc)
        print(f"TLE propagated successfully")
    except Exception as e:
        print(f"ERROR: Failed to propagate TLE: {e}")
        return

    # Convert ECI to ECEF
    jd = epoch_utc.toordinal() + 1721425.5  # Julian date approximation
    jd_frac = (epoch_utc.hour + epoch_utc.minute/60.0 + epoch_utc.second/3600.0) / 24.0
    gmst = EciEcefConverter.gmst(jd + jd_frac)

    pos_ecef_km = EciEcefConverter.eci_to_ecef(pos_eci_km, gmst)
    vel_ecef_km_s = EciEcefConverter.eci_to_ecef(vel_eci_km_s, gmst)

    # Convert to meters and m/s
    pos_ecef_m = pos_ecef_km * 1000.0
    vel_ecef_ms = vel_ecef_km_s * 1000.0

    # Generate commands
    commands = TelecommandEncoder.state_to_commands(pos_ecef_m, vel_ecef_ms)

    # Compute orbital elements for summary
    elements = OrbitAnalyzer.compute_orbital_elements(pos_eci_km, vel_eci_km_s)
    summary = OrbitSummary.format_summary(elements, epoch_utc)

    # Output
    print(summary)
    print("\n=== TELECOMMAND SEQUENCES ===")
    param_names = ['x_pos', 'y_pos', 'z_pos', 'x_vel', 'y_vel', 'z_vel']
    param_labels = ['X Position', 'Y Position', 'Z Position', 'X Velocity', 'Y Velocity', 'Z Velocity']
    param_units = ['(m)', '(m)', '(m)', '(m/s)', '(m/s)', '(m/s)']

    for name, label, unit in zip(param_names, param_labels, param_units):
        value = {
            'x_pos': pos_ecef_m[0],
            'y_pos': pos_ecef_m[1],
            'z_pos': pos_ecef_m[2],
            'x_vel': vel_ecef_ms[0],
            'y_vel': vel_ecef_ms[1],
            'z_vel': vel_ecef_ms[2],
        }[name]
        cmd = commands[name]
        print(f"{label} {unit}: {value:.3f}")
        print(f"  CMD: {cmd}")

    print("\n[Commands ready to paste into MCS command input]")


def cli_sv_mode(args) -> None:
    """Handle --sv command line mode."""
    try:
        x, y, z, vx, vy, vz = map(float, args.sv)
    except ValueError:
        raise ValueError("State vector must be 6 floats: x y z vx vy vz")

    pos_ecef_m = np.array([x, y, z])
    vel_ecef_ms = np.array([vx, vy, vz])

    # Try to compute epoch from args
    epoch_utc = datetime.now(timezone.utc)
    if hasattr(args, 'epoch') and args.epoch:
        try:
            epoch_utc = datetime.fromisoformat(args.epoch)
            if epoch_utc.tzinfo is None:
                epoch_utc = epoch_utc.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    print(f"\n[State Vector to TC Command Converter]")
    print(f"Epoch: {epoch_utc.isoformat()}")

    # Generate commands
    commands = TelecommandEncoder.state_to_commands(pos_ecef_m, vel_ecef_ms)

    # Compute orbital elements for summary
    pos_eci_km = pos_ecef_m / 1000.0
    vel_eci_km_s = vel_ecef_ms / 1000.0
    elements = OrbitAnalyzer.compute_orbital_elements(pos_eci_km, vel_eci_km_s)
    summary = OrbitSummary.format_summary(elements, epoch_utc)

    print(summary)
    print("\n=== TELECOMMAND SEQUENCES ===")
    param_names = ['x_pos', 'y_pos', 'z_pos', 'x_vel', 'y_vel', 'z_vel']
    param_labels = ['X Position', 'Y Position', 'Z Position', 'X Velocity', 'Y Velocity', 'Z Velocity']
    param_units = ['(m)', '(m)', '(m)', '(m/s)', '(m/s)', '(m/s)']

    values = [x, y, z, vx, vy, vz]
    for name, label, unit, value in zip(param_names, param_labels, param_units, values):
        cmd = commands[name]
        print(f"{label} {unit}: {value:.3f}")
        print(f"  CMD: {cmd}")

    print("\n[Commands ready to paste into MCS command input]")


# Web Server Interface

if HAS_AIOHTTP:
    HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Orbit Tools - TC Command Generator</title>
    <style>
        body {
            font-family: 'Courier New', monospace;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
        }
        h1 { color: #0066cc; margin-bottom: 30px; }
        h2 {
            color: #0066cc;
            margin-top: 30px;
            border-bottom: 2px solid #0066cc;
            padding-bottom: 10px;
        }
        .container {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: #0066cc;
        }
        input, textarea {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            font-size: 13px;
            box-sizing: border-box;
        }
        button {
            background: #0066cc;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            font-weight: bold;
        }
        button:hover {
            background: #0052a3;
        }
        .output {
            background: #f0f0f0;
            border: 1px solid #ddd;
            padding: 15px;
            border-radius: 4px;
            white-space: pre-wrap;
            font-size: 12px;
            max-height: 600px;
            overflow-y: auto;
        }
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            border-bottom: 2px solid #ddd;
        }
        .tab-button {
            padding: 10px 15px;
            border: none;
            background: none;
            cursor: pointer;
            font-size: 14px;
            color: #666;
            border-bottom: 3px solid transparent;
            margin-bottom: -2px;
        }
        .tab-button.active {
            color: #0066cc;
            border-bottom-color: #0066cc;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .error {
            color: #cc0000;
            background: #ffe0e0;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 15px;
        }
        .success {
            color: #008000;
            background: #e0ffe0;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 15px;
        }
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }
        @media (max-width: 900px) {
            .grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <h1>Orbit Tools - Telecommand Command Generator</h1>

    <div class="tabs">
        <button class="tab-button active" onclick="switchTab('tle')">TLE to TC</button>
        <button class="tab-button" onclick="switchTab('sv')">State Vector to TC</button>
    </div>

    <div id="tle" class="tab-content active">
        <div class="container">
            <h2>TLE to Telecommand</h2>
            <div class="form-group">
                <label>TLE Line 1</label>
                <textarea id="tle1" rows="2" placeholder="1 25544U 98067A   24001.00000000  .00016717  00000-0  29667-3 0  9990"></textarea>
            </div>
            <div class="form-group">
                <label>TLE Line 2</label>
                <textarea id="tle2" rows="2" placeholder="2 25544  51.6416 124.6269 0001884  25.3126 334.8141 15.54084607435286"></textarea>
            </div>
            <div class="form-group">
                <label>Epoch (ISO format, UTC)</label>
                <input type="text" id="tle_epoch" placeholder="2024-01-15T12:30:00" value="">
            </div>
            <button onclick="convertTLE()">Convert TLE to Commands</button>
            <div id="tle_output" style="margin-top: 20px;"></div>
        </div>
    </div>

    <div id="sv" class="tab-content">
        <div class="container">
            <h2>State Vector to Telecommand</h2>
            <div class="grid">
                <div>
                    <h3>Position (ECEF, meters)</h3>
                    <div class="form-group">
                        <label>X (m)</label>
                        <input type="number" id="sv_x" step="any" placeholder="6378000">
                    </div>
                    <div class="form-group">
                        <label>Y (m)</label>
                        <input type="number" id="sv_y" step="any" placeholder="0">
                    </div>
                    <div class="form-group">
                        <label>Z (m)</label>
                        <input type="number" id="sv_z" step="any" placeholder="0">
                    </div>
                </div>
                <div>
                    <h3>Velocity (ECEF, m/s)</h3>
                    <div class="form-group">
                        <label>VX (m/s)</label>
                        <input type="number" id="sv_vx" step="any" placeholder="0">
                    </div>
                    <div class="form-group">
                        <label>VY (m/s)</label>
                        <input type="number" id="sv_vy" step="any" placeholder="7600">
                    </div>
                    <div class="form-group">
                        <label>VZ (m/s)</label>
                        <input type="number" id="sv_vz" step="any" placeholder="0">
                    </div>
                </div>
            </div>
            <div class="form-group">
                <label>Epoch (ISO format, UTC) - Optional</label>
                <input type="text" id="sv_epoch" placeholder="2024-01-15T12:30:00" value="">
            </div>
            <button onclick="convertSV()">Convert State Vector to Commands</button>
            <div id="sv_output" style="margin-top: 20px;"></div>
        </div>
    </div>

    <script>
        function switchTab(tab) {
            document.querySelectorAll('.tab-content').forEach(e => e.classList.remove('active'));
            document.querySelectorAll('.tab-button').forEach(e => e.classList.remove('active'));
            document.getElementById(tab).classList.add('active');
            document.querySelector(`button[onclick="switchTab('${tab}')"]`).classList.add('active');
        }

        async function convertTLE() {
            const output = document.getElementById('tle_output');
            output.innerHTML = '<div class="success">Processing...</div>';

            const tle1 = document.getElementById('tle1').value.trim();
            const tle2 = document.getElementById('tle2').value.trim();
            const epoch = document.getElementById('tle_epoch').value.trim();

            if (!tle1 || !tle2) {
                output.innerHTML = '<div class="error">Please provide both TLE lines</div>';
                return;
            }

            try {
                const response = await fetch('/api/tle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tle1, tle2, epoch: epoch || new Date().toISOString().split('.')[0] })
                });

                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const data = await response.json();
                if (data.error) {
                    output.innerHTML = `<div class="error">${data.error}</div>`;
                } else {
                    let html = '<div class="success">Success!</div>';
                    html += `<div class="output">${escapeHtml(data.summary)}</div>`;
                    html += '<h3 style="margin-top: 20px;">Telecommand Sequences</h3>';
                    for (const [param, cmd] of Object.entries(data.commands)) {
                        const label = {
                            'x_pos': 'X Position (m)',
                            'y_pos': 'Y Position (m)',
                            'z_pos': 'Z Position (m)',
                            'x_vel': 'X Velocity (m/s)',
                            'y_vel': 'Y Velocity (m/s)',
                            'z_vel': 'Z Velocity (m/s)',
                        }[param] || param;
                        html += `<div style="margin-top: 15px;"><strong>${label}</strong><br><code>${cmd}</code></div>`;
                    }
                    output.innerHTML = html;
                }
            } catch (e) {
                output.innerHTML = `<div class="error">Error: ${e.message}</div>`;
            }
        }

        async function convertSV() {
            const output = document.getElementById('sv_output');
            output.innerHTML = '<div class="success">Processing...</div>';

            const x = parseFloat(document.getElementById('sv_x').value);
            const y = parseFloat(document.getElementById('sv_y').value);
            const z = parseFloat(document.getElementById('sv_z').value);
            const vx = parseFloat(document.getElementById('sv_vx').value);
            const vy = parseFloat(document.getElementById('sv_vy').value);
            const vz = parseFloat(document.getElementById('sv_vz').value);
            const epoch = document.getElementById('sv_epoch').value.trim();

            if (isNaN(x) || isNaN(y) || isNaN(z) || isNaN(vx) || isNaN(vy) || isNaN(vz)) {
                output.innerHTML = '<div class="error">Please provide all 6 state vector components</div>';
                return;
            }

            try {
                const response = await fetch('/api/sv', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ x, y, z, vx, vy, vz, epoch: epoch || new Date().toISOString().split('.')[0] })
                });

                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const data = await response.json();
                if (data.error) {
                    output.innerHTML = `<div class="error">${data.error}</div>`;
                } else {
                    let html = '<div class="success">Success!</div>';
                    html += `<div class="output">${escapeHtml(data.summary)}</div>`;
                    html += '<h3 style="margin-top: 20px;">Telecommand Sequences</h3>';
                    for (const [param, cmd] of Object.entries(data.commands)) {
                        const label = {
                            'x_pos': 'X Position (m)',
                            'y_pos': 'Y Position (m)',
                            'z_pos': 'Z Position (m)',
                            'x_vel': 'X Velocity (m/s)',
                            'y_vel': 'Y Velocity (m/s)',
                            'z_vel': 'Z Velocity (m/s)',
                        }[param] || param;
                        html += `<div style="margin-top: 15px;"><strong>${label}</strong><br><code>${cmd}</code></div>`;
                    }
                    output.innerHTML = html;
                }
            } catch (e) {
                output.innerHTML = `<div class="error">Error: ${e.message}</div>`;
            }
        }

        function escapeHtml(text) {
            const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
            return text.replace(/[&<>"']/g, m => map[m]);
        }

        // Set default epoch to now
        const now = new Date().toISOString().split('T')[0] + 'T12:00:00';
        document.getElementById('tle_epoch').value = now;
        document.getElementById('sv_epoch').value = now;
    </script>
</body>
</html>
"""

    async def handle_index(request):
        """Serve the web interface."""
        return web.Response(text=HTML_TEMPLATE, content_type='text/html')

    async def handle_tle_api(request):
        """API endpoint for TLE conversion."""
        try:
            data = await request.json()
            tle1 = data.get('tle1', '').strip()
            tle2 = data.get('tle2', '').strip()
            epoch_str = data.get('epoch', datetime.now(timezone.utc).isoformat())

            if not tle1 or not tle2:
                return web.json_response({'error': 'Missing TLE lines'}, status=400)

            try:
                epoch_utc = datetime.fromisoformat(epoch_str.replace('Z', '+00:00'))
            except ValueError:
                return web.json_response({'error': f'Invalid epoch format: {epoch_str}'}, status=400)

            if epoch_utc.tzinfo is None:
                epoch_utc = epoch_utc.replace(tzinfo=timezone.utc)

            # Propagate TLE
            try:
                pos_eci_km, vel_eci_km_s = TleToStateConverter.propagate(tle1, tle2, epoch_utc)
            except Exception as e:
                return web.json_response({'error': f'TLE propagation failed: {str(e)}'}, status=400)

            # Convert ECI to ECEF
            jd = epoch_utc.toordinal() + 1721425.5
            jd_frac = (epoch_utc.hour + epoch_utc.minute/60.0 + epoch_utc.second/3600.0) / 24.0
            gmst = EciEcefConverter.gmst(jd + jd_frac)

            pos_ecef_km = EciEcefConverter.eci_to_ecef(pos_eci_km, gmst)
            vel_ecef_km_s = EciEcefConverter.eci_to_ecef(vel_eci_km_s, gmst)

            pos_ecef_m = pos_ecef_km * 1000.0
            vel_ecef_ms = vel_ecef_km_s * 1000.0

            # Generate commands
            commands = TelecommandEncoder.state_to_commands(pos_ecef_m, vel_ecef_ms)

            # Compute orbital elements
            elements = OrbitAnalyzer.compute_orbital_elements(pos_eci_km, vel_eci_km_s)
            summary = OrbitSummary.format_summary(elements, epoch_utc)

            return web.json_response({
                'summary': summary,
                'commands': commands,
                'elements': elements
            })

        except Exception as e:
            return web.json_response({'error': f'Internal error: {str(e)}'}, status=500)

    async def handle_sv_api(request):
        """API endpoint for state vector conversion."""
        try:
            data = await request.json()
            x = float(data.get('x', 0))
            y = float(data.get('y', 0))
            z = float(data.get('z', 0))
            vx = float(data.get('vx', 0))
            vy = float(data.get('vy', 0))
            vz = float(data.get('vz', 0))
            epoch_str = data.get('epoch', datetime.now(timezone.utc).isoformat())

            try:
                epoch_utc = datetime.fromisoformat(epoch_str.replace('Z', '+00:00'))
            except ValueError:
                return web.json_response({'error': f'Invalid epoch format: {epoch_str}'}, status=400)

            if epoch_utc.tzinfo is None:
                epoch_utc = epoch_utc.replace(tzinfo=timezone.utc)

            pos_ecef_m = np.array([x, y, z])
            vel_ecef_ms = np.array([vx, vy, vz])

            # Generate commands
            commands = TelecommandEncoder.state_to_commands(pos_ecef_m, vel_ecef_ms)

            # Compute orbital elements
            pos_eci_km = pos_ecef_m / 1000.0
            vel_eci_km_s = vel_ecef_ms / 1000.0
            elements = OrbitAnalyzer.compute_orbital_elements(pos_eci_km, vel_eci_km_s)
            summary = OrbitSummary.format_summary(elements, epoch_utc)

            return web.json_response({
                'summary': summary,
                'commands': commands,
                'elements': elements
            })

        except Exception as e:
            return web.json_response({'error': f'Internal error: {str(e)}'}, status=500)

    async def start_web_server(port: int = 8093) -> None:
        """Start the aiohttp web server."""
        app = web.Application()
        app.router.add_get('/', handle_index)
        app.router.add_post('/api/tle', handle_tle_api)
        app.router.add_post('/api/sv', handle_sv_api)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()

        print(f"\nWeb server started at http://localhost:{port}")
        print("Press Ctrl+C to stop\n")

        # Keep server running
        try:
            await asyncio.sleep(3600 * 24)  # Run for 24 hours max
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description='Orbit Tools: TLE and State Vector to Telecommand Converter',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert TLE at a specific epoch
  %(prog)s --tle "1 25544U..." "2 25544 51.64..." --epoch 2024-01-15T12:30:00

  # Convert state vector to commands
  %(prog)s --sv 6378000 0 0 0 7600 0

  # Start web interface
  %(prog)s --serve
        """
    )

    # Use mutually exclusive groups instead of subparsers
    group = parser.add_mutually_exclusive_group(required=True)

    group.add_argument('--tle', nargs=2, metavar=('LINE1', 'LINE2'),
                       help='TLE line 1 and line 2')
    group.add_argument('--sv', nargs=6, type=float, metavar=('X', 'Y', 'Z', 'VX', 'VY', 'VZ'),
                       help='State vector: x y z vx vy vz (in meters and m/s)')
    group.add_argument('--serve', action='store_true',
                       help='Start web interface')

    parser.add_argument('--epoch', default=None,
                        help='Epoch (ISO format: 2024-01-15T12:30:00). Default: current time')
    parser.add_argument('--port', type=int, default=8093,
                        help='Port for web server (default 8093)')

    args = parser.parse_args()

    if args.tle:
        tle_args = argparse.Namespace(tle=args.tle, epoch=args.epoch)
        cli_tle_mode(tle_args)
    elif args.sv:
        sv_args = argparse.Namespace(sv=args.sv, epoch=args.epoch)
        cli_sv_mode(sv_args)
    elif args.serve:
        if not HAS_AIOHTTP:
            print("ERROR: aiohttp required for --serve mode")
            print("Install with: pip install aiohttp")
            return

        import asyncio
        asyncio.run(start_web_server(args.port))


if __name__ == '__main__':
    main()
