# PROC-NOM-002: Imaging Session Execution
**Subsystem:** PAYLOAD / AOCS / TCS
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Execute a planned Earth-observation imaging session. This procedure transitions the
payload from STANDBY to IMAGING mode, monitors image acquisition, and returns the
payload to STANDBY upon completion. Fine pointing performance and focal plane
thermal conditions must be confirmed before imaging begins.

## Prerequisites
- [ ] PROC-NOM-001 Pass Startup completed with all-GO declaration
- [ ] Payload in STANDBY mode (`payload.mode` (0x0600) = 1)
- [ ] Target scene parameters uploaded to onboard scheduler
- [ ] AOCS in NADIR_POINT mode (`aocs.mode` (0x020F) = 0)
- [ ] EPS battery SoC > 50 % (`eps.bat_soc` (0x0101) > 50)
- [ ] Imaging window confirmed by flight dynamics (target in view)

## Procedure Steps

### Step 1 --- Verify Fine Pointing Performance
**TC:** `HK_REQUEST` SID=2 (Service 3, Subtype 25) --- request AOCS housekeeping
**Verify:** `aocs.mode` (0x020F) = 0 (NADIR_POINT) within 5 s
**Verify:** `aocs.att_error` (0x0217) < 0.1 deg within 5 s
**Verify:** `aocs.rate_roll` (0x0204) < 0.01 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.01 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.01 deg/s
**GO/NO-GO:** If att_error >= 0.1 deg, HOLD. Wait up to 60 s for convergence.
If still not met, abort imaging and investigate AOCS performance.

### Step 2 --- Verify Focal Plane Thermal Condition
**TC:** `HK_REQUEST` SID=3 (Service 3, Subtype 25) --- request TCS housekeeping
**Verify:** `tcs.temp_fpa` (0x0408) < -10.0 C within 5 s
**Verify:** `tcs.cooler_fpa` (0x040C) = 1 (ACTIVE)
**GO/NO-GO:** If FPA temp >= -10.0 C, HOLD. Monitor trend for up to 120 s. If
temperature is not decreasing, check cooler status and abort if necessary.

### Step 3 --- Verify Storage Capacity
**TC:** `HK_REQUEST` SID=6 (Service 3, Subtype 25) --- request Payload housekeeping
**Verify:** `payload.store_used` (0x0604) < 90 % within 5 s
**Note:** Record current `payload.image_count` (0x0605) as baseline value N0.
**GO/NO-GO:** If storage >= 90 %, abort imaging. Schedule data downlink first
(PROC-NOM-003) to free storage before retrying.

### Step 4 --- Command Imaging Mode
**TC:** `PAYLOAD_SET_MODE` mode=2 (Service 8, Subtype 1) --- command IMAGING
**Verify:** `payload.mode` (0x0600) = 2 (IMAGING) within 15 s
**Verify:** `eps.power_cons` (0x0106) increase observed (payload power draw)
**Note:** Imaging session timer starts. Nominal session duration per pass plan.

### Step 5 --- Monitor Image Acquisition
**TC:** `HK_REQUEST` SID=6 (Service 3, Subtype 25) --- poll every 30 s
**Verify:** `payload.image_count` (0x0605) incrementing (current > N0)
**Verify:** `payload.store_used` (0x0604) < 95 % throughout session
**Verify:** `aocs.att_error` (0x0217) remains < 0.1 deg throughout session
**Verify:** `tcs.temp_fpa` (0x0408) remains < -10.0 C throughout session
**Action:** If `payload.store_used` >= 95 %, immediately proceed to Step 6.
**Action:** If `aocs.att_error` >= 0.5 deg, immediately proceed to Step 6.

### Step 6 --- Terminate Imaging Session
**TC:** `PAYLOAD_SET_MODE` mode=1 (Service 8, Subtype 1) --- command STANDBY
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 15 s
**Verify:** `eps.power_cons` (0x0106) decrease observed (payload powered down)
**Note:** Record final `payload.image_count` (0x0605) as Nf. Images acquired = Nf - N0.

### Step 7 --- Post-Imaging Assessment
**TC:** `HK_REQUEST` SID=6 (Service 3, Subtype 25) --- final payload HK
**Verify:** `payload.store_used` (0x0604) < 95 %
**Verify:** `payload.image_count` (0x0605) = Nf (no unexpected increment)
**Log:** Record images acquired, storage delta, and session duration in pass log.

## Off-Nominal Handling
- If `payload.mode` does not transition to IMAGING within 15 s: Re-send command
  once. If still no transition, abort and flag payload anomaly.
- If `payload.image_count` not incrementing: Possible detector or data-path
  fault. Command STANDBY and report anomaly.
- If `aocs.att_error` exceeds 0.5 deg during imaging: Abort session immediately.
  Images captured during high error may be unusable --- flag for ground review.
- If `tcs.temp_fpa` rises above -5.0 C: Abort imaging to protect detector.
  Investigate cooler performance.
- If `eps.bat_soc` drops below 40 % during session: Abort imaging to preserve
  power margin for safe spacecraft operations.

## Post-Conditions
- [ ] Payload returned to STANDBY mode
- [ ] Image count and storage usage logged
- [ ] AOCS remained in fine-pointing throughout (att_error < 0.1 deg)
- [ ] FPA temperature remained below -20.0 C throughout
- [ ] Pass log updated with imaging session summary
