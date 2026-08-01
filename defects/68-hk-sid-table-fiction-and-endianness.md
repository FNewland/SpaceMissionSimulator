# Defect 68 — The HK Manager's SID table was a hardcoded fiction, and every Enable/Disable/Request it sent addressed the wrong SID

## Summary

### (a) The correctness bug: SIDs were byte-swapped on the wire

```js
// index.html, old
function sidToHex(sid) {
  return ('00' + sid.toString(16)).slice(-2) + ' ' + ('00' + (sid >> 8).toString(16)).slice(-2);
}
```

For `sid = 1` this produced `"01 00"` — **low byte first**. The spacecraft reads
the SID big-endian (`struct.unpack('>H', data[:2])` in
`service_dispatch._handle_s3`, for subtypes 5, 6, 27 and 31 alike), so `"01 00"`
decodes as **SID 256**.

Consequently **every** Enable, Disable and Request button in the HK Manager
targeted a SID the spacecraft has never heard of. The command was *accepted*
(S3.5/6/27 are all valid subtypes), so S1 verification came back clean:
`set_hk_enabled(256, …)` merely added a junk key to the engine's `_hk_enabled`
dict, and an S3.27 for SID 256 found no structure so `build_hk_packet()`
returned `None` and no packet was sent. Silent, verified, total no-op. The same
applied to `hkRequestAll()`, `hkEnableAll()` and `hkDisableAll()`.

The hand-written quick-command buttons were always right (`data-d="00 01"` for
`HK_REQ_EPS`), which is why the discrepancy was never obvious.

### (b) The table itself was wrong in every column of every row

```js
const hkSids = [
  { sid: 1, name: 'EPS Summary',      interval: 4,  params: 8,  enabled: true },
  …
  { sid: 7, name: 'EPS Power Lines',  interval: 10, params: 10, enabled: true },
];
```

against the authoritative `configs/eosat1/telemetry/hk_structures.yaml`:

| SID | Config | UI claimed | |
|---|---|---|---|
| 1 | EPS, **1.0 s**, **51** params | EPS Summary, 4 s, 8 | wrong interval (4×), wrong count |
| 2 | AOCS, 4.0 s, **71** | AOCS Summary, 4 s, 12 | wrong count |
| 3 | TCS, **60.0 s**, **17** | TCS Summary, 10 s, 6 | wrong interval (6×), wrong count |
| 4 | **Platform**, **8.0 s**, **26** | **TTC** Summary, 4 s, 8 | **wrong subsystem name** |
| 5 | Payload, **8.0 s**, **23** | Payload Summary, 10 s, 7 | wrong interval, wrong count |
| 6 | **TTC**, **8.0 s**, **27** | **OBDH** Summary, 4 s, 9 | **wrong subsystem name** |
| 7 | — does not exist — | EPS Power Lines, 10 s, 10 | **phantom SID** |
| **11** | **Beacon**, 10.0 s, 6 params, **APID 2** | **absent** | **missing SID** |

So SIDs **4 and 6 had their names swapped** (an operator told to "check SID 4,
the OBDH packet" was shown "TTC"); **SID 7 was invented**; and **SID 11, the
bootloader beacon — the spacecraft's only signal of life during LEOP and after
any OBC reboot — was not listed at all**, so it could not be seen, requested or
diagnosed from the console.

### (c) The enable LEDs were decorative

`enabled` was pure client-side state: `hkToggle()` flipped `s.enabled` locally
and re-rendered. Nothing in the WebSocket or state stream ever reported the
spacecraft's real HK enable bits, so the LEDs showed what the browser had
assumed, never what the spacecraft was doing. Every row started green forever.

## Severity

**Critical (silent wrong commanding) + Major (fictitious display).** (a) is a
correctness bug: the operator believes they enabled housekeeping and the
verification report agrees, while nothing happened. (b) makes the panel actively
misleading about the spacecraft's telemetry configuration.

## Status

**Fixed.**

1. **`sidToHex()` is big-endian.** `sidToHex(1) === "00 01"`,
   `sidToHex(11) === "00 0B"`, `sidToHex(256) === "01 00"`. Regression-tested.
2. **New `GET /api/hk-structures`.** The MCS already loads `hk_structures.yaml`
   (it needs it to decommutate HK), so the endpoint serves `sid`, `name`,
   `interval_s`, `param_count`, `apid`, `readonly` and a `note` per SID, plus
   the mission's `spacecraft_apid` / `bootloader_apid` from `mission.yaml`. The
   hardcoded `hkSids` array is deleted and the table is rendered from the
   response — which fixes the swapped names, the phantom SID 7, the missing
   SID 11 and every wrong interval and count in one move, permanently.
3. **An APID column.** SID 11 shows `0x002` (bootloader), everything else
   `0x001` (application), so the table doubles as a bootloader-vs-application
   diagnostic.
4. **SID 11 is a first-class, read-only row.** Enable/Disable is rendered
   `disabled` with the explanatory tooltip
   *"Bootloader beacon on APID 0x002 — the only telemetry available while the
   OBC runs the boot image. Enable/disable is inhibited from the console;
   one-shot Request (S3.27) remains available."* **Request stays live**, because
   the LEOP procedures explicitly call for a one-shot beacon request.
5. **State is observed, not assumed** (defect 68c / audit Q7a). The server
   timestamps every decoded SID (`_sid_last_seen`, updated in `_process_tm`
   whether or not the parameters decode) and reports `last_seen_age_s` per SID
   both on the endpoint and in the 1 Hz state broadcast (`hk_sid_age_s`). The
   table shows `PERIODIC` when packets are arriving within 2.5 × the configured
   interval, `SILENT` when they have stopped, `NEVER SEEN` before first
   contact, plus the age. `hkToggle()` no longer fakes a local flag: it sends
   the command and the state column changes only when the *spacecraft's*
   behaviour changes, so an Enable/Disable that does not round-trip is visible.
6. **Ready for an authoritative feed.** If the simulator ever publishes its real
   `hk_enabled` map on its state endpoint, `_ingest_reported_hk_enabled()`
   absorbs it (mapping or list form, string or int keys, garbage ignored) and
   the table prefers it — showing `DISABLED`, `ENABLED · PERIODIC`, or the
   discrepancy state `ENABLED · NOT ARRIVING`. Until then `enabled` is `null`
   and the display is honest about inferring.
7. `hkDisableAll()` now confirms first, and skips read-only SIDs.

## Acceptance criteria

- `/api/hk-structures` returns exactly the SIDs in `hk_structures.yaml` — no
  phantom SID 7, no missing SID 11 — with matching names, intervals and
  parameter counts (asserted against the YAML, so the two cannot diverge again).
- SID 4 is `Platform`; SID 6 is `TTC`.
- SID 11 is `readonly`, `apid == bootloader_apid == 2`, 6 params, 10 s, and its
  row renders `hkRequest(11)` but not `hkToggle(11)`.
- `sidToHex` is big-endian for 1, 2, 6, 11, 256 and 0x1234.
- `hkToggle(1)` sends `S3.6` with data `00 01` and leaves the observed state
  untouched.

## Affected areas

- `packages/smo-mcs/src/smo_mcs/server.py` — `_handle_hk_structures`,
  `_hk_sid_meta`, `_sid_last_seen`, `_sid_enabled_reported`,
  `_ingest_reported_hk_enabled`, `BOOTLOADER_BEACON_SID`, `hk_sid_age_s` /
  `hk_enabled` in the state broadcast, the route registration
- `packages/smo-mcs/src/smo_mcs/static/index.html` — `loadHkStructures`,
  `hkSidState`, `renderHkTable`, `updateHkObservedState`, `hkToggle`,
  `hkRequest*`, `hkEnableAll`, `hkDisableAll`, `sidToHex`, the table header
  (APID and Last-seen columns) and the explanatory footnote
- Tests: `tests/test_mcs/test_hk_structures_api.py`,
  `tests/test_mcs/test_mcs_page_behaviour.py::TestSidEndianness`,
  `::TestHkManagerTable`

## Hand-off — simulator lane

Not changed here (outside this lane), still open:

1. **Default HK enable state** (audit Q7a). `engine.py` sets
   `self._hk_enabled[sid] = True` for every SID at config load and re-enables
   all of them on every application boot, and `hk_structures.yaml` has no
   `enabled:` key. `commissioning_sequence_walkthrough.md` §5.1 instructs the
   operator to `HK_ENABLE` all six SIDs as a distinct commissioning step — a
   no-op against this build. The MCS side is now truthful either way, because it
   reports observed behaviour; making §5.1 a real step needs the config-driven
   default on the simulator side.
2. **`HK_DISABLE(sid=11)` in bootloader is accepted and silences the beacon.**
   The acceptance gate comments claim "the dispatcher will reject anything that
   isn't SID 11 via normal validation", but `_handle_s3` performs **no SID
   validation whatsoever**. The console now inhibits it; the spacecraft should
   too (return S1.2 with `0x0006` for S3.6 on SID 11 while
   `spacecraft_phase <= 3`).
3. **Manual/config disagreement on the beacon.**
   `commissioning_sequence_walkthrough.md:44` says "7 parameters, every 30 s";
   `09_leop.md:144` says 30 s and `:159` implies 100 s. The config says
   **6 parameters at 10.0 s**, which is what the MCS now displays.

## Related

- Defect #64 — the TM PACKETS SID column consumes the same SID definitions.
- Defect #67 — the sibling "stop hardcoding what the configuration already
  says" fix in the command builder.
