# Workstream 4 — Payload commands, argument encoding and command-builder UX

## Divergence table

Offsets below are **within the S8.1 TC data field**: octet 0 is the `func_id` byte (`service_dispatch._handle_s8`: `func_id = data[0]` … `self._route_payload_cmd(func_id, data[1:])`), so argument octet *n* is data-field octet *n+1*. All multi-byte values are **big-endian** (`>`), everywhere, in every handler.

### Payload (func 26–39)

| Cmd (func) | `tc_catalog.yaml` | Simulator handler (`service_dispatch.py`) `struct` | MCS builder | MCS quick cmds | `pus_services.yaml` | Procedures (`configs/eosat1/procedures/**`) | OBCP | Planner `activity_types.yaml` | XTCE `MetaCommand` | Textbook (`orbit_north_week3.md`) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| PAYLOAD_SET_MODE (26=0x1A) | `mode uint8` | `mode = data[0] if data else 0` | free-text hex | `1A 00/01/02` ✓ | silent | `PAYLOAD_SET_MODE(mode=2)` ✓ | `data_hex "02"` ✓ | `func_id 0x1A, params:[2]` ✓ | uint8 ✓ | L1148, L1826, L7050 ✓ | **Consistent.** Only command in the payload set that is. |
| PAYLOAD_SET_SCENE (27=0x1B) | `scene_id uint16` | `struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0` | free-text hex | absent | — | absent | absent | absent | uint16 ✓ | L1827 | **Consistent but dead** — `set_scene` writes `s.current_scene_id`, which `capture` immediately overwrites with `self._next_scene_id`. |
| **PAYLOAD_CAPTURE (28=0x1C)** | `scene_id uint16 @1`, `lines uint16 @3` (4 B) | `if len(data) >= 8:` `lat = struct.unpack('>f', data[:4])[0]`; `lon = struct.unpack('>f', data[4:8])[0]` (8 B) | free-text hex | absent | — | `corrupted_image.md` L111 `PAYLOAD_CAPTURE(scene_id=N, lines=L)` **func_id 22**(!); `commissioning_sequence_walkthrough.md` L695 `PAYLOAD_CAPTURE(lat, lon)` func 28 | `03_payload_checkout.yaml` `data_hex "42360000c2933333"` = float lat 45.5 / lon −73.6 | `func_id: 0x1C`, **no params** → lat=lon=0.0 | `scene_id`+`lines` uint16 (generated from catalogue) | L1150 / L1828 / L7052 `scene_id + lines`; L1939 maps `IMAGE_ACQUIRE`→28 | **CONTESTED. Catalogue/textbook form is correct; handler is wrong.** See below. Today a catalogue-built TC (4 B) fails `len(data) >= 8` → captures at (0,0) and still returns success. |
| PAYLOAD_DOWNLOAD_IMAGE (29=0x1D) | `scene_id uint16` | `struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0` | free-text | absent | — | absent | absent | absent | uint16 ✓ | L1829 | Consistent. Unreachable from any UI. |
| PAYLOAD_DELETE_IMAGE (30=0x1E) | `scene_id uint16` | `struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0` | free-text | absent | — | referenced by name only | absent | absent | uint16 ✓ | L1830 | Consistent; no UI. |
| PAYLOAD_MARK_BAD_SEGMENT (31=0x1F) | `segment_id uint8`, **"Segment index (0-79)"** | `seg = data[0] if data else 0` | free-text | absent | — | `memory_segment_failure.md` | absent | absent | uint8, desc "0-79" | L1831 | **Range wrong everywhere.** `num_memory_segments` is unset in `payload.yaml` → default 8; model rejects `seg >= 8`. Valid range is **0–7**. |
| PAYLOAD_GET_IMAGE_CATALOG (32=0x20) | `fields: []` | no args | fields hidden | absent | — | — | — | — | no args | L1832 | Consistent. Returns a dict into the void — no TM packet is built. |
| PAYLOAD_SET_BAND_CONFIG (33=0x21) | `mask uint8` "bits 0-3 blue/green/red/nir" | `mask = data[0] if data else 0x0F` | free-text hex, no bit labels | absent | — | absent | absent | absent | uint8 | L1833 / L7057 | Consistent on the wire; **no source anywhere states the bit→band mapping in a machine-readable way**, and the builder offers a hex box. |
| PAYLOAD_SET_INTEGRATION_TIME (34=0x22) | 4×`float32` ms (blue/green/red/nir) | `if len(data) >= 16:` 4× `struct.unpack('>f', data[0:4]/[4:8]/[8:12]/[12:16])` | free-text hex (16 B by hand) | absent | — | `payload_calibration.md` uses **`SET_PARAM(0x0637, …)` (S20)** instead — a parameter that does not exist | absent | absent | 4×float32 | L1834 / L7058; COM-012 walkthrough L4409 quotes "8/12/16/20 ms" with no encoding | **Consistent on the wire, unusable in practice.** Operator must hand-type 16 hex octets. Setting is also **inert** in the model (§ Payload settings). |
| PAYLOAD_SET_GAIN (35=0x23) | `gain float32` "(0.1-10.0)" | `if len(data) >= 4:` `struct.unpack('>f', data[0:4])[0]` | free-text hex | absent | — | absent | absent | absent | float32 | L1835 | Consistent on the wire; **inert** in the model (`s.detector_gain` never read). |
| **PAYLOAD_COOLER_SETPOINT (36=0x24)** | `setpoint_c float32` "(-20C to 0C)" | `if len(data) >= 4:` `struct.unpack('>f', data[0:4])[0]` | free-text hex | absent | — | `fpa_cooler_activation.md` reads back **`GET_PARAM(0x0621)`** — 0x0621 is `payload.band_green_snr`, not the setpoint | absent | absent | float32 | L1026, L1836, L4299, **L5490 card step 3** `PAYLOAD_COOLER_SETPOINT(setpoint_c=-10)` | **Wire form consistent; encoding is the wrong one** (see §Authoritative encodings, §FPA worked example). Readback param is wrong (should be the `payload.fpa_setpoint` 0x0631 stub, which is never populated). Sim floor −20 °C vs textbook/procedure expectations of −30 °C (`payload_calibration.md` L17/L30, `first_light.md` L37) vs `PART2_SIM_RECONCILIATION.md` L325 (−55/−60 °C). Three incompatible thermal stories. |
| PAYLOAD_START/STOP_CALIBRATION (37/38) | `fields: []` | no args | — | absent | — | `payload_calibration.md` never issues them (uses fictitious S20 params) | absent | absent | no args | L1837/8 | Wire consistent; **the shipped calibration procedure does not use them**. |
| PAYLOAD_SET_COMPRESSION (39=0x27) | `ratio float32` "0=auto" | `if len(data) >= 4:` `struct.unpack('>f', data[0:4])[0]` | free-text hex | absent | — | absent | absent | absent | float32 | L1839 | Wire consistent. Handler also accepts an `algorithm` key that **no wire encoding can ever set** (`_route_payload_cmd` never sends it). |

### TT&C func 68 and the other contested/misdocumented entries

| Cmd (func) | Catalogue | Handler `struct` | Docs / role analysis | FDIR procedures | Verdict |
|---|---|---|---|---|---|
| **TTC_SET_TX_POWER (68=0x44)** | `level uint8` "0=low(1W), 1=nominal(5W), 2=high(8W)" | `power = struct.unpack('>f', data[:4])[0] if len(data) >= 4 else 2.0` | `PUS_SERVICE_REFERENCE.md` L509 `level (0=1W,1=5W,2=8W)`; `ttc_role.md` L61 same but **func 55**; `sot_index.md` L183/L1680 same | `ttc_reduce_power.yaml` L34 `data: [0x32]`; `ttc_antenna_switch.yaml` L34 `data: [0x32]`; `load_shed_stage3.yaml` L34 `data: [0x01]` | **CONTESTED. Catalogue/docs/FDIR are right; handler is wrong.** All three FDIR steps send 1 byte → `len(data) >= 4` is false → the handler substitutes `2.0` W. "Reduce to 50 %", "minimum power" and "reduced level" all execute as *the same* 2.0 W, and S1.7 reports success. Additionally `ttc_basic.py` `_pa_max_power_w = 5.0`, so the documented `2 = high (8 W)` is **unreachable** — `set_tx_power` returns `{"success": False, "message": "Power out of range"}` for anything > 5 W. |
| TTC_SET_DATA_RATE (65=0x41) | `rate uint8` tier code | `if len(data) >= 4: struct.unpack('>I', data[:4])[0]; elif len(data) >= 1: data[0]` | consistent | `data: [0x00]` ✓ | **Deliberate dual form**, documented in the handler and in `test_quick_commands.py`. Leave alone; document the legacy 4-byte form in the schema as `deprecated_alias`. |
| HEATER_SET_SETPOINT (44) | `circuit uint8, on_temp float32, off_temp float32` | `if len(data) >= 9:` `struct.unpack('>f', data[1:5])`, `data[5:9]` | `eps_tcs_role.md` L73 says **func 34**; `battery_cell_failure.md` L45 says **func 34** | — | Wire consistent; **func_id misdocumented in two places** (34 is PAYLOAD_SET_INTEGRATION_TIME). |
| S20.1 / S20.3 | `param_id uint16 + value float32` / `param_id uint16` | `_handle_s20`: subtype 1 = SET, subtype 3 = GET | `payload_calibration.md`, `first_light.md` label **`SET_PARAM(...) (Service 20, Subtype 3)`** — subtype 3 is GET | — | **Set/get inverted in every payload procedure.** Worse, S20.1 writes `self._engine.params[param_id]`, a dict the payload model never reads — so `SET_PARAM(0x0637, …)` (integration time), `SET_PARAM(0x0640, …)` (shutter), `SET_PARAM(0x0641, 10)` (dark frames), `SET_PARAM(0x0630, 1)` (trigger capture) are **all no-ops**. These parameter IDs are either undefined or mean something else (0x0640 = `payload.cal_flag`, 0x0642/3 = dark/bias frame counts). |
| `payload_ops_role.md` L48–55 | Whole payload table at **func 20–26** | actual 26–39 | — | — | Wholly stale block; 7 of 14 commands missing. |

### Where the MCS and the textbook actually differ (report (a))

The catalogue itself is *almost* aligned with the textbook — the real gap is **reachability**, not naming:

* `index.html` L4608: `catalogFieldDefs()` contains `if (Number(service) === 8 && Number(subtype) === 1) return null;` — **every one of the 91 S8.1 functions falls back to the hand-written `'8.1'` form**: `{name:'func_id',type:'func_select'}` + `{name:'arg', type:'hex_bytes', label:'Argument (hex, e.g. 07=line7, 00=wheel0)'}`. The catalogue's `fields[]` are never rendered for S8. This is report (c) in one line.
* MCS quick commands (`index.html` L2132–2136) expose **3** payload buttons (`1A 00/01/02`). The textbook's payload ops card (L1148–1160) and the func table (L1826–1840) advertise **14**. The Payload tab (L1776–1815) is display-only — no command controls at all.
* Net effect: 11 of 14 payload commands exist only as a hex string the operator must compose by hand, with no statement anywhere of what value means what.

---

## Authoritative encodings

### PAYLOAD_CAPTURE (func 28) → **`scene_id uint16` + `lines uint16`** (catalogue/textbook form wins; change the handler)

Engineering reasoning:

1. **Real EO payloads are not commanded in geodetic coordinates.** On Sentinel-2 MSI, SPOT/Pléiades, Landsat OLI and PROBA-V, the instrument receives an *observation request*: a start time, an acquisition duration (expressed as detector lines or as a stop time) and an instrument-configuration id. Where the spacecraft is pointing at that instant is the AOCS's problem, resolved by the mission-planning facility on the ground. Putting lat/lon in the imager TC would require the imager to hold an orbit propagator and a geodetic model — hardware no line-scan imager has.
2. **PUS S11 already supplies the "where" as a "when".** The repo has a working time-tagged path (`_handle_s11`, `/api/schedule/upload-s11`). A capture at a target is `S11.4(exec_time=T_overpass) { S8.1 func 28 }`. Encoding lat/lon in the TC duplicates and contradicts that mechanism.
3. **`scene_id` is load-bearing downstream.** `PAYLOAD_DOWNLOAD_IMAGE` (29) and `PAYLOAD_DELETE_IMAGE` (30) both take `scene_id uint16`. If the ground cannot choose the id at capture time it cannot pre-plan the downlink — it must first `PAYLOAD_GET_IMAGE_CATALOG` and parse a report that the simulator never actually transmits. Ground-assigned product ids are universal practice.
4. **`lines` is the only argument that carries data-volume physics** — the arithmetic the whole pass-planning lesson (`payload.yaml` comment block, textbook §13.2) rests on. `lines × 5000 px × active_bands × 2 bytes` is the raw frame size. Today it is discarded and the frame is a hard-coded 4.0 MB.
5. **float32 for latitude is quietly lossy at the top of the range** (ULP ≈ 7.6e-6° ≈ 0.85 m at |lat| = 90°) — if geolocation *were* commanded, int32 in 1e-7 deg would be the correct choice, not float32. That it is float32 is further evidence the handler was written ad hoc.

**Authoritative form:** `1C | scene_id:uint16 | lines:uint16` — 5 data octets total.
**Handler changes required:** honour the commanded `scene_id` (`0x0000` = auto-assign, preserving the current `_next_scene_id` behaviour); stamp `lat`/`lon` into image metadata **from `orbit_state` at capture time** (the model already receives `orbit_state` in `tick`), which is what a real spacecraft does and which removes the planner's need to inject coordinates; derive `size_mb` from `lines`.
**Consequential deletions:** `procedure_runner._build_data_hex` L426–435 (the `parameters{target_lat,target_lon} → struct.pack(">ff", …)` special case) and its test `test_capture_lat_lon_encoded_as_floats`; `obcp/03_payload_checkout.yaml` `data_hex "42360000c2933333"` → `"041200000A"`.

### TTC_SET_TX_POWER (func 68) → **`level uint8` enumerated** (catalogue/docs/FDIR form wins; change the handler), with corrected watt table

Engineering reasoning:

1. **SSPAs have discrete qualified operating points, not a continuous watt setpoint.** A spacecraft S-band SSPA is commanded to a gain state / attenuator step; the RF output that results is a function of that step *and* baseplate temperature, VSWR and bus voltage. Commanding "3.7 W" is not physically meaningful and is unverifiable in telemetry.
2. **Enumerations are the ECSS/PUS default for mode-like arguments** and are what every other mode command in this catalogue already uses (`TTC_SET_MODULATION`, `TTC_SET_DATA_RATE`, `AOCS_SET_MODE`, `OBC_SET_MODE`).
3. **FDIR must be short and deterministic.** An autonomous power-reduction action executed on-board or from a stored procedure cannot depend on a float the ground has to compute; the three FDIR procedures already encode a single byte.
4. **A float invites out-of-envelope commanding.** The current handler will happily accept `0x7F7FFFFF` (3.4e38 W) and only the model's range check saves it — after S1.1/S1.3 have already reported acceptance.
5. **The documented watt values are wrong and must change with it.** `_pa_max_power_w = 5.0` W and `eps.yaml transponder_power_w: 12.0` W DC. At a realistic 30 % PAE, 12 W DC supports ~3.6 W RF; 5 W RF is already the stretch case and 8 W RF (~27 W DC) is impossible on this bus. The documented `2 = high (8 W)` has never been executable.

**Authoritative form:** `44 | level:uint8` — 2 data octets. Levels: **`0 = LOW 1.0 W`, `1 = NOMINAL 2.0 W`, `2 = HIGH 5.0 W`** (matching `_pa_nominal_power_w = 2.0` default and `_pa_max_power_w = 5.0` ceiling). Reject unknown codes with S1.8 error `0x0003`, never clamp.
**FDIR fixes:** `ttc_reduce_power.yaml` `data: [0x32]` → `[0x00]`; `ttc_antenna_switch.yaml` `[0x32]` → `[0x00]`; `load_shed_stage3.yaml` `[0x01]` → `[0x00]`. (`0x32` was "50 %" — a third unit system that appears nowhere else.)

### Other encodings decided at the same time (payload settings, report (b)/(d))

| Command | Authoritative encoding | Reasoning |
|---|---|---|
| PAYLOAD_COOLER_SETPOINT (36) | **`setpoint_ck uint16`, centi-kelvin (0.01 K/LSB), range 20 000–30 000 (200.00–300.00 K = −73.15…+26.85 °C), operational sub-range 25 315–27 315 (−20…0 °C)** | Absolute temperature in fixed point is the standard cryo/thermal ICD unit. It removes two's complement from the operator's world entirely — **no negative engineering value ever produces a "negative-looking" raw**. 0.01 K resolution is finer than the cooler's ±2 K control band. `uint16` halves the field vs float32 and is exactly representable, so the readback matches the command bit-for-bit (float32 `-15.0` round-trips, `-15.1` does not). Alternative accepted form: `int16` centi-degC — see §FPA worked example. |
| PAYLOAD_SET_GAIN (35) | **`gain_milli uint16`, 0.001×/LSB, range 100–10 000 (0.100–10.000)** | Detector gain is a register value, not a real number; the model already clamps to [0.1, 10.0]. Fixed-point makes the clamp exactly expressible and the readback exact. |
| PAYLOAD_SET_INTEGRATION_TIME (34) | **4 × `uint16`, 0.01 ms/LSB, range 10–65 535 (0.10–655.35 ms)**, order blue/green/red/nir | 16 B of float32 for four detector timings is 4× the wire cost of the real thing; CCD integration times are register counts. 0.01 ms resolution covers the textbook's 8/12/16/20 ms and the model's 2.0 ms default. Field drops from 16 B to 8 B. |
| PAYLOAD_SET_COMPRESSION (39) | **`algorithm uint8` enum {0=NONE,1=CCSDS-121,2=CCSDS-122,3=CCSDS-123} + `ratio_deci uint8`, 0.1×/LSB, 0 = AUTO, 10–40 = 1.0–4.0** | `s.compression_algorithm` already exists in the model and is *unreachable over the wire* today. Ratio is bounded 1.5–4.0 by the scene model, so one octet at 0.1 resolution is ample. |
| PAYLOAD_SET_BAND_CONFIG (33) | **`band_mask uint8` bitfield**, bit0 BLUE 443 nm, bit1 GREEN 560, bit2 RED 665, bit3 NIR 865, bits 4–7 reserved 0, `mask != 0` | Already correct on the wire; the change is declaring the bit semantics machine-readably so the UI can render four checkboxes. |
| PAYLOAD_MARK_BAD_SEGMENT (31) | `segment_id uint8`, **range 0–7** (was documented 0–79) | `num_memory_segments` defaults to 8; the model rejects ≥ 8. |
| **New:** PAYLOAD_SELECT_FILTER (func 40 → reallocate, see WP-7) | `position uint8` enum 0–3 | Model implements `select_filter`; nothing can reach it. |
| **New:** PAYLOAD_CYCLE_SHUTTER | `cycles uint8` 1–100 | Model implements `cycle_shutter`; nothing can reach it. Required by COM-011 dark-frame acquisition. |
| **New:** PAYLOAD_INITIATE_TRANSFER | `scene_id uint16` | Model implements `initiate_transfer`; the textbook's `S13_CREATE_TRANSFER` workflow has no bridge to it. |

**Rule adopted for the whole catalogue:** *no float32 on an uplink argument.* Every engineering quantity is fixed-point integer + declared scale/offset. Floats are kept only for downlink parameter reports. This is standard ECSS practice (deterministic, bit-exact round-trip, no NaN/denormal on the uplink, shorter TCs) and it is what makes report (c) solvable — every argument becomes a small integer with an obvious hex form.

---

## Argument schema design

### Where it lives: **extend `tc_catalog.yaml`, generate XTCE from it**

XTCE *does* have the right vocabulary — `ArgumentTypeSet` with `PolynomialCalibrator`/`SplineCalibrator` inside `IntegerDataEncoding`, `ValidRange`, `EnumerationList`, `UnitSet`, and `CommandContainer` for byte layout. But it should **not** become the hand-edited source here:

* `configs/eosat1/xtce/eosat1.xml` is a **generated artifact** (`tools/generate_xtce.py`), and everything upstream of it (simulator, MCS, planner, procedures, OBCP) reads YAML. Making the XML authoritative inverts the dependency and orphans six consumers.
* The current generated `CommandMetaData` proves the point: it emits exactly three argument types (`float32_ArgType`, `int16_ArgType`, `uint8_ArgType`) with **no calibrators, no `ValidRange`, no `EnumerationList`, and no `CommandContainer`** — so it carries no byte offsets and no func_id encoding. It also contains a live bug: `<Argument name="True" argumentTypeRef="uint8_ArgType">` in `EPS_FPA_COOLER` and `EPS_TRANSPONDER_TX`, because YAML parsed the field name `on` as the boolean `True`. Quoting `"on"` in the catalogue and `str()`-ing in the generator fixes it.
* Nothing in the repo parses XTCE. Writing the truth into a format no consumer reads guarantees a seventh drift surface.

**Decision: `tc_catalog.yaml` is the single source of truth. `generate_xtce.py` is upgraded to emit conformant XTCE (calibrators, ranges, enums, `CommandContainer` with `FixedValueEntry` for `func_id`) so the XTCE becomes a faithful *export* for third-party tools (Yamcs, MOIS), validated by a round-trip test.** Same for `generate_smdl.py`.

### Schema (per-field additions to `TCFieldDef`)

```yaml
# smo_common.config.schemas.TCFieldDef — new optional keys, all backward compatible
- name:        str            # existing
  type:        str            # existing: uint8|int8|uint16|int16|uint32|int32|float32|bytes
  description: str            # existing
  # ── NEW ──
  offset:      int            # octet offset within the TC DATA FIELD (func_id byte = 0)
  endian:      big            # default 'big'; declared, never assumed
  unit:        str            # engineering unit shown in the UI ("degC", "ms", "W", "")
  raw_unit:    str            # optional, when raw != eng ("cK")
  calibration:                # raw -> engineering. Absent => identity.
    type:      linear         # linear | polynomial | enum | bitfield | none
    scale:     0.01           # eng = raw*scale + offset
    offset:    -273.15
    # polynomial: coefficients: [c0, c1, c2, ...]  (eng = sum ci * raw^i)
  range:       {min: -20.0, max: 0.0}     # in ENGINEERING units, inclusive
  raw_range:   {min: 25315, max: 27315}   # derived; asserted by test, not hand-kept
  default:     -15.0                      # engineering units
  enum:        [{value: 0, label: LOW, description: "1.0 W"}, ...]
  bits:        [{bit: 0, name: BLUE, description: "443 nm"}, ...]
  reserved:    false
  criticality: nominal|caution|critical    # per-field second-key requirement
```

Command-level additions: `arg_schema_version: 1`, `deprecated_aliases: [{...}]` (for the S8.1 func 65 legacy 4-byte form), `readback: {param_id: 0x0631}` (which TM parameter verifies this command).

### Four worked examples

```yaml
  # ── 1. Contested command, resolved: geodesy removed, product id + duration in ──
  - name: PAYLOAD_CAPTURE
    service: 8
    subtype: 1
    func_id: 28                      # data-field octet 0 = 0x1C
    arg_schema_version: 1
    criticality: caution
    description: >-
      Trigger one image acquisition. The WHERE is set by WHEN: schedule this TC
      with S11.4 at the target overpass. Geolocation is stamped on board from the
      propagated sub-satellite point at shutter open and returned in the image
      catalogue — it is not an argument.
    readback: { param_id: 0x060C }   # payload.last_scene_id
    fields:
      - name: scene_id
        type: uint16
        offset: 1
        endian: big
        unit: ""
        calibration: { type: none }
        range: { min: 0, max: 65535 }
        default: 0
        description: >-
          Ground-assigned product identifier, later used by
          PAYLOAD_DOWNLOAD_IMAGE (29) and PAYLOAD_DELETE_IMAGE (30).
          0x0000 = let the payload auto-assign the next free id.
      - name: lines
        type: uint16
        offset: 3
        endian: big
        unit: "lines"
        calibration: { type: none }
        range: { min: 256, max: 20000 }
        default: 2048
        description: >-
          Along-track detector lines. Acquisition time = lines / line_rate_hz
          (500 Hz, subsystems/payload.yaml). Raw volume =
          lines x 5000 px x popcount(band_mask) x 2 B; 2048 lines x 4 bands
          = 4.0 MB = subsystems/payload.yaml image_size_mb.

  # ── 2. The engineering-unit problem, solved: no sign, no two's complement ──
  - name: PAYLOAD_COOLER_SETPOINT
    service: 8
    subtype: 1
    func_id: 36                      # data-field octet 0 = 0x24
    arg_schema_version: 1
    criticality: caution
    description: "Set the FPA thermo-electric cooler regulation setpoint."
    readback: { param_id: 0x0631 }   # payload.fpa_setpoint
    fields:
      - name: setpoint
        type: uint16                 # WAS float32 degC
        offset: 1
        endian: big
        unit: "degC"                 # what the OPERATOR types
        raw_unit: "cK"               # what goes on the WIRE: centi-kelvin
        calibration:
          type: linear
          scale: 0.01                # eng_degC = raw * 0.01 - 273.15
          offset: -273.15
        range:     { min: -20.0, max: 0.0 }
        raw_range: { min: 25315, max: 27315 }
        default: -15.0               # subsystems/payload.yaml fpa_cooler_target_c
        description: >-
          FPA regulation setpoint in degC. Encoded as unsigned centi-kelvin, so a
          negative Celsius value never requires a two's-complement raw. Outside
          [-20, 0] degC the TC is REJECTED with S1.8 error 0x0003 (Peltier-class
          cooler; see manual 06_payload section 3).

  # ── 3. Enumerated, replacing an unverifiable float32 watt setpoint ──
  - name: TTC_SET_TX_POWER
    service: 8
    subtype: 1
    func_id: 68                      # data-field octet 0 = 0x44
    arg_schema_version: 1
    criticality: caution
    readback: { param_id: 0x0507 }   # ttc.tx_fwd_power
    description: >-
      Select an SSPA operating point. Discrete steps only: the RF output of a
      solid-state PA is a function of the commanded gain state, baseplate
      temperature and VSWR, and is not a continuous ground-settable quantity.
    fields:
      - name: level
        type: uint8
        offset: 1
        unit: ""
        calibration: { type: enum }
        default: 1
        enum:
          - { value: 0, label: LOW,     description: "1.0 W RF - link-margin-limited passes, FDIR power reduction" }
          - { value: 1, label: NOMINAL, description: "2.0 W RF - routine operations (ttc.yaml pa_nominal_power_w)" }
          - { value: 2, label: HIGH,    description: "5.0 W RF - ceiling (ttc.yaml pa_max_power_w); ~17 W DC at 30% PAE" }
        description: >-
          Unknown codes are REJECTED with S1.8 error 0x0003, never clamped.
          The old '2 = 8 W' in PUS_SERVICE_REFERENCE.md was never executable:
          set_tx_power rejects anything above pa_max_power_w = 5.0 W.

  # ── 4. Bitfield, with the bit->band map finally machine-readable ──
  - name: PAYLOAD_SET_BAND_CONFIG
    service: 8
    subtype: 1
    func_id: 33                      # data-field octet 0 = 0x21
    arg_schema_version: 1
    readback: { param_id: 0x0625 }   # payload.band_enable_mask
    fields:
      - name: band_mask
        type: uint8
        offset: 1
        unit: ""
        calibration: { type: bitfield }
        range: { min: 0x01, max: 0x0F }   # all-bands-off is rejected
        default: 0x0F
        bits:
          - { bit: 0, name: BLUE,  description: "443 nm, 20 nm BW, SNR 40 dB - chlorophyll-a" }
          - { bit: 1, name: GREEN, description: "560 nm, 20 nm BW, SNR 50 dB - turbidity" }
          - { bit: 2, name: RED,   description: "665 nm, 20 nm BW, SNR 48 dB - atmos. correction" }
          - { bit: 3, name: NIR,   description: "865 nm, 40 nm BW, SNR 42 dB - atmos. correction" }
          - { bit: 4, name: RESERVED, reserved: true }
          - { bit: 5, name: RESERVED, reserved: true }
          - { bit: 6, name: RESERVED, reserved: true }
          - { bit: 7, name: RESERVED, reserved: true }
        description: >-
          Each enabled band multiplies raw volume by lines x 5000 px x 2 B.
          Bit order matches SPECTRAL_BANDS[].bit in payload_basic.py.
```

Equivalent conformant XTCE, emitted by the upgraded `generate_xtce.py` (illustrating that the model maps cleanly, so the export is lossless):

```xml
<ArgumentTypeSet>
  <IntegerArgumentType name="PAYLOAD_COOLER_SETPOINT_setpoint_ArgType" signed="false">
    <UnitSet><Unit>degC</Unit></UnitSet>
    <IntegerDataEncoding sizeInBits="16" encoding="unsigned" byteOrder="mostSignificantByteFirst">
      <DefaultCalibrator>
        <PolynomialCalibrator>
          <Term coefficient="-273.15" exponent="0"/>
          <Term coefficient="0.01"    exponent="1"/>
        </PolynomialCalibrator>
      </DefaultCalibrator>
    </IntegerDataEncoding>
    <ValidRange minInclusive="-20.0" maxInclusive="0.0"/>
  </IntegerArgumentType>
  <EnumeratedArgumentType name="TTC_SET_TX_POWER_level_ArgType">
    <IntegerDataEncoding sizeInBits="8" encoding="unsigned"/>
    <EnumerationList>
      <Enumeration value="0" label="LOW"/>
      <Enumeration value="1" label="NOMINAL"/>
      <Enumeration value="2" label="HIGH"/>
    </EnumerationList>
  </EnumeratedArgumentType>
</ArgumentTypeSet>
<MetaCommandSet>
  <MetaCommand name="PAYLOAD_COOLER_SETPOINT">
    <BaseMetaCommand metaCommandRef="/EOSAT-1/PUS/S8_1_PerformFunction"/>
    <ArgumentList>
      <Argument name="setpoint" argumentTypeRef="PAYLOAD_COOLER_SETPOINT_setpoint_ArgType" initialValue="-15.0"/>
    </ArgumentList>
    <CommandContainer name="PAYLOAD_COOLER_SETPOINT_Container">
      <EntryList>
        <FixedValueEntry name="func_id" binaryValue="24" sizeInBits="8"/>
        <ArgumentRefEntry argumentRef="setpoint"/>
      </EntryList>
    </CommandContainer>
  </MetaCommand>
</MetaCommandSet>
```

### Consumers, and how each stops drifting

| Consumer | Today | After |
|---|---|---|
| **MCS command builder** (`index.html` `catalogFieldDefs`) | returns `null` for S8.1 → free-text hex | delete the S8.1 early-return; render from `fields[]` incl. `enum`/`bits`/`range`/`unit` |
| **MCS quick commands** (`index.html` L2060–2160, `data-d` literals) | 40+ hand-written hex strings; 3 payload buttons | generated at load from a `quick_commands:` list in `tc_catalog.yaml` naming command + engineering values; the hex is computed, never typed |
| **Procedure runner** (`procedure_runner._build_data_hex`) | `params` packed one octet each; hard-coded lat/lon special case | pack via a shared `smo_common.commanding.encode(command_name, {arg: eng_value})`; `params:` list deprecated in favour of named `args:` |
| **OBCP** (`configs/eosat1/obcp/*.yaml`) | raw `data_hex` blobs | `args: {scene_id: 1042, lines: 2048}`; `data_hex` generated at load; a test asserts any hand-written `data_hex` still matches |
| **Planner** (`activity_types.yaml`, `imaging_planner.py`) | `params: [2]` positional; capture has no args | `args:` maps; `imaging_planner.generate_capture_sequence` emits `scene_id`/`lines` |
| **S11 time-tagged builder** | shares `CMD_BUILDERS` → inherits the free-text box | inherits the typed form automatically (same code path) |
| **XTCE / SMDL** | generated, lossy | generated with calibrators/enums/ranges/containers |
| **Spacecraft user manual + textbook** | prose tables, hand-maintained | tables generated by `tools/gen_command_reference.py` into `configs/eosat1/manual/09_command_reference.md` and a textbook include |

### Drift prevention — four mechanical gates

1. **Extend the existing dispatcher scanner.** `tests/test_mcs/test_quick_commands.py::_scan_dispatcher` already AST-walks `service_dispatch.py` and derives, per func_id, the minimum and maximum argument octets each handler touches. Today it asserts only `declared >= handler_minimum` and pins `KNOWN_CATALOGUE_HANDLER_DIVERGENCES = {28, 65, 68}`. **Tighten to equality** — `declared_arg_bytes == handler_max_offset` for every S8.1 command — and reduce the pinned set to `{65}` (the deliberate legacy dual form). 28 and 68 must come out of that set as part of their fix; that is the acceptance test for WP-1/WP-2.
2. **New `tests/test_common/test_arg_schema.py`.** For every command with `fields[]`: offsets are contiguous from 1 (S8.1) or 0 and sum to `expected_data_bytes`; `range` round-trips through `calibration` to `raw_range` inside the field's integer width; `default` is inside `range`; every `enum.value` fits the width and is unique; `bits[]` covers 0..width−1 with no duplicates; `readback.param_id` exists in `telemetry/parameters.yaml`.
3. **New `tests/test_content/test_command_encoders_agree.py`.** Encode the *same* engineering-unit dict through all five encoders — MCS builder (via a Node/`js2py` harness or a ported pure-Python `encode()`), `procedure_runner._build_data_hex`, the OBCP loader, the planner activity expander, and the S11 embedder — and assert byte-identical output for a table of ~20 commands including all four examples above. This is the test that would have caught the 0x32/2.0 W bug on day one.
4. **Generated, diffed artifacts.** `tools/gen_command_reference.py` writes `09_command_reference.md`; `generate_xtce.py`/`generate_smdl.py` write the XML. A CI step regenerates and fails on any diff, so the manual and the XTCE cannot drift from the YAML. Extend `tools/procedure_audit.py` to fail when a `**TC:**` annotation in a procedure markdown names a mnemonic/func_id pair that contradicts the catalogue (this would already have flagged `corrupted_image.md`'s "func_id 22" and `battery_cell_failure.md`'s "func 34").

---

## FPA setpoint worked example

The user asks about "the FPA set point". There are two distinct setpoints and the docs conflate them — that alone is part of the confusion:

* **`PAYLOAD_COOLER_SETPOINT` (S8.1 func 36 = 0x24)** — the payload's TEC regulation target. This is the one that takes an engineering temperature.
* **`FPA_COOLER` (S8.1 func 43 = 0x2B)** / **`EPS_FPA_COOLER` (func 17 = 0x11)** — on/off enables, `uint8`, no temperature.
* `HEATER_SET_SETPOINT` (func 44) is TCS heaters, **not** the FPA.

### Current encoding (what the operator must do today)

Handler, verbatim (`service_dispatch.py` `_route_payload_cmd`):

```python
elif func_id == 36:  # set cooler setpoint
    if len(data) >= 4:
        setpoint = struct.unpack('>f', data[0:4])[0]
```

So the wire is `0x24` + **IEEE-754 binary32, big-endian, °C**. Total 5 data octets.

| Eng. value | float32 BE | Full S8.1 data field | Result |
|---|---|---|---|
| −10 °C | `C1 20 00 00` | `24 C1 20 00 00` | accepted (`-20 ≤ t ≤ 0`) |
| −15 °C | `C1 70 00 00` | `24 C1 70 00 00` | accepted; this is the config default |
| −20 °C | `C1 A0 00 00` | `24 C1 A0 00 00` | accepted, at the floor |
| **−65 °C** | `C2 82 00 00` | **`24 C2 82 00 00`** | **REJECTED** — `payload_basic.py`: `if -20.0 <= target <= 0.0` … else `{"success": False, "message": "Invalid cooler setpoint (-20C to 0C)"}` → S1.8 |

To type `24 C2 82 00 00` an operator must know that: the sign lives in bit 31; the exponent is biased by 127; 65 = 1.015625 × 2⁶ so the exponent field is 133 = `0x85`; the mantissa `.015625` = `0x080000`; and the whole thing is big-endian. There is no realistic chance of a student doing that at a console, and the MCS gives them a text box labelled `Argument (hex, e.g. 07=line7, 00=wheel0)`.

If it *had* been `int16` °C, the answer to the user's literal question would be: −65 → `FF BF` (two's complement) — i.e. the operator must compute 65 536 − 65. That is exactly the trap to design out.

### Recommended encoding

**`uint16`, 0.01 K/LSB (centi-kelvin), big-endian.** `raw = round((T_degC + 273.15) × 100)`, `T_degC = raw × 0.01 − 273.15`.

| Eng. value | Absolute | raw (dec) | raw (hex) | Full S8.1 data field | Status |
|---|---|---|---|---|---|
| **−65.00 °C** | 208.15 K | 20 815 | `0x514F` | **`24 51 4F`** | outside the Peltier envelope → rejected S1.8/0x0003, but **the encoding itself is trivial and positive** |
| −50.00 °C | 223.15 K | 22 315 | `0x572B` | `24 57 2B` | rejected (envelope) |
| −20.00 °C | 253.15 K | 25 315 | `0x62E3` | `24 62 E3` | accepted — floor |
| −15.00 °C | 258.15 K | 25 815 | `0x64D7` | `24 64 D7` | accepted — default |
| −10.00 °C | 263.15 K | 26 315 | `0x66CB` | `24 66 CB` | accepted — textbook COM-010 step 3 |
| 0.00 °C | 273.15 K | 27 315 | `0x6AB3` | `24 6A B3` | accepted — ceiling |

**The direct answer to (d): under the recommended encoding a negative engineering temperature never produces a negative raw value, because the raw field is absolute temperature. There is no two's complement to compute, no sign bit to reason about, and no way to mistake a sign-extended dump for a valid value.** Data field shrinks from 5 to 3 octets.

**Accepted alternative if the mission prefers Celsius on the wire:** `int16` centi-degC, `raw = round(T × 100)`. Then −65 °C → −6 500 → `0xE69C` (`24 E6 9C`) and −15 °C → −1 500 → `0xFA24`. This is also fine *provided the UI always renders the engineering value* — but it re-introduces the two's-complement question the user asked, which is why centi-kelvin is preferred. Do **not** use `int16` deci-degC (−65 → `0xFD76`): 0.1 K resolution is coarser than the readback noise and invites off-by-ten errors against the 0.01-scaled telemetry parameters.

### What the UI must show

```
PAYLOAD_COOLER_SETPOINT                            S8.1  func 36 (0x24)   CAUTION
  Set the FPA thermo-electric cooler regulation setpoint.

  setpoint     [ -15.00 ] °C     [-20.00 … 0.00]   default -15.00
               |======================o=========|          (slider, snapped to 0.01)
               = 258.15 K  = 25815 cK  = 0x64D7  (uint16, big-endian, 0.01 K/LSB)

  RAW  ▸  24 64 D7                                        3 data bytes  ✓ EXPECTED 3
  ── paste hex to reverse-decode ──────────────────────────────────────────────
  Readback: payload.fpa_setpoint (0x0631) should equal -15.00 °C within 10 s
```

Typing `-65` turns the field red inline — *"−65.00 °C is below the −20.00 °C floor (Peltier-class cooler, `subsystems/payload.yaml`). The spacecraft will reject this with S1.8 error 0x0003."* — with SEND disabled unless the operator ticks **"send anyway (fault-injection training)"**, preserving the existing deliberate soft-check philosophy in `sendPUSCommand`.

---

## Command builder UX spec

### Core behaviour

1. **Delete the S8.1 escape hatch.** `catalogFieldDefs()` line `if (Number(service) === 8 && Number(subtype) === 1) return null;` is removed. When the operator picks an S8.1 function, `updateStructuredFields()` renders that command's `fields[]`, exactly as it already does for S3/S6/S12/S20. `PUS_FIELD_DEFS['8.1']` keeps `func_select` for the function picker only; the `{name:'arg', type:'hex_bytes'}` entry is deleted.
2. **One input widget per field, chosen by `calibration.type`:**
   * `linear`/`polynomial` → `<input type="number">` with `step` = `scale`, `min`/`max` = `range`, unit rendered as a suffix chip, `value` = `default`.
   * `enum` → `<select>`, options `"0 — LOW (1.0 W RF)"`, disabled options for values the current spacecraft state forbids.
   * `bitfield` → one checkbox per non-reserved `bits[]` entry, labelled `BLUE 443 nm`, plus a live `mask = 0x0F` readout; reserved bits shown greyed and forced 0.
   * `none`, integer → plain number input with `min`/`max`; `HEX_FIELD_NAMES` (`param_id`, `device_id`, `sid`, …) keep the existing hex input.
   * `bytes` → the existing hex textarea, with a byte counter.
3. **Live raw-hex preview.** An `oninput` handler on every field re-runs `packStructuredFields()` and updates a monospace `RAW ▸ 24 64 D7` line and the existing `cmd-bytes-chip` (`EXPECTED 3 B · ENTERED 3 B ✓`). No "Pack" button press required; the button remains for compatibility.
4. **Per-field validation, non-blocking by design.** Out-of-range → red border + one-line explanation naming the config file that sets the limit. SEND still available behind an explicit "send anyway" checkbox (the repo deliberately allows malformed TCs for fault-injection training — see the `confirm()` in `sendPUSCommand`).
5. **Reverse decode.** The `cmd-data` hex box gets a `paste`/`input` listener: if the hex parses against the currently selected command's `fields[]`, every structured field is back-populated **in engineering units**, and a `DECODED` badge appears. If octet 0 does not match the selected func_id, the builder auto-switches to the command that octet 0 identifies (the `wireFuncId()` helper already exists). Pasting `24 64 D7` selects PAYLOAD_COOLER_SETPOINT and shows `setpoint = -15.00 °C`.
6. **Provenance line** under the command name: `S8.1 func 36 (0x24) · 3 data bytes · payload_ops · CAUTION · readback payload.fpa_setpoint (0x0631)`.
7. **Same renderer everywhere.** Extract the field renderer + encoder into one module used by: the Commanding tab, the S11 panel (`#s11-cmd-builder`, already a `CMD_BUILDERS` instance — inherits for free), the Procedure Builder step editor, the OBCP editor, and a read-only variant for procedure/planner *display*, so a procedure step renders as `PAYLOAD_COOLER_SETPOINT(setpoint = −15.00 °C)  ▸ 24 64 D7` instead of `S8.1 func 36`.
8. **Quick commands become schema-driven.** Replace the `data-d="1A 01"` literals with `data-cmd="PAYLOAD_SET_MODE" data-args='{"mode":1}'`; the click handler encodes via the shared encoder and fills the builder. Add the 11 missing payload buttons so the MCS and the textbook ops cards finally agree.

### What the operator sees — three concrete panels

**FPA setpoint** — as rendered above.

**PAYLOAD_CAPTURE (recommended form):**

```
PAYLOAD_CAPTURE                                    S8.1  func 28 (0x1C)   CAUTION
  Trigger one image acquisition. The WHERE is set by the WHEN — schedule with
  S11.4 at the target overpass. Geolocation is stamped on board.

  scene_id     [ 1042 ]              [0 … 65535]     0 = auto-assign
                                     ▸ 0x0412
  lines        [ 2048 ] lines        [256 … 20000]   default 2048
                                     ▸ 0x0800
               ⓘ 2048 lines @ 500 Hz = 4.10 s acquisition
                 2048 x 5000 px x 4 bands x 2 B = 4.00 MB raw
                 -> 1.5–2.7 MB stored after CCSDS-123 (ratio 1.5–4.0)

  RAW  ▸  1C 04 12 08 00                                 5 data bytes  ✓
  Preconditions (live):  payload.mode = 2 IMAGING ✓ | payload.fpa_ready = 1 ✓
                         free store 16.0 GB ✓         | aocs.mode = 5 FINE_POINT ✓
```

The precondition strip is derived from the model's own guards (`if s.mode != 2` → "Not in IMAGING mode"; `if not s.fpa_ready` → "FPA not ready"; `_available_storage_mb() < stored_size`), so the operator sees *before pressing SEND* the three reasons this TC is going to fail.

**Bitfield — PAYLOAD_SET_BAND_CONFIG:**

```
PAYLOAD_SET_BAND_CONFIG                            S8.1  func 33 (0x21)
  band_mask   [x] bit0  BLUE   443 nm  20 nm BW   SNR 40 dB   chlorophyll-a
              [x] bit1  GREEN  560 nm  20 nm BW   SNR 50 dB   turbidity
              [x] bit2  RED    665 nm  20 nm BW   SNR 48 dB   atmos. correction
              [ ] bit3  NIR    865 nm  40 nm BW   SNR 42 dB   atmos. correction
              [ ] bit4-7  reserved (must be 0)
                                                    mask = 0x07   3 bands active
              ⚠ mask = 0x00 is rejected (at least one band required)
              ⓘ dropping NIR cuts raw volume 4.00 -> 3.00 MB per 2048-line frame

  RAW  ▸  21 07                                          2 data bytes  ✓
  Readback: payload.band_enable_mask (0x0625), payload.active_bands (0x0624)
```

---

## Payload settings reference table

Derived from `payload_basic.py` `PayloadState` + `handle_command`, `_route_payload_cmd`, and `subsystems/payload.yaml`. "Commandable" = reachable over the wire today.

| Setting | Model state | Default | Commandable today | Command | Wire form today | **Recommended form** | Readback TM | Physically modelled? | Notes / gaps |
|---|---|---|---|---|---|---|---|---|---|
| Payload mode | `mode` | 0 OFF | ✅ | PAYLOAD_SET_MODE (26) | `1A` + uint8 | unchanged, `enum{0 OFF,1 STANDBY,2 IMAGING}` | `payload.mode` 0x0600 | Yes — gates capture, cooler, SNR | Only fully-working payload command |
| Scene id (pre-set) | `current_scene_id` | 0 | ✅ | PAYLOAD_SET_SCENE (27) | `1B` + uint16 | **delete** — superseded by CAPTURE `scene_id` | `payload.scene_id` 0x0606 | **No** — `capture` overwrites it immediately | Dead command |
| Capture trigger | — | — | ✅ (wrong form) | PAYLOAD_CAPTURE (28) | `1C` + 2×float32 lat/lon | `1C` + `scene_id:u16` + `lines:u16` | `payload.last_scene_id` 0x060C | Yes | Catalogue-built TC captures at (0,0) |
| **Lines per frame** | *(none)* | — | ❌ | (arg of 28) | discarded | new `lines` field; drives `size_mb` | — | **No** — frame is hard-coded 4.0 MB | Blocks the data-volume lesson |
| **Detector gain** | `detector_gain` | 1.0 | ✅ | PAYLOAD_SET_GAIN (35) | `23` + float32 | `23` + `uint16` ×0.001, 0.100–10.000 | **none** (0x0636 `gain_code` is a `stub`) | **No** — set, never read; no SNR/DN effect | Inert setting; needs TM param + SNR coupling |
| **Integration time, per band** | `integration_time_{blue,green,red,nir}` | 2.0 ms ea. | ✅ | PAYLOAD_SET_INTEGRATION_TIME (34) | `22` + 4×float32 | `22` + 4×`uint16` ×0.01 ms | **none** (0x0618/0x062F carry the *average* only; 0x0635 is a `stub`) | **No** — set, never read | Textbook COM-012 quotes 8/12/16/20 ms with no encoding; procedure uses a non-existent `SET_PARAM(0x0637)` |
| Integration time, average | `integration_time_ms` | 2.0 ms | ⚠ derived only | — | — | read-only | `payload.integration_time` 0x0618 | Reported only | |
| **Band enable mask** | `band_enable_mask` | 0x0F | ✅ | PAYLOAD_SET_BAND_CONFIG (33) | `21` + uint8 | + `bits[]` declaration | `payload.band_enable_mask` 0x0625, `active_bands` 0x0624 | **Yes** — gates per-band SNR and `active_bands` | The one setting that both commands and matters |
| **Binning** | *(absent)* | — | ❌ | — | — | **new** `PAYLOAD_SET_BINNING(mode enum 1x1/2x2/4x4)` | — | Not modelled | Named in the task; does not exist. GSD 10 m is fixed from altitude/IFOV |
| **TDI stages** | *(absent)* | — | ❌ | — | — | **new** `PAYLOAD_SET_TDI(stages enum 1/2/4/8/16/32)` | — | Not modelled | Named in the task; does not exist. Would be the natural physical driver of SNR ∝ √N |
| **Compression ratio** | `compression_override`, `compression_ratio` | 0.0 (auto) / 2.0 | ✅ | PAYLOAD_SET_COMPRESSION (39) | `27` + float32 | `27` + `algorithm:u8 enum` + `ratio_deci:u8` | `payload.compression_ratio` 0x0614 | Partly — override sets `compression_ratio`, which sizes stored images | Scene-dependent auto ratio 1.5–4.0 |
| **Compression algorithm** | `compression_algorithm` | 3 (CCSDS-123) | ❌ | (handler accepts `algorithm`) | **no encoding exists** | new octet in 39 | none | Metadata only | Handler key `algorithm` is unreachable from the wire |
| **FPA cooler setpoint** | `cooler_setpoint_c`, `_fpa_target` | −15.0 °C | ✅ | PAYLOAD_COOLER_SETPOINT (36) | `24` + float32 °C, clamp −20…0 | `24` + `uint16` centi-kelvin | `payload.fpa_setpoint` 0x0631 (`stub`, never populated) | **Yes** — first-order lag to `_fpa_target`, τ=100 s | Procedure reads back 0x0621 (= green-band SNR). Envelope conflicts with textbook/procedures (−30/−55/−60 °C) |
| FPA cooler on/off (payload side) | `cooler_active` | derived from mode | ⚠ indirect | via PAYLOAD_SET_MODE ≥ 1 | — | — | `payload.cooler_pwr` 0x0602 | Yes | |
| FPA cooler on/off (TCS/EPS) | TCS/EPS state | — | ✅ | FPA_COOLER (43), EPS_FPA_COOLER (17) | `2B`/`11` + uint8 | unchanged | `eps.pl_fpa_cooler` 0x0114 | Yes | Two enables for one device — deliberate cross-position dependency, but undocumented as such |
| FPA ready hysteresis | `fpa_ready_hysteresis_s` | 60 s | ❌ | — | — | leave uncommandable (design constant) | `payload.fpa_ready` 0x0610 | Yes | |
| **Calibration start/stop** | `calibration_active/_state/_progress` | idle | ✅ | 37 / 38 | `25` / `26`, no args | unchanged | 0x062A/0x062B/0x062C | Yes — 2-phase state machine, 30 s | **The shipped COM-011 procedure never issues them** |
| **Calibration mode selection** (dark only / flat only / both) | `calibration_state` | — | ❌ | — | — | **new** arg on 37: `mode enum{0 DARK,1 FLAT,2 BOTH}` | 0x062A | Partly — state machine has both phases | Cannot run dark-only, which COM-011 step 2 requires |
| **Calibration lamp** | `cal_lamp_on` | False | ❌ | — | — | **new** `PAYLOAD_CAL_LAMP(on u8)`, or S2 device 0x0602 | `payload.cal_lamp_on` 0x0615 | Reported only | Device 0x0602 exists in `device_states` → reachable via **S2.1** `02 06 02 01`, but that path is documented nowhere |
| **Shutter position / cycle test** | `shutter_position`, `shutter_test_*` | 1 OPEN | ❌ | `cycle_shutter` implemented, unrouted | — | **new** `PAYLOAD_CYCLE_SHUTTER(cycles u8)` | none | Yes — toggles, counts cycles | `payload_calibration.md` commands it via the non-existent `SET_PARAM(0x0640)`. Device 0x0603 exists |
| **Filter wheel position** | `filter_position`, `filter_rotation_*` | 0 | ❌ | `select_filter` implemented, unrouted | — | **new** `PAYLOAD_SELECT_FILTER(position u8 0–3)` | none | Mechanism modelled (2 s / 90°), no optical effect | |
| **S13 transfer initiate** | `transfer_*` | inactive | ❌ | `initiate_transfer` implemented, unrouted | — | **new** `PAYLOAD_INITIATE_TRANSFER(scene_id u16)` | 0x062D/0x062E | Yes | Textbook §6.7 sells `S13_CREATE_TRANSFER` → `S13_REPORT_STATUS`; no bridge exists |
| Bad-segment marking | `bad_segments`, `mem_segments_bad` | [] | ✅ | PAYLOAD_MARK_BAD_SEGMENT (31) | `1F` + uint8 | range **0–7** (docs say 0–79) | 0x0612 | Yes — shrinks `mem_total_mb` | |
| Image catalogue query | — | — | ✅ | PAYLOAD_GET_IMAGE_CATALOG (32) | `20`, no args | add S8.2 report packet | — | Returns a dict that is discarded | No TM packet is ever built — the operator sees nothing |
| Payload device on/off (FPA 0x0600, cooler 0x0601, cal lamp 0x0602, shutter 0x0603, compression unit 0x0604) | `device_states` | all True | ✅ via **S2.1** | S2_* | `dev_id:u16 + state:u8` | catalogue entries + manual section | S2.6 report | Gate checks exist in some models | **No `S2_PAYLOAD_*` entries exist in `tc_catalog.yaml`** (EPS/AOCS/TCS have theirs) and `payload_ops` position does **not** list service 2 in `allowed_services` — so payload devices are unreachable from the payload console |

**Settings the operator cannot reach at all today:** lines-per-frame, compression algorithm, calibration mode, calibration lamp (except via an undocumented S2 path), shutter, filter wheel, S13 transfer initiate, payload device on/off from the payload console. **Settings that are commandable but inert:** detector gain, per-band integration times, scene id (pre-set). **Settings the task named that do not exist in the model at all:** binning, TDI stages.

---

## Work packages

| # | Title | Files | Acceptance criteria (testable) | Effort | Depends on |
|---|---|---|---|---|---|
| **WP-0** | Schema definition + validator | `packages/smo-common/src/smo_common/config/schemas.py` (`TCFieldDef`), new `packages/smo-common/src/smo_common/commanding/{__init__,encode,decode}.py`, new `tests/test_common/test_arg_schema.py` | `encode("PAYLOAD_COOLER_SETPOINT", {"setpoint": -15.0}) == bytes.fromhex("2464d7")`; `decode` round-trips; schema test green over the whole catalogue (offsets contiguous, ranges round-trip, enums unique, `readback.param_id` resolvable) | **M** | — |
| **WP-1** | Resolve func 28 PAYLOAD_CAPTURE | `service_dispatch.py` `_route_payload_cmd` (28), `payload_basic.py` `capture`, `tc_catalog.yaml`, `procedure_runner._build_data_hex` (delete lat/lon branch), `imaging_planner.generate_capture_sequence`, `activity_types.yaml`, `obcp/03_payload_checkout.yaml`, `tests/test_planner/test_activity_funcids.py` | `1C 04 12 08 00` → image `scene_id == 1042`, `size_mb == 4.0`, `lat/lon` stamped from `orbit_state`; `28` removed from `KNOWN_CATALOGUE_HANDLER_DIVERGENCES`; equality assertion in `test_quick_commands.py` passes | **M** | WP-0 |
| **WP-2** | Resolve func 68 TTC_SET_TX_POWER | `service_dispatch.py` `_route_ttc_cmd` (68), `ttc_basic.py` `set_tx_power`, `tc_catalog.yaml`, `fdir/procedures/{ttc_reduce_power,ttc_antenna_switch,load_shed_stage3}.yaml`, `docs/PUS_SERVICE_REFERENCE.md` L509, `mcs/role_analysis/ttc_role.md` L61 | `44 00` → `tx_fwd_power == 1.0`; `44 02` → `5.0`; `44 03` → S1.8 error `0x0003`; running `ttc_reduce_power` measurably lowers `tx_fwd_power`; `68` removed from the pinned divergence set | **S** | WP-0 |
| **WP-3** | Re-encode the payload engineering-unit arguments (36/35/34/39/33/31) | `service_dispatch.py` `_route_payload_cmd`, `payload_basic.py`, `tc_catalog.yaml` | Each command's `expected_data_bytes` equals the handler's max touched offset; `24 51 4F` (−65 °C) → S1.8/0x0003 with the range in the message; `33` rejects `mask == 0` | **M** | WP-0 |
| **WP-4** | MCS builder: typed fields, live hex, reverse decode | `packages/smo-mcs/src/smo_mcs/static/index.html` (`catalogFieldDefs`, `catalogFieldToBuilderField`, `updateStructuredFields`, `packPusFields`, `PUS_FIELD_DEFS['8.1']`), `tests/test_mcs/test_mcs_page_behaviour.py`, `tests/test_mcs/js/mcs_harness.js` | Selecting func 36 renders a `°C` number input, not a hex box; typing `-15` shows `RAW ▸ 24 64 D7`; pasting `24 64 D7` back-populates `-15.00`; no S8.1 command renders `type:'hex_bytes'` | **L** | WP-0, WP-3 |
| **WP-5** | Schema-driven quick commands + payload console controls | `index.html` L2060–2160 and the Payload tab (L1776–1815), `tc_catalog.yaml` `quick_commands:` | Every `.quick-cmd` carries `data-cmd`/`data-args`, no `data-d` literals; a test asserts each button's encoded hex matches `encode()`; payload group has ≥ 12 buttons covering the textbook's ops-card command list | **M** | WP-0, WP-4 |
| **WP-6** | Cross-encoder agreement test + tightened dispatcher scan | new `tests/test_content/test_command_encoders_agree.py`; `tests/test_mcs/test_quick_commands.py` (equality assertion, pinned set → `{65}`) | 20-command table encodes byte-identically through builder / runner / OBCP / planner / S11; equality assertion green | **M** | WP-1, WP-2, WP-3 |
| **WP-7** | Expose the inert payload settings | `tc_catalog.yaml` (new funcs — allocate from the free block after 39; extend `positions.yaml payload_ops.allowed_func_ids`), `service_dispatch.py`, `payload_basic.py`, `telemetry/parameters.yaml` (real params for gain / per-band integration / cooler setpoint / shutter / filter, replacing the `stub:` entries), `subsystems/payload.yaml` (`num_memory_segments: 8` made explicit) | `PAYLOAD_SELECT_FILTER`, `PAYLOAD_CYCLE_SHUTTER`, `PAYLOAD_INITIATE_TRANSFER`, `PAYLOAD_CAL_LAMP` reachable and verified by TM; `S2_PAYLOAD_*` entries added and service 2 granted to `payload_ops` | **M** | WP-0, WP-3 |
| **WP-8** | Make gain / integration time / lines physically real | `payload_basic.py` (SNR ∝ f(gain, t_int); `size_mb` from `lines × bands`), `tests/test_simulator/test_payload_enhanced.py` | Doubling integration time raises band SNR by ~3 dB; halving `lines` halves stored `size_mb`; `PAYLOAD_SET_GAIN` changes reported SNR | **M** | WP-1, WP-3 |
| **WP-9** | Decide and unify the FPA thermal envelope | `subsystems/payload.yaml`, `mcs/limits.yaml` L90, `monitoring/s12_definitions.yaml` L310, `tc_catalog.yaml`, textbook §6.5/§6.7/§13.2/COM-010, `cdf/docs/PART2_SIM_RECONCILIATION.md` L325 | One number appears in all six places; `payload_calibration.md`/`first_light.md` preconditions match the commandable range | **S** | WP-3 |
| **WP-10** | Rewrite the payload procedures onto real commands | `procedures/commissioning/{payload_calibration,first_light,fpa_cooler_activation,payload_power_on}.md`, `procedures/nominal/imaging_session.md`, `procedures/contingency/{corrupted_image,payload_anomaly,memory_segment_failure}.md` | No `SET_PARAM`/`GET_PARAM` against undefined payload param ids; no S20 subtype-3 labelled "SET"; `tools/procedure_audit.py` passes with the new mnemonic/func_id cross-check | **M** | WP-1, WP-3, WP-7 |
| **WP-11** | Upgrade XTCE/SMDL generators to conformant output | `tools/generate_xtce.py`, `tools/xtce_smdl_common.py`, `tools/generate_smdl.py`, `configs/eosat1/xtce/eosat1.xml`, `configs/eosat1/smdl/*.xml` | `MetaCommand`s carry `CommandContainer` + `FixedValueEntry` func_id, calibrators, `ValidRange`, `EnumerationList`; the `name="True"` bug gone; regeneration is byte-stable in CI | **M** | WP-0, WP-3 |
| **WP-12** | Generated command reference | new `tools/gen_command_reference.py`, `configs/eosat1/manual/09_command_reference.md`, `configs/eosat1/manual/06_payload.md` (new §Commandable settings), CI diff gate | Manual regenerates identically; every catalogue command appears with func_id, argument table, units, ranges, enums, example hex | **M** | WP-0, WP-3 |
| **WP-13** | Textbook + courseware alignment | see next section | Every listed page states the same func_id, argument names, units and example hex as the generated reference | **L** | WP-1, WP-2, WP-3, WP-9, WP-12 |

**Ordering:** WP-0 → (WP-1 ∥ WP-2 ∥ WP-3) → WP-6 → WP-4 → WP-5 → (WP-7 → WP-8) → WP-9 → (WP-10 ∥ WP-11 ∥ WP-12) → WP-13.
WP-0, WP-1, WP-2, WP-6 together are the minimum viable fix for reports (a) and the two named divergences; WP-4 is the minimum for (c); WP-3 + WP-9 for (d); WP-12 + WP-13 for (b).

---

## Doc/textbook updates required

### SpaceCDF (`~/cdf`)

| File | Location | Change |
|---|---|---|
| `docs/course/orbit_north_week3.md` | §6.5 L1020–1032 (Thermal "Commands typically issued" + Editor's note) | `PAYLOAD_COOLER_SETPOINT` encoding + agreed envelope (WP-9) |
| | §6.7 L1147–1155 (Payload Ops "Commands typically issued") | `PAYLOAD_CAPTURE(scene_id, lines)` with byte form; add the missing detector-config commands |
| | §7.7 L1819–1840 (Table 7.6c, S8 payload func table) | add Arguments / Units / Range / Example-hex columns |
| | §7.7 L1881–1904 (Table 7.6f, TT&C func table) | `TTC_SET_TX_POWER` levels 1.0/2.0/5.0 W (delete 8 W) |
| | §7.7 L1916–1946 ("What you will not find") | `IMAGE_ACQUIRE → PAYLOAD_CAPTURE (28)` gains the argument form |
| | §11.9 COM-010 L4280–4310 | `PAYLOAD_COOLER_SETPOINT(setpoint_c=-10)` → new encoding + exact hex |
| | §11.9 COM-011 L4344–4378 | replace hand-waved "acquire dark frame" with `PAYLOAD_CYCLE_SHUTTER` + `PAYLOAD_START_CALIBRATION(mode=DARK)` |
| | §11.9 COM-012 L4380–4463 | "integration times 8/12/16/20 ms" → actual `PAYLOAD_SET_INTEGRATION_TIME` call + hex; "IMAGE_ACQUIRE, target ID LAB-CURR-01" → `PAYLOAD_CAPTURE(scene_id=…, lines=…)` |
| | §14.2 card `[PE] COM-009 — Payload Power-On` | command column |
| | §14.2 card `[SE] COM-010 — FPA Cooler Activation`, **step 3 at L5490** | `PAYLOAD_COOLER_SETPOINT(setpoint_c=-10)` → `setpoint = −10.00 °C ▸ 24 66 CB`; "Within [−20, 0] range" → agreed envelope |
| | §14.2 card `[PE] COM-011 — Payload Calibration` | real command sequence |
| | §14.2 card `[MULTI] COM-012 — First Light` | real capture command + args |
| | §14.3 card `[PE] NOM-002 — Imaging Session` | add `PAYLOAD_CAPTURE` with arguments |
| | §14.4 contingency cards (corrupted image, memory segment) | `PAYLOAD_CAPTURE` func **28** not 22; segment range 0–7 |
| | §15.5 L7048–7064 (Payload command table) and the TT&C block below it | regenerate from the catalogue with argument columns |
| | §16.5 Thermal case study L7262, §16.7 Payload case study L7325 | setpoint/encoding consistency |
| `docs/course/orbit_north_week3_workbook.md` | L548 | `STAR_TRACKER_ENABLE, S8 func_id 36` → `ST1_POWER (4)`. **36 is PAYLOAD_COOLER_SETPOINT** — a student following this workbook commands the FPA cooler while trying to enable a star tracker |
| `docs/course/learner_workbook_expanded.md` | payload/thermal exercise pages | add a raw↔engineering encoding exercise (this is the natural home for the −65 °C worked example) |
| `docs/course/facilitator_book_expanded.md` | ops/commanding sections | facilitator notes on the encoding rule and the reverse-decode drill |
| `docs/orbit_north/week3/student_procedure_quickref.md` | L38 (`OBC_BOOT_APP … func_id 42`) and every generated card | func_id 42 → **55**; regenerate cards against the corrected catalogue |
| `docs/orbit_north/week3/_work/sot_index.md` | L183, L203, L396, L402, L688, L1680 | payload/TT&C command lists, TX power levels, the `SET_FPA_COOLER(func 36)` confusion |
| `docs/orbit_north/week3/student_tool_reference.md` | commanding section | document the new typed builder + hex preview + reverse decode |
| `docs/PART2_SIM_RECONCILIATION.md` | L325–341 (FPA setpoint), L468–485 (func_id mis-mappings) | close out with the decisions from WP-9 / WP-1 / WP-2 |
| `docs/SIM_SWEEP_REPORT.md` | Task 5 list (11 scenario func_id mismatches) | resolve rather than "list for review" |

### SMO repo (`~/smo`)

| File | Change |
|---|---|
| `configs/eosat1/manual/09_command_reference.md` | §2.2 Payload has **one** command (`PAYLOAD_SET_MODE`) out of 14 — regenerate the whole document (WP-12); §2.4 TT&C has 2 of 17 |
| `configs/eosat1/manual/06_payload.md` | add "§ Commandable settings" reference table (the table above) — currently zero command coverage |
| `configs/eosat1/manual/commissioning_sequence_walkthrough.md` | L695 `PAYLOAD_CAPTURE(lat, lon)` → `(scene_id, lines)`; L678/L734 setpoint |
| `configs/eosat1/manual/03_tcs.md`, `05_ttc.md` | TX-power levels; FPA vs heater setpoint distinction |
| `configs/eosat1/mcs/role_analysis/payload_ops_role.md` | L48–55 whole payload table is at func **20–26**; rewrite to 26–39 + arguments |
| `configs/eosat1/mcs/role_analysis/ttc_role.md` | L61 func 55 → 68; watt table |
| `configs/eosat1/mcs/role_analysis/eps_tcs_role.md` | L73 `HEATER_SET_SETPOINT` func 34 → 44 |
| `configs/eosat1/procedures/commissioning/payload_calibration.md` | remove `SET_PARAM(0x0637/0x0640/0x0641)` and the `GET_PARAM(0x0631/0x0632/0x0642/0x0643)` chain; use S8 commands; fix Subtype 3 = GET |
| `configs/eosat1/procedures/commissioning/first_light.md` | same (`SET_PARAM(0x0630/0x0634/0x0637)`) |
| `configs/eosat1/procedures/commissioning/fpa_cooler_activation.md` | L73/L102 `GET_PARAM(0x0621)` → `0x0631`; `SET_PARAM(0x0622, …)` (cooler gain) does not exist |
| `configs/eosat1/procedures/nominal/imaging_session.md` | add the capture step (today it commands IMAGING mode and waits for `image_count` to move on its own) |
| `configs/eosat1/procedures/contingency/corrupted_image.md` | L41/L111 `PAYLOAD_CAPTURE` func **22** → 28, `(scene_id, lines)` |
| `configs/eosat1/procedures/contingency/battery_cell_failure.md` | L45 `HEATER_SET_SETPOINT` func 34 → 44 |
| `configs/eosat1/procedures/contingency/memory_segment_failure.md` | segment range 0–7 |
| `configs/eosat1/procedures/contingency/thermal_exceedance.md` | L63 `SET_PARAM(param_id=tcs.htr_<zone>_setpoint)` — symbolic id, no such parameter |
| `configs/eosat1/fdir/procedures/{ttc_reduce_power,ttc_antenna_switch,load_shed_stage3}.yaml` | TX-power bytes (WP-2) |
| `configs/eosat1/obcp/03_payload_checkout.yaml` | capture step `data_hex` → new form |
| `configs/eosat1/planning/activity_types.yaml` | `imaging_pass` capture step gains `args:` |
| `docs/PUS_SERVICE_REFERENCE.md` | L454 (capture scene_id/lines), L462 (cooler setpoint), L509 (TX power), L574/L1091/L1232 examples |
| `docs/OPERATIONS_GUIDE.md` | L327, L335, L865, L1504, L1571 |
| `docs/ops_research/payload_requirements.md` | L193 `PAYLOAD_CAPTURE … func_id 22` → 28 |
| `docs/ops_research/eps_tcs_requirements.md` | L131 `HEATER_SET_SETPOINT 34` → 44 |
| `docs/EOSAT1_Spacecraft_User_Manual.docx`, `docs/EOSAT1_Commissioning_Procedures.{html,docx}` | regenerate from the corrected markdown; the HTML alone carries eight `func_id 28` references, several of which are OBDH/memory steps that have nothing to do with PAYLOAD_CAPTURE |agentId: a4c72aca31b343a48 (use SendMessage with to: 'a4c72aca31b343a48', summary: '<5-10 word recap>' to continue this agent)
<usage>subagent_tokens: 266908
tool_uses: 91
duration_ms: 767415</usage>
