"""
EO Mission Simulator — Mission Configuration
Central constants for orbit, ground station, spacecraft, PUS services.
"""
from datetime import datetime

# ---------------------------------------------------------------------------
# Mission identity
# ---------------------------------------------------------------------------
MISSION_NAME      = "EOSAT-1"
SPACECRAFT_APID   = 0x01          # Primary APID for all TM packets
PUS_VERSION       = 2             # PUS-C

# ---------------------------------------------------------------------------
# Simulation server ports
# ---------------------------------------------------------------------------
TC_PORT         = 8001
TM_PORT         = 8002
INSTR_PORT      = 8003
SIM_TICK_HZ     = 1.0             # Simulation time steps per real second (default 1 Hz)
TM_MAX_CLIENTS  = 8

# ---------------------------------------------------------------------------
# Orbit — Sun-Synchronous LEO
# TLE: ~500 km, 97.4° inclination (representative SSO)
# ---------------------------------------------------------------------------
TLE_LINE1 = "1 99001U 26001A   26068.50000000  .00000100  00000-0  10000-4 0  9990"
TLE_LINE2 = "2 99001  97.4000 120.0000 0001200  90.0000 270.0000 15.15000000 00010"

ORBIT_ALTITUDE_KM   = 500.0
ORBIT_INCLINATION   = 97.4        # degrees
EARTH_RADIUS_KM     = 6371.0
GS_MIN_ELEVATION    = 5.0         # degrees — minimum elevation for contact

# Ground station: Svalbard SvalSat (KSAT), Norway
GS_NAME         = "Svalbard"
GS_LAT_DEG      = 78.229         # °N
GS_LON_DEG      = 15.407         # °E
GS_ALT_KM       = 0.458          # km ASL

# CCSDS/PUS time epoch: J2000.0 = 2000-01-01T12:00:00 UTC
TIME_EPOCH = datetime(2000, 1, 1, 12, 0, 0)

# ---------------------------------------------------------------------------
# PUS Housekeeping Structure IDs
# ---------------------------------------------------------------------------
HK_SID_EPS      = 1
HK_SID_AOCS     = 2
HK_SID_TCS      = 3
HK_SID_PLATFORM = 4
HK_SID_PAYLOAD  = 5
HK_SID_TTC      = 6

# Default collection intervals (seconds)
HK_INTERVAL_EPS      = 4
HK_INTERVAL_AOCS     = 4
HK_INTERVAL_TCS      = 8
HK_INTERVAL_PLATFORM = 8
HK_INTERVAL_PAYLOAD  = 8
HK_INTERVAL_TTC      = 8

# ---------------------------------------------------------------------------
# PUS Parameter IDs  (match architecture document §3.4)
# ---------------------------------------------------------------------------
# EPS  0x0100–0x010F
P_BAT_VOLTAGE       = 0x0100
P_BAT_SOC           = 0x0101
P_BAT_TEMP          = 0x0102
P_SA_A_CURRENT      = 0x0103
P_SA_B_CURRENT      = 0x0104
P_BUS_VOLTAGE       = 0x0105
P_POWER_CONS        = 0x0106
P_POWER_GEN         = 0x0107
P_ECLIPSE_FLAG      = 0x0108
P_BAT_CURRENT       = 0x0109
P_BAT_CAPACITY_WH   = 0x010A

# AOCS  0x0200–0x021F
P_ATT_Q1            = 0x0200
P_ATT_Q2            = 0x0201
P_ATT_Q3            = 0x0202
P_ATT_Q4            = 0x0203
P_RATE_ROLL         = 0x0204
P_RATE_PITCH        = 0x0205
P_RATE_YAW          = 0x0206
P_RW1_SPEED         = 0x0207
P_RW2_SPEED         = 0x0208
P_RW3_SPEED         = 0x0209
P_RW4_SPEED         = 0x020A
P_MAG_X             = 0x020B
P_MAG_Y             = 0x020C
P_MAG_Z             = 0x020D
P_AOCS_MODE         = 0x020F
P_GPS_LAT           = 0x0210
P_GPS_LON           = 0x0211
P_GPS_ALT           = 0x0212
P_GPS_VX            = 0x0213
P_GPS_VY            = 0x0214
P_GPS_VZ            = 0x0215
P_SOLAR_BETA        = 0x0216
P_ATT_ERROR         = 0x0217
P_RW1_TEMP          = 0x0218
P_RW2_TEMP          = 0x0219
P_RW3_TEMP          = 0x021A
P_RW4_TEMP          = 0x021B

# OBDH  0x0300–0x030F
P_OBC_MODE          = 0x0300
P_OBC_TEMP          = 0x0301
P_OBC_CPU_LOAD      = 0x0302
P_MMM_USED_PCT      = 0x0303
P_TC_RX_COUNT       = 0x0304
P_TC_ACC_COUNT      = 0x0305
P_TC_REJ_COUNT      = 0x0306
P_TM_PKT_COUNT      = 0x0307
P_UPTIME_S          = 0x0308
P_OBC_TIME_CUC      = 0x0309
P_REBOOT_COUNT      = 0x030A
P_SW_VERSION        = 0x030B

# TCS  0x0400–0x040F
P_TEMP_PANEL_PX     = 0x0400
P_TEMP_PANEL_MX     = 0x0401
P_TEMP_PANEL_PY     = 0x0402
P_TEMP_PANEL_MY     = 0x0403
P_TEMP_PANEL_PZ     = 0x0404
P_TEMP_PANEL_MZ     = 0x0405
P_TEMP_OBC          = 0x0406
P_TEMP_BATTERY      = 0x0407
P_TEMP_FPA          = 0x0408
P_TEMP_THRUSTER     = 0x0409
P_HTR_BATTERY       = 0x040A
P_HTR_OBC           = 0x040B
P_COOLER_FPA        = 0x040C
P_HTR_THRUSTER      = 0x040D

# TT&C  0x0500–0x050F
P_TTC_MODE          = 0x0500
P_LINK_STATUS       = 0x0501
P_RSSI              = 0x0502
P_LINK_MARGIN       = 0x0503
P_UL_FREQ           = 0x0504
P_DL_FREQ           = 0x0505
P_TM_DATA_RATE      = 0x0506
P_XPDR_TEMP         = 0x0507
P_RANGING_STATUS    = 0x0508
P_RANGE_KM          = 0x0509
P_CONTACT_ELEVATION = 0x050A
P_CONTACT_AZ        = 0x050B

# Payload  0x0600–0x060F
P_PLI_MODE          = 0x0600
P_FPA_TEMP          = 0x0601
P_COOLER_PWR        = 0x0602
P_IMAGER_TEMP       = 0x0603
P_STORE_USED_PCT    = 0x0604
P_IMAGE_COUNT       = 0x0605
P_SCENE_ID          = 0x0606
P_LINE_RATE         = 0x0607
P_PLI_DATA_RATE     = 0x0608
P_CHECKSUM_ERRORS   = 0x0609

# Propulsion  0x0700–0x070F
P_PROP_TANK_PRES    = 0x0700
P_PROP_FUEL_MASS    = 0x0701
P_THRUSTER_TEMP     = 0x0702
P_DELTA_V_TOTAL     = 0x0703
P_PROP_MODE         = 0x0704

# ---------------------------------------------------------------------------
# EPS physical constants
# ---------------------------------------------------------------------------
BATTERY_CAPACITY_WH     = 120.0   # Wh
BATTERY_NOMINAL_VOLTAGE  = 26.4   # V  (full charge ~29 V, empty ~22 V)
SA_PEAK_POWER_W         = 252.0   # W  (both wings, equinox)
PLATFORM_IDLE_POWER_W   = 95.0    # W  (bus + AOCS + OBDH + TCS heaters, no payload)
PAYLOAD_POWER_W         = 45.0    # W  (payload in imaging)
PAYLOAD_STANDBY_POWER_W = 8.0     # W
FPA_COOLER_POWER_W      = 15.0    # W
TRANSPONDER_POWER_W     = 20.0    # W  (TX on)
TRANSPONDER_RX_POWER_W  = 5.0     # W  (RX standby)

# Battery SoC → OCV curve (V), simple linear approximation
BAT_SOC_100_V   = 29.2
BAT_SOC_0_V     = 21.5
BAT_INTERNAL_R  = 0.05            # Ω  approximate internal resistance

# ---------------------------------------------------------------------------
# AOCS physical constants
# ---------------------------------------------------------------------------
RW_MAX_SPEED_RPM        = 5500
RW_NOMINAL_SPEED_RPM    = 1200
RW_DESATURATION_SPD     = 200
RW_ANGULAR_MOMENTUM_MAX = 0.12    # Nms
ATTITUDE_ERROR_DEADBAND = 0.01    # degrees

# AOCS modes
AOCS_MODE_NOMINAL     = 0
AOCS_MODE_DETUMBLE    = 1
AOCS_MODE_SAFE        = 2
AOCS_MODE_WHEEL_DESAT = 3
AOCS_MODE_SLEW        = 4

# ---------------------------------------------------------------------------
# OBC modes
# ---------------------------------------------------------------------------
OBC_MODE_NOMINAL      = 0
OBC_MODE_SAFE         = 1
OBC_MODE_EMERGENCY    = 2

# ---------------------------------------------------------------------------
# Spacecraft modes (top-level FDIR state)
# ---------------------------------------------------------------------------
SC_MODE_NOMINAL       = 0
SC_MODE_SAFE          = 1
SC_MODE_EMERGENCY     = 2

# ---------------------------------------------------------------------------
# TT&C constants
# ---------------------------------------------------------------------------
TRANSPONDER_UL_FREQ_MHZ = 2025.5
TRANSPONDER_DL_FREQ_MHZ = 2200.5
TM_RATE_HI_BPS          = 64000
TM_RATE_LO_BPS          = 1000

# ---------------------------------------------------------------------------
# S12 Monitoring — default limit definitions
# Each entry: (param_id, yellow_low, yellow_high, red_low, red_high, check_interval_s)
# ---------------------------------------------------------------------------
DEFAULT_LIMITS = [
    # EPS
    (P_BAT_SOC,        25.0, 95.0,  15.0, 100.0, 4),
    (P_BAT_VOLTAGE,    23.0, 29.0,  22.0,  29.5, 4),
    (P_BAT_TEMP,        2.0, 40.0,   0.0,  45.0, 8),
    (P_BUS_VOLTAGE,    27.0, 29.0,  26.5,  29.5, 4),
    (P_SA_A_CURRENT,    0.0,  9.0,  -0.5,   9.5, 4),
    (P_SA_B_CURRENT,    0.0,  9.0,  -0.5,   9.5, 4),
    # AOCS
    (P_ATT_ERROR,      -1.0,  1.0,  -2.0,   2.0, 4),
    (P_RATE_ROLL,      -0.5,  0.5,  -2.0,   2.0, 1),
    (P_RATE_PITCH,     -0.5,  0.5,  -2.0,   2.0, 1),
    (P_RATE_YAW,       -0.5,  0.5,  -2.0,   2.0, 1),
    (P_RW1_SPEED,    -5000, 5000, -5500,  5500, 4),
    (P_RW2_SPEED,    -5000, 5000, -5500,  5500, 4),
    (P_RW3_SPEED,    -5000, 5000, -5500,  5500, 4),
    (P_RW4_SPEED,    -5000, 5000, -5500,  5500, 4),
    (P_RW1_TEMP,        0.0, 60.0,  -5.0,  70.0, 8),
    (P_RW2_TEMP,        0.0, 60.0,  -5.0,  70.0, 8),
    (P_RW3_TEMP,        0.0, 60.0,  -5.0,  70.0, 8),
    (P_RW4_TEMP,        0.0, 60.0,  -5.0,  70.0, 8),
    # TCS
    (P_TEMP_OBC,        5.0, 60.0,   0.0,  70.0, 8),
    (P_TEMP_BATTERY,    2.0, 40.0,   0.0,  45.0, 8),
    (P_TEMP_FPA,      -18.0,  8.0, -20.0,  12.0, 8),
    (P_TEMP_PANEL_PX, -25.0, 70.0, -30.0,  80.0, 8),
    (P_TEMP_PANEL_PY, -25.0, 65.0, -30.0,  75.0, 8),
    # OBDH
    (P_OBC_CPU_LOAD,    0.0, 85.0,   0.0,  98.0, 8),
    (P_MMM_USED_PCT,    0.0, 90.0,   0.0,  98.0, 60),
    (P_TC_REJ_COUNT,    0.0,  5.0,   0.0,  20.0, 30),
    (P_REBOOT_COUNT,    0.0,  2.0,   0.0,   5.0, 60),
    # Payload
    (P_FPA_TEMP,      -18.0,  8.0, -20.0,  12.0, 8),
    (P_CHECKSUM_ERRORS, 0.0,  3.0,   0.0,   8.0, 60),
]
