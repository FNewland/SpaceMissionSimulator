# Orbit Tools - TC Command Generator

A comprehensive Python tool for converting orbital elements (TLEs or Cartesian state vectors) into telecommand sequences for spacecraft command upload.

## Installation

Dependencies:
- `sgp4` - SGP4 orbit propagation (required for TLE mode)
- `numpy` - Numerical computing (required)
- `aiohttp` - Web server (optional, required for --serve mode)

Install with:
```bash
pip install sgp4 numpy aiohttp
```

## Usage

### TLE to Telecommand (--tle mode)

Convert Two-Line Element Set to spacecraft commands:

```bash
python3 orbit_tools.py --tle "1 25544U 98067A   24001.00..." "2 25544  51.6416..." --epoch 2024-01-15T12:00:00
```

Arguments:
- `--tle LINE1 LINE2`: TLE lines
- `--epoch TIMESTAMP`: Propagation epoch (ISO format, required)

Output includes:
- Orbital elements (altitude, inclination, period, etc.)
- Six SET_PARAM telecommand hex sequences (one per orbital state component)

### State Vector to Telecommand (--sv mode)

Convert Cartesian state directly to commands:

```bash
python3 orbit_tools.py --sv 6378000 0 0 0 7600 0 --epoch 2024-01-15T12:00:00
```

Arguments:
- `--sv X Y Z VX VY VZ`: State vector values (meters and m/s)
- `--epoch TIMESTAMP`: Epoch (ISO format, optional)

### Web Interface (--serve mode)

Start an interactive web server:

```bash
python3 orbit_tools.py --serve --port 8093
```

Then open http://localhost:8093 in your browser for a form-based interface.

## Output Format

Telecommand sequences are generated as hex strings suitable for direct MCS command input.

Each command has the structure:
- **Service/Subtype**: `14 01` (SET_PARAM command)
- **Parameter ID**: 2 bytes (big-endian), one of:
  - `0231`: GPS ECEF X position (m)
  - `0232`: GPS ECEF Y position (m)
  - `0233`: GPS ECEF Z position (m)
  - `0234`: GPS ECEF X velocity (m/s)
  - `0235`: GPS ECEF Y velocity (m/s)
  - `0236`: GPS ECEF Z velocity (m/s)
- **Value**: 4 bytes IEEE 754 float32 (big-endian)

Example command output:
```
X Position (m): 3791602.441
  CMD: 140102314A676BCA
```

To execute in MCS, paste the hex string (without CMD: prefix) into the command input field.

## Orbital State Computation

For TLE inputs:
1. SGP4 propagation to specified epoch (in ECI frame, km and km/s)
2. GMST calculation for that epoch
3. ECI to ECEF conversion via rotation matrix
4. Conversion to meters and m/s
5. IEEE 754 float32 encoding in big-endian byte order

For state vector inputs:
1. Assumes input is already in ECEF (meters and m/s)
2. Direct IEEE 754 float32 encoding

## Orbital Elements Summary

The tool automatically computes and displays:
- Semi-major axis (a)
- Eccentricity (e)
- Inclination (i)
- Right Ascension of Ascending Node (RAAN/Ω)
- Argument of Perigee (ω)
- True Anomaly (ν)
- Orbital period
- Perigee and apogee altitudes
- Velocity magnitude

## Examples

### ISS-like LEO orbit

```bash
python3 orbit_tools.py --tle \
  "1 25544U 98067A   24001.00000000  .00016717  00000-0  29667-3 0  9990" \
  "2 25544  51.6416 124.6269 0001884  25.3126 334.8141 15.54084607435286" \
  --epoch 2024-01-15T12:00:00
```

### Equatorial transfer

```bash
python3 orbit_tools.py --sv 6378000 0 0 0 7600 0
```

## Implementation Notes

- ECI to ECEF conversion uses GMST rotation (astropy-free)
- Orbital element computation uses standard astrodynamics formulas
- All angles in degrees for readability
- All distances in meters or km as appropriate
- All velocities in m/s or km/s as appropriate
