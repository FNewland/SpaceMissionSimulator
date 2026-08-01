# MCS Operator Displays — Audit (EO-Sat-1 simulator suite)

**Date:** 2026-07-28 · **Mode:** read-only investigation · **Repo:** `/home/claude/repos/SpaceMissionSimulation`

Primary artefacts audited:

| File | Lines | Role |
|---|---|---|
| `packages/smo-mcs/src/smo_mcs/static/index.html` | 6295 | the whole MCS operator webapp (HTML + CSS + all JS inline) |
| `packages/smo-mcs/src/smo_mcs/static/displays.js` | 467 | `DisplayPanels` class — **never loaded by index.html** |
| `packages/smo-mcs/src/smo_mcs/server.py` | 2412 | aiohttp server, TM socket, param cache, state broadcast, REST API |
| `packages/smo-mcs/src/smo_mcs/tm_processor.py` | 146 | HK decommutation |
| `packages/smo-mcs/src/smo_mcs/displays/fdir_alarm_panel.py` | 213 | FDIR panel model — **half-wired** |
| `tools/delayed_tm_viewer.py` | 770 | offline dump viewer |
| `configs/eosat1/telemetry/hk_structures.yaml` | 291 | authoritative HK SID definitions |
| `configs/eosat1/commands/tc_catalog.yaml` | 1363 | 188 command definitions |

Effort key: **S** ≤ ½ day · **M** 1–3 days · **L** > 3 days.

---

## Q1. Scrolling vs latest — EVENTS and TM PACKETS windows

### Verdict: **CONFIRMED DEFECT — neither window can be scrolled back. Both are destroyed and re-created on every packet, resetting scroll to top. History is also silently truncated to the newest 80 rows on screen.**

### Evidence

Both windows are the *same* DOM node, `#log-container`, toggled by a tab flag:

- `index.html:2498` — `<div id="log-container" role="log" aria-live="polite" …></div>`
- `index.html:410` — `#log-container { flex: 1; overflow-y: auto; … }` (so it *is* scrollable — nothing else preserves the position)
- `index.html:3060` — `log-container` is shown for `bottomTab === 'events' || bottomTab === 'tm'`

Render path (`index.html:3238-3262`):

```js
function renderBottomLog() {
  const container = document.getElementById('log-container');
  let entries = bottomTab === 'events' ? eventLog : tmLog;
  …
  let html = '';
  const show = entries.slice(0, 80);          // ← hard cap: only newest 80 ever rendered
  for (const e of show) { html += `<div class="log-entry">…</div>`; }
  container.innerHTML = html;                 // ← full replace ⇒ scrollTop resets to 0
}
```

Trigger frequency and ordering:

- `index.html:3188-3236` `handleTMPacket()` — called for **every** TM packet from the WS (`index.html:3141`). It `unshift()`s into `tmLog` (newest-first, `:3193`), optionally into `eventLog` (`:3201`, `:3217`, `:3229`), then calls `renderBottomLog()` unconditionally at `:3235`.
- `index.html:3553-3558` `addEventLog()` — every locally generated log line also `unshift()`s and calls `renderBottomLog()`.
- Because entries are newest-first and the container is re-filled from scratch, the browser resets `scrollTop` to 0 on each render, i.e. **it always jumps to latest**. There is no way to hold a position: the sim emits SID 1 HK at 1 Hz (`hk_structures.yaml:4`) plus S1/S5/S12 traffic, so a re-render happens several times per second.

Ring buffers (in-memory, larger than what is shown):

- `index.html:3194` — `if (tmLog.length > 200) tmLog.length = 200;`
- `index.html:3206` / `:3556` — `if (eventLog.length > 300) eventLog.length = 300;`
- but only `entries.slice(0, 80)` is ever rendered (`:3253`), so 120 TM rows and 220 event rows exist in memory and are **unreachable from the UI**.

Filtering makes it worse: `eventSevFilter` is applied *after* slicing the log but *before* the 80-row cut (`:3242-3248`), so a narrow filter shows the newest 80 *matching* rows and still cannot be scrolled.

There is exactly one place in the whole app that touches scroll — the procedure log at `index.html:5671` (`logEl.scrollTop = logEl.scrollHeight;`) — proving the pattern was known but never applied to the operator log.

Same class of bug in the per-subsystem event panes (`index.html:2694-2704` `renderSubsystemEvents()` → `el.innerHTML = …`, containers `.subsys-events { max-height:140px; overflow-y:auto }` at `:589`). These are re-rendered from `updateUI()` at 1 Hz (`:4058`, `:4145`, etc.), so the EPS/TCS/AOCS/TTC/PAYLOAD/OBDH "EVENTS" boxes are equally unscrollable, and are additionally capped at `.slice(-30)` (`:2702`) out of a 100-entry buffer (`:2679`).

### Proposed fix (effort: **S** for scroll-lock; **M** with virtualised history)

1. **Pause-on-scroll + jump-to-latest.** In `renderBottomLog()`:
   - before the write, capture `const atTop = container.scrollTop <= 2;` (newest-first list ⇒ "latest" is the top);
   - after the write, `if (atTop) container.scrollTop = 0; else container.scrollTop = prevTop + heightDelta;`
   - when `!atTop`, set a `logPaused` flag, stop re-rendering entirely (just increment a counter) and show a sticky "▲ N newer — jump to latest" chip in `.bp-hdr`; clicking it clears the flag and re-renders.
   This alone fixes the reported symptom and is ~25 lines.
2. **Incremental DOM instead of `innerHTML` replace.** Keep the existing nodes and `container.prepend(newRow)` for each new entry, trimming the tail with `while (container.children.length > RENDER_CAP) container.lastChild.remove()`. Removes the reflow storm at 1 Hz+ and makes (1) trivial.
3. **Raise the render cap to the buffer size.** `slice(0, 80)` → render the full 300/200 entries (cheap with (2)), and raise the ring buffers to `EVENT_RING = 2000`, `TM_RING = 2000` — the MCS already archives events server-side (`server.py:792` `self._archive.store_event(...)`), so the client buffer is only a working window.
4. **Recommended sizes:** `tmLog` 2000 (≈ 10 min of dense traffic), `eventLog` 2000, per-subsystem 500 with the same scroll-lock treatment applied to `renderSubsystemEvents()`.
5. Optional (**M**): add a "Load older from archive" button wired to the existing `GET /api/archive/events` route (`server.py:566`) so operators can page back beyond the client ring.

---

## Q2. SIDs / RIDs in the TM PACKET window

### Verdict: **The data is already on the wire and already in the WebSocket message — the UI just doesn't print it. SID for HK and event-ID (RID) for S5 are both one-line additions. Verdict: FEASIBLE, effort S.**

### What the packet list shows today

`index.html:3193` — the entire TM PACKETS row is a single formatted string:

```js
tmLog.unshift({ ts, text: `S${pkt.service}.${pkt.subtype} APID=${pkt.apid} SEQ=${pkt.seq} (${pkt.data_len}B)` });
```

so the columns are: **timestamp · service.subtype · APID · sequence count · data-field length**. Nothing else. Rendering is the `e.sev == null` branch at `index.html:3258`.

### Is SID available?

**Yes, twice over.**

- On the wire: `smo-common/src/smo_common/telemetry/tm_builder.py:57` — `data = struct.pack('>H', sid)` is the first field of every S3.25 data field.
- Decoded MCS-side: `tm_processor.py:44-68` `_process_hk()` unpacks `sid = struct.unpack('>H', data[:2])[0]` and returns it.
- Already forwarded to the browser: `server.py:679-702` puts it in the WS payload —
  ```python
  info["hk_decoded"] = {"sid": decoded.get("sid"), "param_count": len(decoded["params"])}
  ```
  and the unknown-SID branch sets `{"sid": …, "param_count": 0, "unknown_sid": True}`.
  The broadcast is `server.py:885` `json.dumps({"type": "tm", "packet": info})`.

So `pkt.hk_decoded.sid` is sitting unused in the client right now.

### Is RID available?

In ECSS-E-ST-70-41C the S5 event report carries an **RID / event-definition ID**; this build calls it `event_id`.

- On the wire: `tm_builder.py:72` — `data = struct.pack('>HB', event_id, severity)`; subtype = severity.
- MCS-side: `server.py:758-774` parses it into `info["event"] = {"event_id":…, "severity":…, "description":…}`.
- The client already uses it — but only to build a *text* string for the EVENTS tab (`index.html:3200`: `` `EVT-${pkt.event.event_id}: …` ``). The TM PACKETS row never mentions it.
- Catalogue for name resolution exists: `configs/eosat1/events/event_catalog.yaml` (110 entries, e.g. `:3` `{ id: 0x0001, name: AOS, … }`).

Two adjacent "report-ID-like" fields are also already forwarded and unused in the TM tab:

- S12 monitoring transition: `server.py:781-785` → `info.monitoring.param_id` (the monitored parameter ID);
- S20.2 parameter report: `server.py:716` → `info.s20_param.id`;
- S1 verification: `server.py:732-737` → `info.verification.tc_seq` (the TC request ID being reported on).

### What it takes to add columns (effort **S**, ~40 lines, client-only)

1. Change `tmLog` entries from `{ts, text}` to structured `{ts, svc, sub, apid, seq, len, sid, rid, ridName, ool}` in `handleTMPacket()` (`index.html:3188`).
2. Derive the ID column per service:
   - S3.25 → `SID ${pkt.hk_decoded.sid}` (+ a red `UNKNOWN` chip when `hk_decoded.unknown_sid`, which today is silently swallowed);
   - S5.x → `RID 0x${event_id.toString(16).padStart(4,'0')}`;
   - S12.9/.10 → `PID 0x…` from `info.monitoring.param_id`;
   - S20.2 → `PID 0x…`; S1.x → `TC SEQ …`.
3. Render as real table cells rather than one `<span class="log-msg">` — the log CSS already has fixed-width cells (`index.html:415-416` `.log-ts`, `.log-sev`), so add `.log-id { width:78px }` and `.log-name { flex:1 }`.
4. Optional name column: fetch `event_catalog.yaml` names once. There is **no** existing MCS endpoint that serves the event catalogue (`server.py:512-576` route table has `/api/tc-catalog` and `/api/param-catalog` but nothing for events) — add `GET /api/event-catalog` (~10 lines) or ship the id→name map in `/api/client-config` (`server.py:558`). SID names likewise: `hk_structures.yaml` has `name:` for every SID (`:3 EPS`, `:64 AOCS`, `:151 TCS`, `:175 Platform`, `:210 Payload`, `:241 TTC`, `:281 Beacon`) but nothing exposes them over HTTP today.
5. Add a per-column filter in the existing `.bp-hdr` filter slot (`index.html:2487-2496`) — "SID = n" is the single most requested TM-tab filter and becomes trivial once the field is structured.

---

## Q3. FDIR window

### Verdict: **The textbook says the MCS has an FDIR panel; `textbook_vs_sim.md` marks that claim "Impl" — that verdict is WRONG. There is NO FDIR display in the operator UI. A backend panel model and a client-side renderer both exist but are dead code.**

### Textbook claims (with pages)

- `docs/review_2026-07/textbook_inventory.md:63` — "Mission ops — organisation & console | Ch 5 (pp. 305–320); Ch 6 (pp. 321–331); **Ch 7.1–7.5 (pp. 332–339)**; App B (pp. 526–528) … MCS webapp (localhost:8080) with header/tabs/mosaic/procedure panel/**FDIR panel**/commanding panel".
- `docs/review_2026-07/textbook_inventory.md:54` — the FDIR chapter set: **Ch 5.20 §2 (pp. 314–316); Ch 7.5 (pp. 338–339); Ch 12 (pp. 406–411); Ch 15.2–15.4 (pp. 497–499); Figs 5.3 / 15.1**. Content the book expects an operator to work with: 5-level hierarchy, the `NORMAL→MONITOR→DETECT→ISOLATE→RECOVER→EVALUATE` state machine with time windows, **severity ladder 0–4** (4 = safe-mode candidate), **26 S19 event-action rules + S12 monitors**, per-rule enable/disable via S19.4/S19.5, and explicitly "**acknowledge vs clear; manual override with FD authorisation**".
- `docs/review_2026-07/textbook_inventory.md:93` (p. 323) and `:98` (p. 338) — no rule-table swapping; rules live in `monitoring/s12_definitions.yaml` + `monitoring/s19_rules.yaml`.
- `docs/review_2026-07/textbook_inventory.md:114` — "ConOps Editor + **FDIR design** (pp. 318–319)" — note this one is a *SpaceCDF design-tool* exercise, not the ops MCS.
- **The wrong verdict:** `docs/review_2026-07/textbook_vs_sim.md:81` — "MCS console (tabs, positions, procedures panel, **FDIR panel**, commanding, alarms, go/no-go) | 332–335 | **Impl** | `mcs/positions.yaml`, `displays.yaml`, `limits.yaml` exist…". The evidence cited is config files, not UI. See below.
- Related (correct) row: `docs/review_2026-07/textbook_vs_sim.md:72` — FDIR hierarchy "Part … the book's 5-level hierarchy and `fault_propagation.yaml` cascade layer is partially dead config".

### What the MCS actually has

**No FDIR tab.** The tab bar is `index.html:748-761`: OVERVIEW, EPS, TCS, AOCS, TTC, PAYLOAD, OBDH, COMMANDING, PUS SERVICES, PROCEDURES, ON-DEMAND TM, MANUAL. The only "FDIR" strings in 6295 lines are:

- `index.html:742` — an operator **position** named `fdir_systems` in the position dropdown;
- `index.html:2733-2734` — its description block ("FDIR / SYSTEMS ENGINEER");
- `index.html:1818` — the text label "S19 FDIR" inside the OBDH SVG block diagram;
- `index.html:4694-4695` — the FDIR seat in the GO/NO-GO matrix.

So the `fdir_systems` operator has a seat, but **no display for their discipline**.

**Dead renderer.** `static/displays.js:166-247` implements `initFDIRAlarms()` / `renderFDIRAlarms()` / `updateFDIRAlarms()` against `GET /api/displays/fdir-alarms`. `index.html` loads only two scripts — `index.html:8` `svg-world-map.js` and `index.html:9` `chart.umd.min.js` — and `grep -c "displays.js" index.html` = **0**. `displays.js` is never fetched, and `class DisplayPanels` is never instantiated anywhere.

**Half-wired backend.** `displays/fdir_alarm_panel.py` and route `server.py:572` → `_handle_fdir_alarms` (`server.py:2328-2330`) exist and work, but only the alarm half is fed:

- fed: `server.py:814` and `server.py:845` — `self._fdir_alarm_panel.add_alarm(alarm)` for S5 severity ≥ 3 and S12.9 out-of-limits;
- **never called anywhere in the repo**: `FDIRAlarmPanel.update_s12_rules()` (`fdir_alarm_panel.py:126`), `update_s19_rules()` (`:140`), `set_fdir_level()` (`:154`). Consequence: `get_display_data()` (`:159-201`) always returns `s12_monitoring.active_rules = 0`, `s19_event_action.active_rules = 0`, and `fdir_level = "nominal"` / colour green, **regardless of spacecraft state**. The endpoint is actively misleading if anyone wires it up as-is.

**What the operator has instead:** an alarm-journal slide-over (`index.html:593-605` CSS, `:720-722` button, `:5930` `renderAlarmJournal()`) fed by `GET /api/alarms`, plus alarm count badges (`index.html:712-716`) and a contingency banner (`index.html:764-766`, `:3777` `updateContingencyBanner()`). That is an *alarm list*, not an FDIR display: it shows no rule state, no severity ladder, no autonomous-action attribution, no safe-mode state.

### FDIR information that IS available to ground

| Source | Where | Reaches the MCS? |
|---|---|---|
| **S5 events** (110 defined, severity 1–4) | `configs/eosat1/events/event_catalog.yaml` (110 entries); emitted `tm_builder.py:67-75`; parsed `server.py:758-774` | ✅ yes — event id, severity, 32-char text |
| **S12 monitors** — 38 defined | `configs/eosat1/monitoring/s12_definitions.yaml` (38 entries; `:1` `{param_id:257, name:'EPS Battery SoC Warning', low_limit:20.0, high_limit:100.0, severity:'WARNING'}`) | ⚠️ only *transitions*: S12.9/.10 parsed at `server.py:777-785` (param_id + value + direction). The **definition table is never downlinked or displayed** — S12.12 "Report All Monitors" is in the builder (`index.html:3306`) but the sim rejects it: valid S12 subtypes are `{1,2,6,7}` (`engine.py:1179`) |
| **S19 event-action rules** — 26 defined | `configs/eosat1/monitoring/s19_rules.yaml` (26 entries; `:1` `{name:'eps_bus_undervoltage_payload_off', ea_id:1001, event_type:258, action_func_id:10, enabled:True}`) | ⚠️ S19.8 "report all definitions" is implemented (`service_dispatch.py:1425-1434`, replies S19.128 with `ea_id/event_type/action_func_id/enabled` per rule) and the builder can send it (`index.html:3317`) — but **nothing in the MCS decodes S19.128**; `_process_tm` has no S19 branch, so the reply lands in the TM log as an opaque `S19.128 … (NB)` line |
| **Sim FDIR flags / `sc_mode`** | `engine.py:196-201` — `self.sc_mode`, `_fdir_safe_active`, `_fdir_emergency_active`, `_fdir_triggered`; derived each tick by `_update_sc_mode()` (`engine.py:994-997`); constants `engine.py:38-41` (0=SAFE,1=NOMINAL,2=SCIENCE,3=EMERGENCY) | ❌ **not downlinked at all.** `grep -rn "sc_mode" packages/smo-mcs/` finds only the two consumers in `index.html` (`:3784`, `:3843`) — the producer does not exist. `state.sc_mode` is always `undefined`, so `sb-sc-mode` shows `--` forever and `updateContingencyBanner()` silently falls back to `obdh.mode` |
| **`fdir.level` / alarm counts as telemetry** | `configs/eosat1/telemetry/parameters.yaml:341-344` declares `0x0700 fdir.level`, `0x0701 fdir.active_alarms`, `0x0702 fdir.critical_alarms`, `0x0703 fdir.high_alarms` | ❌ **dead config** — `grep -c "0x07" configs/eosat1/telemetry/hk_structures.yaml` = **0**. These four parameters are in no HK SID, so they are never packed, never downlinked, never in `_param_cache` |
| **Load-shed stage** | `parameters.yaml:63` `0x0134 eps.load_shed_stage`; in SID 1 (`hk_structures.yaml:56`) | ✅ yes — and already rendered (`index.html:3733` `updateLoadShedIndicator()`, `:958-967`) |
| **Fault cascade layer** | `configs/eosat1/fdir/fault_propagation.yaml` + 10 auto-procedures under `configs/eosat1/fdir/procedures/` | ❌ nothing surfaces it (also flagged dead in `textbook_vs_sim.md:72`) |

### Concrete design for an FDIR window

Architecture constraint to respect: `index.html` is a single self-contained page whose panes are `<div class="tab-pane" id="pane-XXX">` selected by `switchTab()` (`index.html:2939`), fed by the 1 Hz `state` WS broadcast plus per-tab REST fetches (the PROCEDURES and ON-DEMAND TM tabs are the precedent for REST-fed tabs). `displays/` modules are server-side data assemblers behind `/api/displays/*`.

**Add `pane-fdir` as a 13th tab**, visible to `flight_director` and `fdir_systems` positions (position gating already exists — `configs/eosat1/mcs/positions.yaml`, applied at `index.html:2887` `updatePositionDetail()`).

Layout — five panels, mirroring the existing panel grid style:

1. **FDIR STATE strip** (top, always visible like the load-shed strip at `index.html:958`):
   `SC MODE [SAFE|NOMINAL|SCIENCE|EMERGENCY]` · `FDIR LEVEL 0–4` · `SAFE latched Y/N` · `EMERGENCY latched Y/N` · `LOAD SHED L0–L3` · `time since last FDIR action`.
   *Requires a sim-side change:* add `sc_mode`, `fdir_level`, `fdir_safe_active`, `fdir_emergency_active` to HK SID 4 (Platform) using the already-declared `0x0700-0x0703` ids plus one new id for `sc_mode`. That is 5 lines of `hk_structures.yaml` + writing `self.params[0x0700] = …` in `_update_sc_mode()`.
2. **ACTIVE ALARMS table** — parameter · value · limit · severity · source (S5/S12) · age · ACK button. Reuse `FDIRAlarmPanel.get_active_alarms()` (`fdir_alarm_panel.py:89`) which already sorts by severity, and the existing ack route `server.py:575` `/api/displays/alarms/{id}/ack`. Add "ACK vs CLEAR" as two distinct actions per the book (p. 338–339 / Ch 15.2–15.4, pp. 497–499): ACK silences the badge, CLEAR requires the condition to have returned in-limits (i.e. a matching S12.10 back-to-nominal).
3. **S12 MONITOR TABLE** — one row per monitor from `s12_definitions.yaml`: id · parameter · limits · current value (from `state.params`) · in/out of limits · enable state · violation count. Serve the static definitions via a new `GET /api/monitors` (load `s12_definitions.yaml` the same way `load_tc_catalog` is loaded at `server.py:223`); overlay live values from the existing `params` broadcast and OOL state from the S12.9/.10 stream. Row actions: S12.1/S12.2 global enable/disable, S12.7 delete — all already in the builder's `PUS_FIELD_DEFS` (`index.html:3302-3306`).
4. **S19 EVENT-ACTION TABLE** — one row per rule from `s19_rules.yaml`: `ea_id` · name · trigger event (resolved through `event_catalog.yaml`) · action (resolved through `tc_catalog.yaml` `func_id` → name) · enabled · fire count · last fire time. Live enable state comes from decoding the S19.128 report (needs a ~15-line `elif pkt.secondary.service == 19 and pkt.secondary.subtype == 128:` branch in `server.py:_process_tm`, unpacking `>HBBB` per rule per `service_dispatch.py:1428-1431`). Row actions: S19.4 enable / S19.5 disable (already in `PUS_FIELD_DEFS`, `index.html:3315-3316`). Add a "REFRESH (S19.8)" button. **Show the sim's real limitation** the book does not state (`textbook_vs_sim.md:70`): an S19 action is exactly one parameter-less S8 function (`service_dispatch.py:1445-1456`).
5. **FDIR EVENT TIMELINE** — a filtered, *scroll-locked* view (reuse the Q1 fix) of S5 events with severity ≥ 2, annotated with which S19 rule fired in response, so the trainee can read cause → autonomous action → effect. Source is the archive (`server.py:566` `/api/archive/events`) so it survives tab switches.

**Wiring plan (server side):**
- Call `set_fdir_level()` / `update_s12_rules()` / `update_s19_rules()` from `_process_tm` and from a small loader at construction — these three methods exist and are currently uncalled (`fdir_alarm_panel.py:126,140,154`).
- Extend `_handle_fdir_alarms` (`server.py:2328`) to return the rule tables and the FDIR state strip.
- Either load `displays.js` from `index.html` and use its `renderFDIRAlarms()` (`displays.js:178`), or — more consistent with how every other tab in this app is written — implement `updateFDIR()` inline in `index.html` and delete the dead `displays.js`. **Recommend the latter**: `displays.js` also contains four other unused panels (contact schedule, power budget, procedure status, system overview) whose backends `server.py:570-574` are equally orphaned; keeping two divergent display stacks is the root cause of the "Impl" mis-verdict in `textbook_vs_sim.md:81`.

**Effort:** UI panel + REST assembly **M**. Sim-side `sc_mode`/`fdir.*` telemetry **S**. S19.128 decode **S**. Total **M**.

**Documentation correction required:** `docs/review_2026-07/textbook_vs_sim.md:81` should be downgraded from **Impl** to **Part** with the note "procedures panel, commanding, alarms and go/no-go exist; there is no FDIR panel in `index.html` — `displays.js` is never loaded".

---

## Q4. Stale telemetry → blank plots

### Verdict: **CONFIRMED — plots do not blank, do not gap, and do not freeze. They keep appending the last received value once per second forever, drawing a perfectly flat line that is indistinguishable from real steady telemetry. This is the most operationally dangerous defect in this audit.**

### The plotting code

All six charted tabs use one 47-line Chart.js wrapper (`index.html:2616-2666`):

```js
const MAX_CHART_POINTS = 120;                       // :2627  — 2-minute window at 1 Hz
let chartTimeCounter = 0;                           // :2628  — a COUNTER, not a clock
function pushChartData(canvasId, timeLabel, values) {          // :2651
  …
  bufs[i].push({ x: timeLabel, y: values[i] });
  if (bufs[i].length > MAX_CHART_POINTS) bufs[i].shift();
  chart.data.datasets[i].data = bufs[i];
  chart.update('none');
}
```

Call sites (all inside `updateXXX()`, all gated only on "is this tab active"):

| Chart | Line | Value expression |
|---|---|---|
| `eps-chart-soc` / `-power` / `-busv` | `index.html:4046-4054` | `eps.soc_pct \|\| 0`, `eps.power_gen_W \|\| 0`, … |
| `tcs-chart-temps` / `-heaters` | `index.html:4129-4139` | `tcs.temp_obc_C\|\|0`, … |
| `aocs-chart-rates` / `-atterr` | `index.html:4256-4263` | `p(aocs,'rate_roll',0)`, … |
| `ttc-chart-rssi` / `-ber` | `index.html:4766-4773` | `rssi`, `Math.log10(...)` |
| `pld-chart-fpa` | `index.html:4839-4842` | `pld.fpa_temp \|\| 0` |
| `obdh-chart-cpu` | `index.html:4929-4932` | `obdh.cpu_load \|\| 0` |

Driver: `updateUI()` (`index.html:3815-3881`) calls all six updaters, and `updateUI()` fires on every `{"type":"state"}` WS message — broadcast **once per second unconditionally** by `server.py:1020-1045` (`await asyncio.sleep(1.0)` at `:1045`).

### What happens when telemetry stops

`server.py:937-1019` builds the broadcast from `self._param_cache`. That cache is **write-only-on-arrival and never expires**:

```python
self._param_cache[int(pid)] = {"value": float(value), "last_update_ts": now, "sid": …}   # server.py:688-692
```

There is no eviction, no TTL, no null-out anywhere in `server.py`. So at LOS:

1. TM stops arriving; `_last_tm_frame_ts` stops advancing (`server.py:683`).
2. `_state_poll_loop` keeps running at 1 Hz and keeps emitting the **same last-known values** in `data` (`server.py:980-987`).
3. `updateUI()` runs, the active tab's `updateXXX()` runs, `chartTimeCounter++`, and `pushChartData()` appends *another* point with the identical y-value.
4. After 120 s the chart is a **100 % flat line spanning the whole 2-minute window**, with no visual difference from a genuinely constant parameter. `MAX_CHART_POINTS = 120` guarantees all real pre-LOS history is pushed off the left edge within two minutes of the link dropping.

The x-axis makes it worse: `x` is `chartTimeCounter`, a shared global incremented by **whichever tab happens to be active** (`index.html:4047, 4129, 4256, 4766, 4839, 4929`) — not a timestamp. `CHART_OPTS_BASE` sets `x: { type:'linear', display:false }` (`index.html:2620`), so the axis is hidden. There is therefore no way for the operator to tell from the plot how much wall-clock time a segment covers, and a gap in sampling is rendered as a straight interpolating segment between adjacent counter values.

### The freshness signals that already exist (all unused by the plots)

| Signal | Produced at | Consumed at | Used by plots? |
|---|---|---|---|
| `state.stale` (bool, age > 60 s) | `server.py:973-981` | `index.html:3817` — assigned to a local `const stale` and then **never referenced again** | ❌ |
| `state.last_frame_age_s` | `server.py:977-982` | `index.html:3852-3866` — drives only the `sb-conn` text (`NO TM` / `STALE ns` / `LAG ns` / `WS OK`) | ❌ |
| `state.subsystem_staleness{sub: age_s}` | `server.py:993-1005` | `index.html:3941-3951` — flips the six OVERVIEW tab chips to `STALE` | ❌ |
| `state.params_age{pid: age_s}` | `server.py:1009-1012` | `index.html:3667-3700` `isParamStale()` / `paramCell()` — greys individual numeric cells and appends a `STALE` chip | ❌ |
| `state.in_contact` | `server.py:990` `_compute_orbital_state()` | `index.html:3906`, `:4785`, `:5382`, `:6200` | ❌ |
| `lastTmTimestamp` (client) | `index.html:3189` | `index.html:3847-3849` `sb-last-tm` | ❌ |

The staleness infrastructure is complete and correct — **it simply stops at the numeric readouts and never reaches Chart.js.** An operator sees "STALE" on the cell and a healthy-looking trend two inches away.

### Proposed uniform staleness policy

Define one predicate and one gate, used by every chart — implement **once** in `pushChartData()` so no call site can forget it.

```js
// index.html, next to MAX_CHART_POINTS (~:2627)
const TM_FRESH_S = 5;      // < 5 s  → fresh
const TM_STALE_S = 15;     // 5–15 s → degraded (still plot, mark amber)
                           // > 15 s → stale: stop plotting, blank on LLOS
function tmFreshness() {
  const a = state && state.last_frame_age_s;
  if (a == null) return 'none';
  if (a < TM_FRESH_S) return 'fresh';
  if (a < TM_STALE_S) return 'degraded';
  return 'stale';
}
```

Policy (recommend "blank on stale", which is what was asked for):

1. **`fresh`** — plot normally.
2. **`degraded`** — still plot, but tint the chart border/background amber and show a `LAG ns` chip in the panel header.
3. **`stale` or `none`** — **stop appending points entirely** and, per the request, **clear the buffers and the canvas**: empty `chartBuffers[canvasId]`, `chart.data.datasets.forEach(d => d.data = [])`, `chart.update('none')`, and overlay a centred `NO TELEMETRY — LOS 00:42` label on the panel. On the next fresh packet the chart restarts from empty, so the first post-AOS trend is honest.
4. **Never interpolate across a gap.** Even if "freeze" is preferred over "blank" for some parameter, insert a `null` y-value at the stale boundary — Chart.js renders `null` as a break in the line rather than a straight segment.
5. **Per-parameter, not just per-link.** `state.params_age` (`server.py:1009`) already gives per-PID ages; a parameter whose HK SID is disabled or whose subsystem is powered down goes stale while the link is up (the sim suppresses HK from unpowered subsystems at `engine.py:1423-1428` and from bus-failed subsystems at `engine.py:1419-1422`). Use `subsystem_staleness[sub]` as the per-chart gate and `last_frame_age_s` as the global gate; blank if **either** is stale.
6. **Fix the x-axis while you are in there.** Change `pushChartData`'s `timeLabel` from `chartTimeCounter` to `Date.now()` (or the ground-clock value the MCS already computes in `_compute_time_state()`, `server.py:991`), set `x: { type:'time' }` and `display:true`. Then a stale gap is *visible as a gap* rather than being compressed away, and all six charts share a real time base instead of a counter that only advances while their own tab is open.

### Where to implement (single point)

`pushChartData()` at `index.html:2651` is called by all 11 charts and is the correct choke point. Add a `blankChart(canvasId)` helper next to `ensureChart()` (`:2664`) and call the freshness gate at the top of `pushChartData`. Optionally also blank on `updateUI()` entry so charts on *inactive* tabs are cleared too (today they retain a frozen buffer that reappears intact on tab switch, which is its own small lie).

**Effort: S** (gate + blank + chip: ~50 lines, one function). **M** if the time-axis conversion is included (touches all 11 `ensureChart` definitions and `CHART_OPTS_BASE`).

---

## Q5. Delayed telemetry tool — plots do not work

### Verdict: **CONFIRMED, and reproduced by inspection. TWO independent bugs, either of which alone breaks plotting. Both are one-line fixes.**

### Bug 1 (fatal, client) — `ChartDataLabels` is not defined

`tools/delayed_tm_viewer.py:604` and `:702`, in `addChart()` and `plotAllForSid()` respectively:

```js
    plugins: [ChartDataLabels || {}]
```

`ChartDataLabels` is the global exported by `chartjs-plugin-datalabels`. The page loads exactly two scripts (`tools/delayed_tm_viewer.py:255-256`):

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.1.0/dist/chartjs-plugin-zoom.min.js"></script>
```

**`chartjs-plugin-datalabels` is never loaded**, and `ChartDataLabels` appears nowhere else in the file. In JavaScript, `undeclaredIdentifier || {}` does **not** short-circuit to `{}` — evaluating the identifier throws:

```
$ node -e "try{ console.log(ChartDataLabels || {}) }catch(e){ console.log('THROWS:', e.constructor.name, e.message) }"
THROWS: ReferenceError ChartDataLabels is not defined
```

(Only `typeof ChartDataLabels === 'undefined'` is safe against an undeclared binding.)

The throw happens while evaluating the options object passed to `new Chart(...)`, i.e. **before** `chartInstances[chartId] = chart` at `:606` / `:704`. But the DOM insert happens *earlier* — `chartsBody.appendChild(chartItem)` at `:543` / `:654`. So the observable symptom is exactly what was reported: **clicking "Plot" or "Plot All" makes an empty bordered chart box with a title and a ✕ appear, containing a blank canvas, plus an uncaught `ReferenceError` in the console.** Nothing recovers, because there is no `try/catch` around either constructor. `removeChart()` (`:707`) will also leave `chartInstances` inconsistent since the entry was never created.

**Fix (S, 1 line ×2):** delete the `plugins: [ChartDataLabels || {}]` line from both constructors (the plugin is not used — no `datalabels` options are configured anywhere). If data labels are actually wanted, add `<script src=".../chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>` next to `:256` and guard with `typeof ChartDataLabels !== 'undefined' ? [ChartDataLabels] : []`.

### Bug 2 (fatal to the *content* of the plot) — every CUC timestamp is 0

`tools/tm_report.py:150`, in the shared decoder that `delayed_tm_viewer` imports (`delayed_tm_viewer.py:38-42`):

```python
cuc = int(getattr(pkt.secondary, "time", 0) or 0)
```

The PUS secondary header dataclass has **no** `time` attribute — the field is `cuc_time`:

```python
# packages/smo-common/src/smo_common/protocol/ecss_packet.py:84-89
@dataclass
class SecondaryHeader:
    pus_version: int = 2
    service: int = 0
    subtype: int = 0
    cuc_time: int = 0        # ← the real name; unpacked at :103
```

Verified:

```
$ python3 -c "... s=SecondaryHeader(service=3,subtype=25,cuc_time=12345); print(hasattr(s,'time'), getattr(s,'time',0), s.cuc_time)"
has time attr: False getattr: 0
cuc_time: 12345
```

`getattr(..., "time", 0)` therefore silently returns the default **0 for every packet**. Downstream consequences in the viewer:

- `analyse_dumps()` (`delayed_tm_viewer.py:157-187`) builds `series: [[cuc, value], …]` → every x-coordinate is `0`.
- `first_cuc` / `last_cuc` stay `None` (`tm_report.py:151` `if cuc:` is never true) → the "CUC range" summary card shows `— → —` (`delayed_tm_viewer.py:423`).
- The sparkline (`:485-493`) computes `dx = (x1-x0) || 1` = 1 with all points at x=0 → a vertical scribble at the left edge.
- Even once Bug 1 is fixed, `addChart()` labels every point `formatCucTime(0)` (`:528`) → all N labels identical, and the tooltip always says `CUC: 0`.
- S5 events all render with `CUC 0` (`:441`).

**Fix (S, 1 line):** `tools/tm_report.py:150` → `cuc = int(getattr(pkt.secondary, "cuc_time", 0) or 0)`. This also repairs `tools/tm_report.py`'s own HTML output (`:210`, `:283`), which has the same symptom.

### Bug 3 (secondary) — `formatCucTime()` misinterprets the CUC encoding

`delayed_tm_viewer.py:502-508`:

```js
function formatCucTime(cuc) {
  const s = Math.floor(cuc / 256);
  const ms = ((cuc % 256) / 256 * 1000).toFixed(0);       // computed, then discarded
  const date = new Date((s - 657720000) * 1000);
  …
}
```

This assumes a coarse/fine packed CUC (`>H` seconds + sub-second byte). The sim's CUC is **plain seconds since 2000-01-01T12:00:00**, packed as a bare `>I`:

- `ecss_packet.py:93` — `struct.pack('>I', self.cuc_time)`;
- `ecss_packet.py:16` — `TIME_EPOCH = datetime(2000, 1, 1, 12, 0, 0)`;
- `ecss_packet.py:132-134` — `parse_cuc_time(cuc_seconds) = TIME_EPOCH + timedelta(seconds=cuc_seconds)`.

So dividing by 256 under-reports elapsed time by 256×, and the magic constant `657720000` is a hand-rolled offset that does not correspond to `TIME_EPOCH` (J2000 noon is Unix 946_728_000). Once Bug 2 is fixed this produces confidently wrong axis labels. **Fix (S):**

```js
const CUC_EPOCH_UNIX_MS = Date.UTC(2000, 0, 1, 12, 0, 0);   // 946728000000
function formatCucTime(cuc) {
  if (typeof cuc !== 'number') return String(cuc);
  return new Date(CUC_EPOCH_UNIX_MS + cuc * 1000).toISOString().substr(11, 8);
}
```

Also delete the dead `minCuc` locals at `:527` and `:622`, and the unused `getRelativeTime()` (`:510`) / `maxDataPoints` (`:623`).

### Bug 4 (environmental) — the docstring's "fully offline" promise is false

`delayed_tm_viewer.py:12` states "It is fully offline — no CDN, no MCS / simulator dependency at runtime", but `:255-256` load Chart.js and the zoom plugin from `cdn.jsdelivr.net`. On an air-gapped classroom machine `Chart` is `undefined` and **every** plot path fails with a second `ReferenceError`, masking the real bug. The MCS itself already vendors Chart.js — `index.html:9` serves `/static/vendor/js/chart.umd.min.js` from the repo, and `server.py:577-580` mounts the vendor tree. **Fix (S):** either serve the vendored copy from this tool's aiohttp app (`app.router.add_static('/vendor', REPO/'packages/smo-mcs/src/smo_mcs/static/vendor')`) or drop the false claim from the docstring.

### Bug 5 (latent) — series serialised into inline `onclick` attributes

`delayed_tm_viewer.py:449` and `:461` embed `JSON.stringify(p.series)` / `JSON.stringify(sid.params)` into `onclick=` attributes. SID 1 has 51 parameters at 1 Hz (`hk_structures.yaml:2-61`, `interval_s: 1.0`); a multi-pass "Load all" easily makes each `series` thousands of points, so a single `Plot All` attribute can reach megabytes of HTML. Only `"` is escaped (`.replace(/"/g,'&quot;')`) — a parameter name containing `'` or `\` would break the handler. **Fix (M):** keep `chartsData` (already assigned at `:466`) as the source of truth, give buttons `data-sid` / `data-pid` attributes and one delegated listener, and look the series up by id.

### Recommended fix order

1. `tm_report.py:150` `time` → `cuc_time` — **S**, unblocks correct x-values everywhere including `tm_report.py`'s own output.
2. Remove `plugins: [ChartDataLabels || {}]` at `delayed_tm_viewer.py:604` and `:702` — **S**, unblocks rendering.
3. Rewrite `formatCucTime()` against `TIME_EPOCH` — **S**.
4. Vendor Chart.js locally or fix the docstring — **S**.
5. Delegated event handlers instead of inline JSON — **M**.

---

## Q6. Command-builder byte counts

### Verdict: **The metadata is there and is 97 % complete — 182 of 188 commands have a fully derivable data-field length. The builder simply never reads the catalogue: `PUS_FIELD_DEFS` and `FUNC_CATALOG` are hardcoded in `index.html`, and the `/api/tc-catalog` endpoint that would supply the real lengths is never called by any client.**

### Where command definitions live

`configs/eosat1/commands/tc_catalog.yaml` — **188 commands**, loaded by `smo-common/src/smo_common/config/loader.py:129-136` into `TCCommandDef` (`schemas.py:199-211`), which preserves `fields: list[TCFieldDef]`, `func_id`, `position`, `criticality`, `aliases`.

Schema per entry (`tc_catalog.yaml:4-10` is representative):

```yaml
  - name: S2_EPS_BATTERY_HEATER
    service: 2
    subtype: 1
    description: "Control battery heater (device 0x0100)"
    fields:
      - { name: device_id, type: uint16, description: "Device ID (0x0100)" }
      - { name: state,     type: uint8,  description: "0=OFF, 1=ON" }
```

Key coverage counts (computed from the file):

| Key | Present on |
|---|---|
| `name`, `service`, `subtype`, `description` | 188 / 188 |
| `fields` | 186 / 188 |
| `func_id` | 91 (all S8.1 entries) |
| `aliases` | 12 · `position` 10 · `criticality` 9 |

Field types used and their fixed widths: `uint8` ×97 (1 B), `uint16` ×83 (2 B), `float32` ×31 (4 B), `uint32` ×10 (4 B), `int16` ×2 (2 B), `bytes` ×4 (**variable**).

### Is expected data-field length derivable?

**Yes for 182 / 188.** The rule is:

```
expected_data_bytes = sum(width(f.type) for f in cmd.fields)
                    + (1 if cmd.service == 8 and cmd.subtype == 1 else 0)   # the leading func_id byte
```

The `+1` is required because `func_id` is a *sibling key*, not a field — confirmed by the dispatcher, which strips it before routing: `service_dispatch.py:463-494`

```python
func_id = data[0]
if func_id in range(0, 16):    return self._route_aocs_cmd(func_id, data[1:])
```

Derived lengths by service/subtype (all fixed unless noted):

| S.sub | #cmds | data bytes |
|---|---|---|
| 2.1 | 49 | 3 |
| 2.6 | 1 | 2 |
| 3.5 / 3.6 / 3.27 | 3 | 2 |
| 3.31 | 1 | 6 |
| 5.5 / 5.6 | 2 | 2 |
| 6.2 | 5 | 8 / 16 + **variable** (`MEM_LOAD`) |
| 6.5 | 2 | 6, 7 |
| 6.9 | 1 | 7 |
| **8.1** | **91** | **1–21** (see below) |
| 9.1 / 9.2 | 2 | 4 / 0 |
| 11.4 | 1 | 4 + **variable** |
| 11.7 / 11.9 / 11.11 / 11.13 / 11.17 | 5 | 2 / 0 / 0 / 0 / 0 |
| 12.1 / 12.2 / 12.6 / 12.7 | 4 | 0 / 0 / 10 / 2 |
| 13.1 / 13.3 / 13.5 / 13.9 | 4 | 4 / 4 / 2 / 2 |
| 15.1 / .2 / .9 / .11 / .13 | 5 | 1 / 1 / 1 / 1 / 0 |
| 17.1 | 1 | 0 |
| 18.1–18.6 | 5 | **variable** (18.1) / 2 / 3 / 2 / 0 |
| 19.1 / .2 / .4 / .5 | 4 | **variable** (19.1) / 2 / 2 / 2 |
| 20.1 / 20.3 | 2 | 6 / 2 |

S8.1 distribution (data bytes incl. func_id): 1 B ×27 (no-arg funcs), 2 B ×43, 3 B ×6, 5 B ×9, 4 B ×1, 6 B ×1, 10 B ×1, 17 B ×1, 21 B ×1.

### Command families where length is NOT derivable

1. **Variable-length `bytes` payloads — 4 commands** (genuinely unbounded, must be shown as `N + k` or `variable`):
   - `MEM_LOAD` (S6.2) — `addr uint32 + len uint16 + data bytes` → **6 + n**
   - `SCHEDULE_TC` (S11.4) — `exec_time uint32 + tc_data bytes` (a complete embedded CCSDS/PUS TC; see `server.py:1464-1485` `_embed_s11_packet`) → **4 + n**
   - `OBCP_LOAD` (S18.1) — `obcp_blob bytes` → **n**
   - `EA_ADD` (S19.1) — declared `ea_id uint16 + event_type uint8 + action_tc bytes`, but the sim implements it as a **fixed 4 bytes** `>HBB` (`ea_id(2)+event_type(1)+action_func_id(1)`, `service_dispatch.py:1400-1409`). **The catalogue is wrong here** — it describes an embedded TC that the sim never parses.
2. **Missing `fields` key entirely — 2 commands:** `TTC_SWITCH_PRIMARY` and `TTC_SWITCH_REDUNDANT` (funcs 63/64). The handler takes no argument (`service_dispatch.py:479-480` routes with `data[1:]`), so they are almost certainly `fields: []` (1 data byte = func_id) — but the file does not say so, and a length calculator must treat "absent" as "unknown", not "zero".
3. **One verified catalogue-vs-handler length mismatch:**
   `RW_SET_SPEED_BIAS` (func 8), `tc_catalog.yaml:535-542`, declares `wheel_idx uint8 + bias_rpm int16` = **3 data bytes**. The handler requires **5**:
   ```python
   # service_dispatch.py:555-561
   elif func_id == 8:  # RW set speed bias
       if len(data) >= 5:
           wheel = data[0]
           bias  = struct.unpack('>f', data[1:5])[0]   # float32, not int16
   ```
   A 3-byte command is silently swallowed (the `if` simply doesn't fire) while S1 acceptance/completion still report success — a false-positive command. Compare `AOCS_RW_RAMP_DOWN` (func 14, `tc_catalog.yaml:585-592`) which correctly declares `uint8 + float32`.
4. **Catalogue/dispatcher coverage gaps** (not length issues, but they belong in the same clean-up):
   - func **83** (`EPS mode`, defect #27) is dispatched at `service_dispatch.py:485-486` but has **no `tc_catalog.yaml` entry**;
   - func **79** does not exist in either; the range is 0–82 + 84 + 100–107 = 91 func_ids.

### Where "expected N data bytes" should appear in the builder

Today the builder is entirely hand-maintained and catalogue-blind:

- `index.html:3279-3320` — `PUS_FIELD_DEFS`, a hardcoded 42-entry map of `'svc.sub' → {label, fields[]}`. It knows `size:` per field, so it *could* already sum them — but nothing does.
- `index.html:3322-3365` — `FUNC_CATALOG`, a hardcoded list of **79** S8 functions. The catalogue defines **91**. Missing from the dropdown: **42** `HEATER_THRUSTER`, **81** `EPS_DEPLOY_WING`, **82** `EPS_WING_STATUS`, **84** `AOCS_GPS_SET_START_MODE`, and the eight legacy quick-actions **100–107**. Operators literally cannot select those functions from the structured builder.
- `index.html:3293` — the S8.1 entry is `func_id` (a `func_select`) plus `arg` typed `hex_bytes`, i.e. a free-text hex box with placeholder `"07=line7, 00=wheel0"`. **The builder has no idea how many argument bytes any function needs.** This is exactly the reported gap.
- `index.html:3402-3464` `updateStructuredFields()` renders the inputs; `index.html:3466-3479` `packStructuredFields()` writes the hex into `#cmd-data`; `index.html:3496` `sendPUSCommand()` posts it to `/api/pus-command`.
- The server-side catalogue endpoint exists and already returns `fields` — `server.py:525` → `_handle_tc_catalog` (`server.py:1349-1365`) → `{"commands": [ {name, service, subtype, description, fields[], func_id, position, criticality, allowed}, … ]}` — and **`grep -c "tc-catalog" index.html` = 0**. No client has ever called it. (`/catalog`, `server.py:1367`, is a legacy alias for pages that no longer exist.)

### Proposal

**Phase 1 — surface the number (effort S, client-only, ~60 lines).**
Add `expected_bytes` computation over the existing `PUS_FIELD_DEFS` sizes and a hardcoded `FUNC_ARG_BYTES` map, and display it in three places:

1. A live chip next to the data box: **`EXPECTED 2 B · ENTERED 2 B ✓`** (green when equal, amber when short/long, grey `VARIABLE` for the 4 variable-length commands). Update it from the existing `cmd-data` `input` handler (`index.html:3269`) and from `updateStructuredFields()`.
2. Suffix each `FUNC_CATALOG` dropdown option (`index.html:3440`) with the argument size: `28 - PLD Capture Image (4 B arg)`.
3. A soft pre-send check in `sendPUSCommand()` (`index.html:3496`), modelled on the existing subtype `confirm()` at `:3504-3507`: *"S8.1 func 8 expects 5 data bytes, you entered 3. Send anyway?"* — soft, not blocking, because operators legitimately hand-craft malformed TCs during fault-injection training.

**Phase 2 — make it catalogue-driven (effort M, the correct fix).**
1. Extend `/api/tc-catalog` (`server.py:1349`) to compute and return `expected_data_bytes: int | null` and `variable_length: bool` server-side, using the width table above plus the S8 `+1`. One place, Python, unit-testable, and it also serves the planner and any future tooling.
2. On MCS load, `fetch('/api/tc-catalog?position='+currentPosition)` and **generate** `FUNC_CATALOG` and the S8 field layout from it, deleting the hardcoded arrays (`index.html:3322-3365`). This automatically fixes the 12 missing functions, keeps the dropdown in sync with the YAML forever, and lets the builder render *typed per-argument inputs* for S8 (e.g. func 10 `AOCS_SLEW_TO` → four float32 quaternion boxes + one rate box) instead of a raw hex field.
3. Show `description`, `criticality` and the position-access `allowed` flag (already returned at `server.py:1363`) in the dropdown, so a locked-out operator sees *why* before sending.
4. Add a content test that asserts, for every `func_id`, that the catalogue's derived length matches the minimum length the dispatcher checks — this would have caught the `RW_SET_SPEED_BIAS` 3-vs-5 defect. (`docs/review_2026-07/test_strategy.md:130` already runs this style of behavioural probe over `s19_rules.yaml`; the same harness applies.)

**Fix the data first (effort S):** correct `tc_catalog.yaml:542` `bias_rpm` `int16` → `float32`; add `fields: []` to `TTC_SWITCH_PRIMARY`/`TTC_SWITCH_REDUNDANT`; correct `EA_ADD` to `ea_id uint16 + event_type uint8 + action_func_id uint8`; add the missing func 83 entry.

---

## Q7. HK SIDs

### (a) Default enable state — sim vs manual

**Verdict: CONFIRMED DISCREPANCY. The user is right: every HK SID is enabled the moment the config loads, and re-enabled on every application boot. The commissioning manual instructs the operator to enable all six as a distinct commissioning step, which is a no-op against this build.**

Sim side:

```python
# packages/smo-simulator/src/smo_simulator/engine.py:184-190
self._hk_enabled: dict[int, bool] = {}
for hk in …:
    self._hk_structures[hk.sid] = params_list
    self._hk_intervals[hk.sid]  = hk.interval_s
    self._hk_timers[hk.sid]     = 0.0
    self._hk_enabled[hk.sid]    = True        # ← every SID enabled at load
```

and on application boot (bootloader → app, phase 3 → 4):

```python
# engine.py:459-467
for sid in self._hk_enabled:
    self._hk_enabled[sid] = True
…
logger.info("Application booted: APID=0x%03X, all HK SIDs enabled, stores online", …)
```

(repeated at `engine.py:1923-1924`). The only place anything is disabled is bootloader entry, `engine.py:351-353`:

```python
for sid in self._hk_enabled:
    self._hk_enabled[sid] = (sid == BOOTLOADER_BEACON_SID)     # BOOTLOADER_BEACON_SID = 11, engine.py:32
```

`configs/eosat1/telemetry/hk_structures.yaml` has **no `enabled:` key on any structure** — the schema does not model a default-disabled SID at all.

Note the emission gate is *independent* of the enable flag: `engine.py:1417` `if sw_image == 0 and sid not in (10, 11): continue`. So in bootloader, SIDs 1–6 are suppressed by software image regardless of their enable bit, and on app boot they resume unconditionally.

Manual side — `configs/eosat1/manual/commissioning_sequence_walkthrough.md:225-247`:

> ### 5.1 Enable Periodic HK Reporting for All SIDs
> | `HK_ENABLE(sid=1)` (S3.5) | EPS | 1.0 s |
> | `HK_ENABLE(sid=2)` (S3.5) | AOCS | 4.0 s |
> … through sid=6 …
> **MCS screen:** … All subsystem tabs should now show **live updating** values (**no longer requiring manual HK_REQUEST**)

and `commissioning_sequence_walkthrough.md:748` lists "HK_ENABLE ×6" as an FDIR/Systems commissioning task.

The manual's premise — that before Phase 5 the operator must poll with `HK_REQUEST` and that HK_ENABLE is what turns on live telemetry — is false in this build: as soon as `OBC_BOOT_APP` (func 55) completes, all six SIDs stream at their configured intervals. The six S3.5 commands are accepted (`service_dispatch.py:252-255`) and change nothing.

Two smaller manual errors in the same neighbourhood:

- `commissioning_sequence_walkthrough.md:44` — "The beacon SID 11 is the only telemetry available (**7 parameters, every 30 s**)". Config says **6 parameters** (`hk_structures.yaml:283-291`, comment at `:283` explicitly says "6 parameters — matches bootloader emission (canonical per SPACECDF_RECONCILE item 8)") at **10 s** (`hk_structures.yaml:282` `interval_s: 10.0`). `09_leop.md:144` likewise says "every 30 s"; `09_leop.md:159` says "vs. waiting 100 s". Three different beacon periods across the manual, none matching the 10 s in config. (`textbook_vs_sim.md:111` already flags the textbook's 30 s claim.)
- `commissioning_sequence_walkthrough.md:236` labels SID 4 "OBDH"; `hk_structures.yaml:175` names it **`Platform`**. See (b) — the MCS calls it something else again.

**Fix options:**
- *Doc fix (S):* rewrite §5.1 as "verify all six SIDs are already streaming; use HK_ENABLE/HK_DISABLE only to change the configuration" and correct the beacon period/param count everywhere.
- *Sim fix (M, more pedagogically valuable):* add `enabled: false` support to the `hk_structures.yaml` schema, default SIDs 1–6 to disabled after app boot, and make Phase 5.1 a real commissioning step. Requires touching `engine.py:190` and `:459-460` plus the loader schema, and re-baselining any test that assumes immediate HK.

### (b) "We do not see ALL the SIDs"

**Verdict: CONFIRMED, and worse than "missing" — the MCS's SID table is a hardcoded fiction that is wrong in every column for every row.**

The HK Manager table (PUS SERVICES tab, `index.html:2065-2067` `<thead><tr><th>SID</th><th>Name</th><th>Interval</th><th>Params</th><th>Enabled</th><th>Actions</th></tr>`) is driven by a literal array, `index.html:5077-5085`:

```js
const hkSids = [
  { sid: 1, name: 'EPS Summary',      interval: 4,  params: 8,  enabled: true },
  { sid: 2, name: 'AOCS Summary',     interval: 4,  params: 12, enabled: true },
  { sid: 3, name: 'TCS Summary',      interval: 10, params: 6,  enabled: true },
  { sid: 4, name: 'TTC Summary',      interval: 4,  params: 8,  enabled: true },
  { sid: 5, name: 'Payload Summary',  interval: 10, params: 7,  enabled: true },
  { sid: 6, name: 'OBDH Summary',     interval: 4,  params: 9,  enabled: true },
  { sid: 7, name: 'EPS Power Lines',  interval: 10, params: 10, enabled: true },
];
```

Compared with the authoritative `configs/eosat1/telemetry/hk_structures.yaml`:

| SID | Config name : interval : params | UI name : interval : params | Status |
|---|---|---|---|
| 1 | `EPS` : **1.0 s** (`:4`) : **51** | EPS Summary : 4 s : 8 | wrong interval (4×), wrong count |
| 2 | `AOCS` : 4.0 s (`:65`) : **60** | AOCS Summary : 4 s : 12 | interval OK, count wrong |
| 3 | `TCS` : **60.0 s** (`:152`) : **18** | TCS Summary : 10 s : 6 | wrong interval (6×), wrong count |
| 4 | `Platform` : **8.0 s** (`:176`) : **26** | **TTC** Summary : 4 s : 8 | **wrong subsystem name**, wrong interval, wrong count |
| 5 | `Payload` : **8.0 s** (`:211`) : **24** | Payload Summary : 10 s : 7 | wrong interval, wrong count |
| 6 | `TTC` : **8.0 s** (`:242`) : **28** | **OBDH** Summary : 4 s : 9 | **wrong subsystem name**, wrong interval, wrong count |
| 7 | — does not exist — | EPS Power Lines : 10 s : 10 | **phantom SID** |
| **11** | `Beacon` : 10.0 s (`:282`) : **6** | **absent** | **missing SID** |
| 10 | removed by design (`hk_structures.yaml:275-278`) | absent | correct |

So: **SIDs 4 and 6 have their names swapped** (an operator asked to "check SID 4, the OBDH packet" is told it is TTC), **SID 7 is invented** — pressing Enable/Request on it sends a command for a SID the spacecraft has never heard of — and **SID 11 (Beacon), the single most important SID during LEOP and after any OBC reboot, is not listed at all**. Every interval and every parameter count is wrong. The `enabled` column is client-side state only (`hkToggle()` flips `s.enabled` locally at `index.html:5106`); nothing in the WS/state stream ever reports the spacecraft's real HK enable bits, so the LEDs are decorative.

**Additional confirmed bug in the same panel — the SID is byte-swapped on the wire.** `index.html:5130-5132`:

```js
function sidToHex(sid) {
  return ('00' + sid.toString(16)).slice(-2) + ' ' + ('00' + (sid >> 8).toString(16)).slice(-2);
}
```

For `sid = 1` this yields `"01 00"` — low byte first. The spacecraft reads the SID big-endian: `service_dispatch.py:254` `sid = struct.unpack('>H', data[:2])[0]` (likewise `:241`, `:246`, `:262`). `"01 00"` therefore decodes as **SID 256**, so **every Enable / Disable / Request button in the HK Manager targets a non-existent SID.** The command is accepted (S3 subtypes 5/6/27 are all valid, `engine.py:1173`) and silently does nothing — `set_hk_enabled(256, …)` just adds a junk key to `_hk_enabled` (`engine.py:2457-2458`), and S3.27 for SID 256 finds no structure so `build_hk_packet` returns `None` (`tm_builder.py:54-55`) and no packet is sent. Contrast the hand-written quick-command buttons, which are correct: `index.html:2018` `data-d="00 01"` for `HK_REQ_EPS`. **Also affects** `hkRequestAll()` (`:5116`), `hkEnableAll()` (`:5120`) and `hkDisableAll()` (`:5125`).

**Fix (effort S for the immediate bug, M to do it properly):**
1. One-line fix `sidToHex()` to emit big-endian: `('0000' + sid.toString(16)).slice(-4).replace(/(..)(..)/,'$1 $2')`. Add a unit/browser test asserting `sidToHex(1) === '00 01'` and `sidToHex(11) === '00 0B'`.
2. Delete the hardcoded `hkSids` array. Add `GET /api/hk-structures` to `server.py` (the definitions are already loaded — `server.py:182-184` builds `hk_struct_map` from `hk_defs`, so `sid`, `name`, `interval_s` and `len(parameters)` are all in hand) and populate `renderHkTable()` from it. This fixes the swapped names, the phantom SID 7, the missing SID 11 and all the wrong intervals/counts in one move, permanently.
3. Report the *real* enable state. Two options: (i) mirror the sim's `_hk_enabled` map through the simulator's HTTP state API into the MCS state broadcast; or (ii) infer it from observation — the MCS already timestamps every decoded SID (`server.py:688-692` stores `sid` and `last_update_ts` per parameter), so a "last seen 3 s ago / expected every 4 s" freshness column is derivable **today** with zero sim changes and is arguably the more honest operator display. Recommend (ii) plus a `PERIODIC / SILENT` chip.

### (c) The APID 2 beacon HK SID (SID 11)

**Verdict: CONFIRMED — SID 11 is absent from the MCS list entirely (see (b)), and while it is *nominally* commandable in bootloader, the emission path makes enable/disable meaningless there. It should be listed read-only with an explanatory state.**

**The definition** — `configs/eosat1/telemetry/hk_structures.yaml:280-291`:

```yaml
  - sid: 11
    name: Beacon
    interval_s: 10.0
    # 6 parameters — matches bootloader emission (canonical per SPACECDF_RECONCILE item 8)
    parameters:
      - { param_id: 0x0100, … }    # bat_voltage
      - { param_id: 0x0101, … }    # bat_soc
      - { param_id: 0x0300, … }    # obc_mode
      - { param_id: 0x030A, … }    # reboot_count
      - { param_id: 0x0311, … }    # sw_image
      - { param_id: 0x030C, … }    # active_obc
```

**APID 2 confirmed** — `configs/eosat1/mission.yaml:4-5`:

```yaml
spacecraft_apid: 1        # OBC application-software APID
bootloader_apid: 2        # OBC bootloader APID (distinct source for beacon TM)
```

consumed at `engine.py:153-158`, and switched on bootloader entry at `engine.py:357-359` `self.tm_builder.set_apid(self._bootloader_apid)`, restored on app boot at `engine.py:454`.

**The bootloader restriction, precisely.** Three separate gates apply, and they do not agree:

1. **TC acceptance gate** (`engine.py:1237-1267`) — in bootloader (`spacecraft_phase <= 3`), only S17.1, S9.1, S3.{1,3,5,6,9,27}, S20.{1,3} and S8.1 with `func_id in {19,20,52,53,54,55,56,57,61,66,67,69}` are accepted; everything else gets an S1.2 rejection with error `0x0006`. So **S3.5 / S3.6 / S3.27 are permitted in bootloader**.
2. **But the comment lies about what happens next.** `engine.py:1244-1247`:
   ```python
   elif svc == 3 and sub in (1, 3, 5, 6, 9, 27):
       # HK management for the beacon SID is allowed; the dispatcher
       # will reject anything that isn't SID 11 via normal validation.
       allowed = True
   ```
   `_handle_s3` (`service_dispatch.py:217-270`) performs **no SID validation whatsoever** — `HK_DISABLE(sid=3)` in bootloader is accepted and executed, and `HK_ENABLE(sid=1)` likewise (it just has no visible effect because of gate 3). The claimed dispatcher-level restriction does not exist.
3. **Emission gate** (`engine.py:1414-1428`): `if not self._hk_enabled.get(sid, True): continue` **and** `if sw_image == 0 and sid not in (10, 11): continue`. So in bootloader only SIDs 10/11 can ever be emitted regardless of enable bits — and SID 10 was deliberately removed (`hk_structures.yaml:275-278`).

Net behaviour today: `HK_DISABLE(sid=11)` in bootloader **is accepted and does silence the beacon** — which is a genuinely hazardous capability (it is the spacecraft's only signal of life during LEOP; recovery would require a blind `HK_ENABLE`, exactly the scenario `09_leop.md:468,478` describes). Meanwhile the MCS gives the operator **no way to see, enable, request or disable SID 11 at all**, because it is not in `hkSids` — and even if it were, `sidToHex(11)` would emit `"0B 00"` = SID 2816.

**Proposal (effort S):** list SID 11 in the HK Manager as a first-class row with a distinct, non-toggleable presentation:

| SID | Name | APID | Interval | Params | State | Actions |
|---|---|---|---|---|---|---|
| 11 | Beacon (bootloader) | **0x002** | 10.0 s | 6 | `ALWAYS-ON — bootloader beacon` | **Request** only; Enable/Disable rendered disabled with tooltip |

Concretely:
1. Add SID 11 to the (now catalogue-driven, per (b)) table with `readonly: true`, `apid: 2`.
2. Render the Enable/Disable button `disabled` with `title="SID 11 is the bootloader beacon on APID 0x002 — the spacecraft's only signal of life in phases ≤ 3. Enable/disable is inhibited from the console."` Leave **Request** (S3.27) live — the LEOP procedures explicitly call for a one-shot beacon request (`09_leop.md:159`, `:478`; `04_obdh.md:114`).
3. Add an **APID** column to the whole table (1 = application, 2 = bootloader) and drive the SID-11 row's state chip from observed APID: if incoming S3.25 packets carry `apid == 2` (already available at `server.py:669` `info["apid"]`), show `ACTIVE — SPACECRAFT IN BOOTLOADER` and grey out rows 1–6 with `SUPPRESSED (sw_image=0)`. This turns the table into a live diagnostic of bootloader-vs-application state, which is precisely the "clear, unambiguous signature" the manual promises at `09_leop.md:142`.
4. **Sim-side hardening (S, recommended):** make `_handle_s3` reject S3.6 for SID 11 while `spacecraft_phase <= 3` (return an S1.2 with `0x0006`), so the beacon cannot be commanded off from the ground during LEOP — and make the comment at `engine.py:1245-1246` true by actually validating the SID there.

---

## Summary of confirmed defects

| # | Q | Defect | Evidence | Effort |
|---|---|---|---|---|
| 1 | Q1 | Event/TM logs re-render via `innerHTML` on every packet → scroll always jumps to latest | `index.html:3253-3261`, `:3235` | S |
| 2 | Q1 | Only newest 80 of 200/300 buffered rows are reachable | `index.html:3194, 3206, 3253` | S |
| 3 | Q2 | SID (`hk_decoded.sid`) and event RID reach the browser but are never displayed in TM PACKETS | `server.py:693`, `index.html:3193` | S |
| 4 | Q3 | No FDIR display exists; `displays.js` never loaded; `textbook_vs_sim.md:81` verdict wrong | `index.html:8-9`, `displays.js:166` | M |
| 5 | Q3 | `FDIRAlarmPanel.update_s12_rules/update_s19_rules/set_fdir_level` never called → endpoint always reports 0 rules / "nominal" | `fdir_alarm_panel.py:126,140,154` | S |
| 6 | Q3 | `sc_mode` never downlinked; `state.sc_mode` always undefined | `engine.py:196` vs `index.html:3843` | S |
| 7 | Q3 | `fdir.level/active_alarms/critical_alarms/high_alarms` (0x0700–0x0703) in no HK SID → dead config | `parameters.yaml:341-344` vs `hk_structures.yaml` | S |
| 8 | Q4 | Charts keep appending the last value at 1 Hz after LOS → flat line indistinguishable from live data | `server.py:1045`, `index.html:4052` | S |
| 9 | Q4 | Four staleness signals exist and none reach the plots; `const stale` assigned and unused | `index.html:3817` | S |
| 10 | Q4 | Chart x-axis is a shared global counter, not time; gaps are interpolated away | `index.html:2628, 2620` | M |
| 11 | Q5 | `ChartDataLabels` undeclared → `ReferenceError` kills every plot in the delayed-TM viewer | `delayed_tm_viewer.py:604, 702` | S |
| 12 | Q5 | `getattr(pkt.secondary,"time")` — field is `cuc_time` → every CUC is 0 | `tm_report.py:150` vs `ecss_packet.py:89` | S |
| 13 | Q5 | `formatCucTime()` assumes coarse/fine packing and a bogus epoch offset | `delayed_tm_viewer.py:502-508` | S |
| 14 | Q5 | Tool claims "fully offline" but loads Chart.js from a CDN | `delayed_tm_viewer.py:12` vs `:255` | S |
| 15 | Q6 | Command builder never reads `/api/tc-catalog`; `FUNC_CATALOG` hardcoded and missing 12 of 91 funcs | `index.html:3322-3365`, `server.py:1349` | M |
| 16 | Q6 | No expected-data-length shown anywhere; S8 argument is a free-text hex box | `index.html:3293` | S |
| 17 | Q6 | `RW_SET_SPEED_BIAS` catalogue says 3 B, handler needs 5 B (`int16` vs `float32`) | `tc_catalog.yaml:542` vs `service_dispatch.py:556-558` | S |
| 18 | Q7a | All HK SIDs enabled at load and re-enabled on app boot; manual §5.1 HK_ENABLE ×6 is a no-op | `engine.py:190, 459-460` vs `commissioning_sequence_walkthrough.md:227` | S/M |
| 19 | Q7b | `hkSids` hardcoded: SIDs 4↔6 names swapped, phantom SID 7, SID 11 missing, all intervals/counts wrong | `index.html:5077-5085` vs `hk_structures.yaml` | S |
| 20 | Q7b | `sidToHex()` emits little-endian → every HK Enable/Disable/Request targets the wrong SID | `index.html:5130` vs `service_dispatch.py:254` | S |
| 21 | Q7c | SID 11 not listed; `HK_DISABLE(sid=11)` is accepted in bootloader and silences the only LEOP signal of life | `engine.py:1244-1247`, `service_dispatch.py:256-259` | S |
| 22 | — | Beacon period stated as 30 s / 100 s in the manual, 10 s in config; 7 params claimed vs 6 defined | `09_leop.md:144,159`, `commissioning_sequence_walkthrough.md:44` vs `hk_structures.yaml:282-291` | S |

*End of audit. All citations re-verified against the working tree on 2026-07-28.*
