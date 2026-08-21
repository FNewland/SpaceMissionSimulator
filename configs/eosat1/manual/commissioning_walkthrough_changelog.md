# Commissioning walkthrough — development change log

**Manual-Scope:** system — a record of defects found and fixed while building the training system. Not part of the flight operations manual.

Excised from `commissioning_sequence_walkthrough.md` on 2026-08-21: an operations manual describes the spacecraft, not the history of the tool used to rehearse it.

---

## Known Simulator Issues

All previously reported issues have been resolved:

**Resolved (Session 1):**
- ~~Antenna deploy in bootloader~~ — func_id 69 added to bootloader allowlist
- ~~Thruster references on TCS screen~~ — removed from `index.html`, `displays.yaml`, `tcs.yaml`, and `hk_structures.yaml`
- **Bootloader power gate bypass** — S20 param get/set and S8 commands now skip the EPS power-state gate during bootloader phase
- **TTC RX/TX separation** — PA off no longer kills uplink lock acquisition; RX and TX paths are independent
- **Three-state link_status** — 0x0501 now reports 0 (NO_LINK), 1 (ACQUIRING), or 2 (LOCKED)
- **Payload image_count** — initialised to 0 (was incorrectly 12); FPA cooler requires ~200s cooldown

**Resolved (Session 2 — MCS display and architecture):**
- **Field name mapping** — `_UI_KEY_MAP` in `server.py` translates YAML short_keys to UI field names (e.g., `bat_soc` → `soc_pct`, `rssi` → `rssi_dbm`)
- **Antenna label** — TTC schematic shows "VHF/UHF" (was "PATCH")
- **Antenna deployed on-demand TM** — parameter 0x0520 now marked `on_demand: true` for S3 requests
- **Event ID conflict** — 0x050E no longer reused for antenna recovery; new event 0x0512 (ANTENNA_DEPLOY_READY_RECOVERED) created
- **AOCS equipment status** — magnetometer (0x025C), gyroscope (0x025D), magnetorquer (0x025E) enabled flags now in HK SID 2; UI shows grey/off when unpowered (was hardcoded green)
- **MCS architecture** — MCS does not poll the simulator; orbit from TLE propagation, ground time configurable via `--sim-epoch`

**Resolved (Session 3 — commissioning walkthrough fixes):**
- **SC TIME clock** — now reads from OBC CUC time telemetry (0x0309) instead of ground clock; shows "NOT SET" before S9.1, updates after SET_TIME
- **OBC time in HK** — parameter 0x0309 (obc_time) added to parameters.yaml and HK SID 4
- **Battery heater EPS gating** — thermostat only operates when EPS power line 5 (`htr_bat`, 0x0115) is ON
- **YPR data in AOCS OFF** — body rates and attitude error now report 0 when AOCS mode is OFF (was leaking internal state)
- **Wheel tick in OFF mode** — reaction wheels now tick in all modes (friction, decay) even when AOCS mode is OFF, allowing pre-DETUMBLE wheel health verification
- **GPS time sync** — new command `OBC_GPS_TIME_SYNC` (func_id 80) synchronises onboard clock with GPS; time jumps > 5s trigger AOCS SAFE_BOOT transition
- **Arbitrary time set UI** — TIME CORRELATION panel now has free-form time input with CUC conversion preview
- **Delayed TM plots** — delayed TM viewer now includes interactive Chart.js time-series plots with zoom/pan capability
- **TLE/orbit tools** — new `tools/orbit_tools.py` converts TLE or state vector to S20.1 command hex format (CLI + web UI on port 8093)

---
