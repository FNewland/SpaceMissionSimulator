# Workstream 6 — Delayed-TM report generator + safe config editor

## A. Report generator

### What the data actually is

**The archive file.** `SimulationEngine.queue_dump()` (`packages/smo-simulator/src/smo_simulator/engine.py:1914`) pulls the whole store (`tm_storage.py:130` `start_dump`) and, at `engine.py:1945-1960`, writes **every** packet — including the ones the link will drop — as `uint32-BE length + bytes`, repeated. Filename `dump_sid<NN>_<YYYYMMDDThhmmssZ>.bin` (`engine.py:1951-1952`; regex `dump_store.py:96`). Reader: `dump_store.iter_packets()` (`dump_store.py:154-165`), duplicated in `tools/tm_report.py:87-97`.

Two consequences that shape the whole design:
- The archive is the **complete tape**; the MCS's live view is lossy (packets released while `downlink_active` is false are discarded at `engine.py:1994-2000`). Archive-minus-MCS is a *measurable* "what the link cost you" number — genuinely the operationally interesting quantity.
- The filename timestamp is **wall clock** (`_dt.utcnow()`, `engine.py:1951`) while packet CUC is **sim time**. `list_dumps()` sorts on the filename timestamp (`dump_store.py:180-215`). Under time acceleration these two axes are unrelated. Any period selection must be CUC-based, with the filename only used as a coarse pre-filter.

**Which packets are in which dump.** `SERVICE_TO_STORE` (`tm_storage.py:41-44`) routes S3→store 1, S5→store 2, and **everything else falls through to store 3** (`tm_storage.py:83-88`). Store 3 is therefore *not* "science": it is the catch-all holding S1 verification, S12.9/12.10 limit transitions, S15.14 store status, S11.18 schedule, S6.6/6.10 memory, S13, S20. Store 4 is a duplicate of S5 events with severity ≥ 2 (`engine.py:1852-1859`). Capacities/behaviour: `tm_storage.py:33-38` — 1: HK 18000 **circular**, 2: Event 2000 linear, 3: Science 10000 linear, 4: Alarm 500 linear. Nothing is stored while the bootloader runs (`engine.py:2241-2247`, gated on param `0x0311 sw_image != 0`) or for packets ≤ 13 bytes. Stores auto-clear after a dump completes (`engine.py:2008+`), so successive dumps of one SID are disjoint slices and concatenating them is legitimate.

**Packet structure.** 6-byte CCSDS primary (`ecss_packet.py:52-80`), 7-byte TM secondary `[spare/PUS, service, subtype, CUC u32]` (`ecss_packet.py:83-104`), data field, CRC-16/CCITT-FALSE (`ecss_packet.py:118-129`, validated at `144-194`). TC secondary is 3 bytes with **no** CUC (`ecss_packet.py:164-176`).

**`cuc_time` vs `time` — and a bigger problem.** The field is `SecondaryHeader.cuc_time` (`ecss_packet.py:89`); `tm_report.py:150-152` documents that the historical `"time"` lookup silently returned 0 for every packet and is now fixed. But **only two builders ever populate CUC**: `build_hk_packet` (`tm_builder.py:73`) and `build_event_packet` (`tm_builder.py:83`). `_pack_tm` defaults `cuc: int = 0` (`tm_builder.py:143-144`); `build_verification_acceptance/completion/failure` (`tm_builder.py:85-110`) pass nothing, and **all 22 `_pack_tm` call sites in `service_dispatch.py` pass no `cuc`** (e.g. the S12 transition at `service_dispatch.py:1307-1319`). So every S1/S12/S6/S11/S15/S20 packet recovered from store 3 carries `cuc_time == 0`. `summarise_dump` happens to be safe because it ignores falsy CUC (`dump_store.py:295-300`), but a report that plots S12 transitions on a time axis would stack them all at J2000. **The report library must reconstruct time for these from file position, bracketed by the neighbouring HK/event CUCs — and a work package should fix the builder.**

**CUC semantics.** Epoch J2000 = 2000-01-01T12:00:00 (`ecss_packet.py:16`), integer **seconds only** — no sub-second field (`parse_cuc_time`, `ecss_packet.py:132-134`; UTC attached in `dump_store.py:302-307`). SID 1 samples at 1.0 s, so adjacent samples routinely share a CUC. Statistics must be sample-count based, not integration-by-timestamp, unless a nominal Δt is assumed from the SID cadence.

**HK body.** `sid(u16)` then parameters packed in `hk_structures.yaml` order using `pack_format`/`scale` (`tm_builder.py:64-73`); decode is `raw/scale` after skipping the 2-byte SID (`tm_report.py:101-124`, `off = 2`). SIDs and cadences (`configs/eosat1/telemetry/hk_structures.yaml`): 1 EPS 1.0 s (:2-4), 2 AOCS 4.0 s (:63-65), 3 TCS 60.0 s (:150-152), 4 Platform 8.0 s (:174-176), 5 Payload 8.0 s (:212-214), 6 TTC 8.0 s (:243-245), 11 Beacon 30.0 s (:283-298). The stale comment in `tm_storage.py:18-26` calls SID 4 "TTC" and SID 6 "OBDH" — it disagrees with the config. **There is no per-parameter validity/quality flag on the wire**, so "missing" can only ever be inferred from cadence.

**S5 event body** is `event_id(u16) severity(u8) cuc(u32) aux_len(u8) aux[aux_len]` and the subtype *is* the severity (`tm_builder.py:80-83`; `EVENT_AUX_TEXT_MAX = 120` at `:25`). The MCS decodes this correctly (`smo-mcs/server.py:1182-1193`). **`tm_report.py:167-179` does not** — it takes `data_field[2:]` as the description, so every event description in today's report/viewer is prefixed with the severity, CUC and length bytes rendered as UTF-8. Live bug; the new library must own one correct decoder.

**S12 transitions** appear twice: as TM(12,9)/(12,10) in store 3 — 18-byte form `param_id(u16) value(f32) mon_id(u16) check_type(u8) severity(u8) low(f32) high(f32)` (`pus_reports.py:86-112`) — and as *synthetic S5 events* in stores 2/4 masked `0x9000 | param_id` (entered) / `0xA000 | mon_id` (cleared) (`pus_reports.py:62-83`, generated at `engine.py:1873-1875`). That redundancy is a gift: an alarm review can be built from an event-store dump alone.

**Commands.** The dump contains only S1 verification reports, never the TCs. `request_id = (apid << 14) | seq` (`tm_builder.py:87`); subtypes 1 accept / 2 accept-fail (`+error_code u16 +` length-prefixed reason, `tm_builder.py:97-110`) / 3 exec-start / 4,8 exec-fail / 5 progress / 7 complete; the MCS's subtype→state map is `smo-mcs/server.py:1161-1175`. Command *identity* must come from either the MCS `tc_log` table (`tm_archive.py:59-69`) or the `0x030B COMMAND_REJECTED` event's aux text, parsed by `parse_command_rejection` (`pus_services.py:141`) with `failure_code_text` (`:55`).

**Ground-side catalogues available to the report:** `telemetry/parameters.yaml` (id/name/subsystem/units/description), `telemetry/hk_structures.yaml`, `events/event_catalog.yaml`, `mcs/limits.yaml` (yellow/red low/high — the basis for out-of-limit time), `monitoring/s12_definitions.yaml`, `monitoring/s19_rules.yaml`. `tm_report.load_catalogs()` (`tm_report.py:47-83`) loads only the first two. **Ground-side history:** the MCS SQLite archive `tm_parameters / tm_events / tm_alarms / tc_log` (`tm_archive.py:24-72`) behind `/api/archive/{parameters,events,alarms,playback}` (`smo-mcs/server.py:962-965`). **Pass boundaries:** planner `GET /api/contacts` (`smo-planner/server.py:375, 662-675`) or `OrbitPropagator.contact_windows` directly (as `tools/pass_gap_analysis.py:53` does); mission T-0 is `orbit.yaml:15 t0_epoch` and the run starts there because `simulation.yaml` sets `epoch: t0`.

**What exists today.** `tools/tm_report.py:204-302` already emits a static HTML report; `tools/delayed_tm_viewer.py:190-265` re-implements the same analysis for JSON. Neither is configurable, neither knows limits, gaps, passes, orbits or commands, and both share the hard-coded "peak Δ > 5 × mean Δ" anomaly heuristic (`tm_report.py:239-248`, `delayed_tm_viewer.py:229-238`). Two copies of one analysis, no configuration — that is exactly the gap the request names.

**Is this the report an ops engineer would produce?** Partly. Real ESA/NASA-style ground segments produce three distinct artefacts and the templates below mirror them: a **Pass/Contact Report** (per contact: station, AOS/LOS, max elevation, planned vs achieved activities, TM volume received against expected, frame/packet errors, every uplinked command with its verification outcome, OOLs during the pass, stored TM dumped and its completeness, handover actions); a **Daily Operations Report / DOR** (24 h: configuration at start and end, per-subsystem min/max/mean against limits, power budget with min SoC and DoD per eclipse, thermal extremes, pointing performance, payload activity and data volume, pass-by-pass data return %, all OOL events with disposition, command count and rejections, autonomy/FDIR actions, open anomalies, plan for next period); and an **Anomaly Report** (identification, dual-timescale timeline in UTC and on-board time, symptom plots with pre/post windows around T0, event and alarm sequence, autonomous actions, recovery commands and outcomes, telemetry gaps during the event, current status, hypotheses, follow-up). The current tool produces roughly a quarter of the Pass Report. The templates are written to *teach* those structures — each ships with `narrative:` prose blocks explaining what an operator writes in each section.

### Report definition schema

Lives at `smo_common/reporting/spec.py`, pydantic v2, **`model_config = ConfigDict(extra="forbid")`** — unknown keys in a report spec must fail loudly, because the whole point of the format is that the user edits it.

```yaml
# smo_common/reporting/templates/<id>.yaml  — report definition v1
schema_version: 1

report:
  id: pass_report                     # unique, filename stem
  title: "EOSAT-1 Pass Report"
  subtitle: "{{period.label}} — {{period.station}}"
  audience: operator                  # operator | student | instructor (affects verbosity)
  classification: "TRAINING — NOT FOR FLIGHT"
  page_size: A4                       # only used by the PDF build step

# ── Where the data comes from ─────────────────────────────────────────
source:
  dumps:
    mode: auto                        # auto | files | none
    store_ids: [1, 2, 3, 4]           # HK / Event / catch-all / Alarm
    files: []                         # explicit basenames when mode: files
    api: null                         # null = local store; else http://host:9090
  ground_archive:                     # MCS SQLite (tm_archive.py) — optional
    enabled: true
    base_url: null                    # null = in-process (MCS) or SMO_MCS_URL
    use_for: [commands, alarms, events, gap_reference]
  catalogs:
    config_dir: configs/eosat1        # parameters/hk_structures/events/limits/s12/s19
  on_missing_source: warn             # warn | error   (a section with no data is stated, never silently empty)

# ── The period the report covers ──────────────────────────────────────
period:
  kind: pass                          # pass | orbit | day | breakpoint_span | dump | absolute | relative
  # --- kind: pass ---
  pass:
    select: latest                    # latest | index:<n> | station:<name>@<iso> | id:<contact_id>
    station: any
    pad_before_s: 300                 # pre-AOS context
    pad_after_s: 300
    source: planner                   # planner (/api/contacts) | propagator | dump_envelope
  # --- kind: orbit ---
  orbit: { select: latest, count: 1, boundary: ascending_node }
  # --- kind: day ---
  day: { select: latest_utc, count: 1, boundary: "00:00:00Z" }
  # --- kind: breakpoint_span ---
  breakpoint_span: { from: "pre_eclipse", to: "post_recovery" }   # names in workspace/breakpoints
  # --- kind: absolute / relative ---
  absolute: { start: "2026-03-10T04:12:00Z", end: "2026-03-10T04:24:00Z" }
  relative: { anchor: last_dump, before_s: 1800, after_s: 600 }
  timebase: cuc                       # cuc (on-board, authoritative) | wall (archive filename)
  # Packets with cuc_time == 0 (S1/S12/S6/S11/S15 — see tm_builder.py:143) are
  # placed by file order between their bracketing CUC-bearing neighbours.
  zero_cuc_policy: interpolate        # interpolate | exclude | pin_to_dump_start

# ── Reusable parameter groups ─────────────────────────────────────────
parameter_sets:
  eps_vitals:  [eps.bat_soc, eps.bus_voltage, eps.bat_current, eps.bat_temp]
  eps_power:   [eps.power_gen, eps.power_cons]
  aocs_point:  [aocs.att_err_x, aocs.att_err_y, aocs.att_err_z, aocs.att_error]
  thermal:     [tcs.temp_battery, tcs.temp_fpa, tcs.temp_obc]
  link:        [ttc.tm_tier, ttc.tm_tier_cmd, ttc.data_rate_bps, ttc.elevation_deg]
  # Parameters are named (parameters.yaml `name`) or given as 0x0101 ids.
  # A name not in parameters.yaml is a spec-validation ERROR, not a silent drop.

defaults:
  plot:
    height_px: 240
    grid: true
    show_limits: true                 # yellow/red bands from mcs/limits.yaml
    show_eclipse: true                # shade from eps.eclipse_flag (0x0108)
    show_contacts: true               # shade AOS..LOS from the planner
    marker_events: [severity>=3]
  stats:
    columns: [count, min, max, mean, stddev, first, last, ool_time_s, ool_pct]
    round: 3

# ── Sections, rendered in order ───────────────────────────────────────
sections:

  - type: header                      # identity, period, provenance, data-source manifest
    id: cover
    include: [period, dumps_used, packet_totals, crc_errors, catalog_versions, generated_at]

  - type: narrative
    id: intro
    title: "Purpose"
    text: |
      Free markdown. Supports {{period.start_utc}}, {{stats.eps.bat_soc.min}},
      {{counts.events.severity_4}} substitutions so prose can quote the numbers.

  - type: completeness                # THE operationally important one
    id: gaps
    title: "Data completeness"
    sids: [1, 2, 3, 4, 5, 6]
    expected_cadence: from_config     # hk_structures.yaml interval_s
    gap_threshold_multiple: 3.0       # gap = Δt > 3 × nominal cadence
    breakdown: [per_sid, per_pass, per_orbit]
    show: [expected, received, missing_pct, longest_gap_s, gap_list, store_wrap, crc_errors]
    correlate_with: [contacts, eclipse, store_overflow]   # explain each gap if we can

  - type: plot
    id: power
    title: "Power"
    style: multi_axis                 # time_series | multi_axis | step | histogram | xy
    axes:
      - { label: "SoC / %",  parameters: [eps.bat_soc], range: [0, 100] }
      - { label: "Bus / V",  parameters: [eps.bus_voltage, eps.sa_a_voltage] }
      - { label: "Power / W", parameters: parameter_sets.eps_power, side: right }
    annotations: { eclipse: true, contacts: true, events: [0x0106, 0x0107, 0x0108] }

  - type: plot
    id: modes
    style: step                       # discrete/state — sample-and-hold, no interpolation
    parameters: [aocs.mode, obdh.mode, payload.mode, ttc.tm_tier]
    value_labels: from_config         # aocs/aocs_mode_codes.yaml if present, else raw
    show_transitions_table: true

  - type: plot
    id: att_err_dist
    style: histogram
    parameters: parameter_sets.aocs_point
    bins: 40
    overlay: [p50, p95, p99, limit_yellow, limit_red]

  - type: stats
    id: subsystem_stats
    title: "Parameter statistics"
    groups:
      - { label: "EPS",  parameters: parameter_sets.eps_vitals }
      - { label: "AOCS", parameters: parameter_sets.aocs_point }
      - { label: "TCS",  parameters: parameter_sets.thermal }
    columns: [count, min, min_at, max, max_at, mean, stddev, first, last,
              ool_yellow_s, ool_red_s, ool_pct, samples_expected, samples_missing]
    limits_from: mcs/limits.yaml
    flag_rule: "ool_red_s > 0 or ool_pct > 5"

  - type: events
    id: event_review
    title: "Event review"
    severity_min: 2                   # 1 INFO 2 LOW/WARN 3 MEDIUM/ALARM 4 HIGH/CRITICAL
    include_synthetic_s12: true       # 0x9000/0xA000 masks -> filed against the parameter
    resolve_names: event_catalog      # attaches name + subsystem + catalogue description
    group_by: subsystem               # none | subsystem | severity | pass
    show: [utc, cuc, id, name, severity, subsystem, aux_text]
    correlate:
      window_s: 60
      with: [s12_transitions, s19_fires, commands, parameter_steps]

  - type: alarms
    id: alarm_review
    title: "Limit violations and FDIR"
    sources: [s12_transitions, synthetic_events, ground_archive_alarms]
    severity_min: 2
    show_definition: true             # low/high/check_type from the packet itself
    pair_entries_and_clears: true     # -> duration in limit, per monitor
    unpaired_policy: report           # an entry with no clear by end-of-period is called out
    s19_correlation:
      enabled: true
      rules_from: monitoring/s19_rules.yaml
      show: [ea_id, trigger_kind, trigger_id, action_func_id, fired_at, latency_s]

  - type: commands
    id: command_history
    title: "Command history"
    source: [s1_verification, ground_archive_tc_log, event_0x030B]
    show: [utc, seq, apid, name, service, subtype, position, accept, execute,
           error_code, error_text, reason, latency_s]
    outcomes: [ACCEPTED, REJECTED, COMPLETED, FAILED, NO_VERIFICATION]
    flag_rule: "outcome in [REJECTED, FAILED, NO_VERIFICATION]"

  - type: breakdown
    id: pass_by_pass
    title: "Pass-by-pass"
    by: pass                          # pass | orbit | eclipse_cycle | hour
    metrics: [duration_s, station, max_elevation_deg, packets, bytes,
              data_return_pct, commands_sent, commands_rejected,
              events_sev3plus, min_soc, max_bat_temp, dumps]

  - type: breakdown
    id: orbit_by_orbit
    by: orbit
    metrics: [min_soc, max_dod, eclipse_duration_s, energy_balance_wh,
              max_att_error, max_wheel_speed, packets, missing_pct]

  - type: table                       # arbitrary derived table
    id: store_status
    title: "On-board store status at dump"
    source: s15_store_status          # TM(15,14) — pus_reports.py:205
    columns: [id, name, count, capacity, fill_pct, overflow, circular, wrap_count]

  - type: appendix
    id: raw
    include: [packet_counts_by_service, apids, undecodable, spec_yaml, dataset_json]
    embed_dataset: true               # inline JSON so the report is re-analysable offline

output:
  formats: [html]                     # html (primary) | pdf | json | csv
  self_contained: true                # inline CSS + inline SVG, no CDN, no /vendor
  filename: "{{report.id}}_{{period.start_utc|compact}}.html"
  save_to: workspace/reports
```

### Templates

Three shipped under `packages/smo-common/src/smo_common/reporting/templates/`.

**1. `pass_report.yaml` — the artefact written after every contact.**

```yaml
schema_version: 1
report:
  id: pass_report
  title: "EOSAT-1 Pass Report"
  subtitle: "{{period.station}} — {{period.start_utc}} to {{period.end_utc}}"
  audience: operator
  classification: "TRAINING — NOT FOR FLIGHT"
source:
  dumps: { mode: auto, store_ids: [1, 2, 3, 4] }
  ground_archive: { enabled: true, use_for: [commands, alarms, gap_reference] }
  catalogs: { config_dir: configs/eosat1 }
period:
  kind: pass
  pass: { select: latest, station: any, pad_before_s: 300, pad_after_s: 300, source: planner }
  timebase: cuc
  zero_cuc_policy: interpolate
parameter_sets:
  eps_vitals: [eps.bat_soc, eps.bus_voltage, eps.bat_current]
  link:       [ttc.tm_tier, ttc.tm_tier_cmd, ttc.data_rate_bps, ttc.elevation_deg, ttc.link_status]
  thermal:    [tcs.temp_battery, tcs.temp_fpa]
sections:
  - { type: header, id: cover,
      include: [period, dumps_used, packet_totals, crc_errors, generated_at] }
  - type: narrative
    id: summary
    title: "Pass summary"
    text: |
      **Station:** {{period.station}}  **AOS:** {{period.start_utc}}  **LOS:** {{period.end_utc}}
      **Duration:** {{period.duration_min}} min  **Max elevation:** {{period.max_elevation_deg}}°

      Data return {{gaps.received_pct}} % of expected. {{counts.commands.total}} commands
      uplinked, {{counts.commands.rejected}} rejected. {{counts.events.severity_3plus}}
      events of severity 3 or above.

      *Operator: record here what you were trying to achieve this pass, what you actually
      achieved, and what the next shift must pick up. A pass report that only lists numbers
      is not a pass report.*
  - type: completeness
    id: gaps
    title: "Telemetry received vs expected"
    sids: [1, 2, 3, 4, 5, 6]
    expected_cadence: from_config
    gap_threshold_multiple: 3.0
    breakdown: [per_sid]
    show: [expected, received, missing_pct, longest_gap_s, gap_list, store_wrap, crc_errors]
    correlate_with: [contacts, store_overflow]
  - type: plot
    id: link
    title: "Link and geometry"
    style: multi_axis
    axes:
      - { label: "Elevation / deg", parameters: [ttc.elevation_deg], range: [0, 90] }
      - { label: "Rate / bps",      parameters: [ttc.data_rate_bps], side: right, log: true }
    annotations: { contacts: true, events: [0x0001, 0x0002] }
  - { type: plot, id: tier, style: step,
      parameters: [ttc.tm_tier, ttc.tm_tier_cmd, ttc.downlink_mode],
      show_transitions_table: true }
  - { type: plot, id: power, style: multi_axis,
      axes: [ { label: "SoC / %", parameters: [eps.bat_soc], range: [0, 100] },
              { label: "Bus / V", parameters: [eps.bus_voltage], side: right } ],
      annotations: { eclipse: true } }
  - { type: stats, id: vitals, title: "Vitals over the pass",
      groups: [ { label: "EPS", parameters: parameter_sets.eps_vitals },
                { label: "TCS", parameters: parameter_sets.thermal },
                { label: "Link", parameters: parameter_sets.link } ],
      columns: [count, min, min_at, max, max_at, mean, stddev, first, last, ool_red_s],
      limits_from: mcs/limits.yaml }
  - { type: commands, id: commands, title: "Commands uplinked this pass",
      source: [s1_verification, ground_archive_tc_log, event_0x030B],
      show: [utc, seq, name, service, subtype, position, accept, execute,
             error_code, error_text, reason, latency_s],
      flag_rule: "outcome in [REJECTED, FAILED, NO_VERIFICATION]" }
  - { type: events, id: events, title: "Events", severity_min: 2,
      include_synthetic_s12: true, resolve_names: event_catalog, group_by: severity,
      correlate: { window_s: 60, with: [commands, s12_transitions] } }
  - { type: alarms, id: alarms, title: "Limit violations", severity_min: 2,
      pair_entries_and_clears: true, unpaired_policy: report,
      s19_correlation: { enabled: true, rules_from: monitoring/s19_rules.yaml } }
  - { type: table, id: stores, title: "On-board stores at dump",
      source: s15_store_status,
      columns: [id, name, count, capacity, fill_pct, overflow, circular, wrap_count] }
  - type: narrative
    id: handover
    title: "Actions and handover"
    text: |
      | # | Action | Owner | Due |
      |---|--------|-------|-----|
      |   |        |       |     |

      Open anomalies carried forward:
      Next contact: {{next_pass.station}} at {{next_pass.aos_utc}}
  - { type: appendix, id: raw,
      include: [packet_counts_by_service, apids, undecodable, spec_yaml] }
output: { formats: [html], self_contained: true, save_to: workspace/reports }
```

**2. `daily_ops_review.yaml` — the DOR.**

```yaml
schema_version: 1
report:
  id: daily_ops_review
  title: "EOSAT-1 Daily Operations Report"
  subtitle: "MET day {{period.met_day}} — {{period.start_utc}} to {{period.end_utc}}"
  audience: operator
source:
  dumps: { mode: auto, store_ids: [1, 2, 3, 4] }
  ground_archive: { enabled: true, use_for: [commands, alarms, events, gap_reference] }
  catalogs: { config_dir: configs/eosat1 }
period:
  kind: day
  day: { select: latest_utc, count: 1, boundary: "00:00:00Z" }
  timebase: cuc
parameter_sets:
  eps:     [eps.bat_soc, eps.bus_voltage, eps.bat_current, eps.bat_temp, eps.bat_dod]
  power:   [eps.power_gen, eps.power_cons]
  thermal: [tcs.temp_battery, tcs.temp_fpa, tcs.temp_obc]
  aocs:    [aocs.att_error, aocs.rw_speed_1, aocs.rw_speed_2, aocs.rw_speed_3, aocs.total_momentum]
  payload: [payload.store_used, payload.mode]
defaults:
  plot: { height_px: 200, show_limits: true, show_eclipse: true, show_contacts: true }
sections:
  - { type: header, id: cover, include: [period, dumps_used, packet_totals, crc_errors,
                                         catalog_versions, generated_at] }
  - type: narrative
    id: exec
    title: "Executive summary"
    text: |
      Spacecraft mode at period start / end. Overall health assessment in one paragraph,
      then the exceptions. A DOR is read by people who will only read this box — put the
      decisions here, not the data.

      Min SoC {{stats.eps.bat_soc.min}} % at {{stats.eps.bat_soc.min_at}}.
      {{counts.passes}} passes, mean data return {{gaps.received_pct}} %.
      {{counts.events.severity_4}} critical events. {{counts.commands.rejected}} command rejections.
  - { type: completeness, id: gaps, title: "Data completeness",
      sids: [1, 2, 3, 4, 5, 6], expected_cadence: from_config, gap_threshold_multiple: 3.0,
      breakdown: [per_sid, per_pass, per_orbit],
      show: [expected, received, missing_pct, longest_gap_s, gap_list, store_wrap, crc_errors],
      correlate_with: [contacts, eclipse, store_overflow] }
  - { type: plot, id: power_day, title: "Power over 24 h", style: multi_axis,
      axes: [ { label: "SoC / %", parameters: [eps.bat_soc], range: [0, 100] },
              { label: "W", parameters: parameter_sets.power, side: right } ],
      annotations: { eclipse: true, contacts: true, events: [0x0106, 0x0107, 0x0108, 0x0102] } }
  - { type: plot, id: thermal_day, title: "Thermal", style: time_series,
      parameters: parameter_sets.thermal }
  - { type: plot, id: aocs_day, title: "Attitude and momentum", style: multi_axis,
      axes: [ { label: "deg", parameters: [aocs.att_error] },
              { label: "RPM", parameters: [aocs.rw_speed_1, aocs.rw_speed_2, aocs.rw_speed_3],
                side: right } ] }
  - { type: plot, id: att_hist, title: "Pointing error distribution",
      style: histogram, parameters: [aocs.att_error], bins: 50,
      overlay: [p50, p95, p99, limit_yellow, limit_red] }
  - { type: plot, id: modes, style: step,
      parameters: [obdh.mode, aocs.mode, payload.mode, ttc.tm_tier, eps.load_shed_stage],
      show_transitions_table: true }
  - { type: stats, id: stats, title: "Parameter statistics (24 h)",
      groups: [ { label: "EPS", parameters: parameter_sets.eps },
                { label: "TCS", parameters: parameter_sets.thermal },
                { label: "AOCS", parameters: parameter_sets.aocs },
                { label: "Payload", parameters: parameter_sets.payload } ],
      columns: [count, min, min_at, max, max_at, mean, stddev, first, last,
                ool_yellow_s, ool_red_s, ool_pct, samples_expected, samples_missing],
      limits_from: mcs/limits.yaml,
      flag_rule: "ool_red_s > 0 or ool_pct > 5" }
  - { type: breakdown, id: orbits, title: "Orbit-by-orbit", by: orbit,
      metrics: [min_soc, max_dod, eclipse_duration_s, energy_balance_wh,
                max_att_error, max_wheel_speed, packets, missing_pct] }
  - { type: breakdown, id: passes, title: "Pass-by-pass", by: pass,
      metrics: [duration_s, station, max_elevation_deg, packets, bytes, data_return_pct,
                commands_sent, commands_rejected, events_sev3plus, dumps] }
  - { type: events, id: events, title: "Event review", severity_min: 2,
      include_synthetic_s12: true, resolve_names: event_catalog, group_by: subsystem,
      correlate: { window_s: 120, with: [s12_transitions, s19_fires, commands] } }
  - { type: alarms, id: alarms, title: "Limit violations and autonomy", severity_min: 2,
      pair_entries_and_clears: true, unpaired_policy: report,
      s19_correlation: { enabled: true, rules_from: monitoring/s19_rules.yaml,
                         show: [ea_id, trigger_kind, trigger_id, action_func_id, fired_at, latency_s] } }
  - { type: commands, id: commands, title: "Commanding", source: [ground_archive_tc_log, s1_verification],
      show: [utc, seq, name, service, subtype, position, accept, execute, error_code, error_text],
      flag_rule: "outcome in [REJECTED, FAILED]" }
  - type: narrative
    id: forward
    title: "Open items and plan for next period"
    text: |
      Open anomalies (id, opened, severity, owner, status):
      Configuration changes made this period:
      Planned activities next period:
      Constraints / cautions for the incoming shift:
output: { formats: [html], self_contained: true, save_to: workspace/reports }
```

**3. `anomaly_investigation.yaml` — the AR.**

```yaml
schema_version: 1
report:
  id: anomaly_investigation
  title: "EOSAT-1 Anomaly Report"
  subtitle: "{{vars.anomaly_id}} — {{vars.title}}"
  audience: instructor
vars:                                  # supplied by the UI form / API body
  anomaly_id: "ANO-001"
  title: "Battery undervoltage during eclipse"
  t0: "2026-03-10T04:18:22Z"
  suspect_subsystems: [eps, tcs]
source:
  dumps: { mode: auto, store_ids: [1, 2, 3, 4] }
  ground_archive: { enabled: true, use_for: [commands, alarms, events] }
  catalogs: { config_dir: configs/eosat1 }
period:
  kind: relative
  relative: { anchor: "{{vars.t0}}", before_s: 3600, after_s: 3600 }
  timebase: cuc
  zero_cuc_policy: interpolate
parameter_sets:
  suspects: [eps.bat_soc, eps.bus_voltage, eps.bat_current, eps.bat_temp,
             eps.power_gen, eps.power_cons, eps.sa_a_current, eps.sa_b_current]
  context:  [tcs.temp_battery, obdh.mode, ttc.tm_tier, aocs.mode]
defaults:
  plot: { height_px: 260, show_limits: true, show_eclipse: true,
          marker_events: ["severity>=2"] }
sections:
  - { type: header, id: cover, include: [period, dumps_used, packet_totals, generated_at] }
  - type: narrative
    id: identification
    title: "1. Identification"
    text: |
      | Field | Value |
      |---|---|
      | Anomaly ID | {{vars.anomaly_id}} |
      | Title | {{vars.title}} |
      | T0 (on-board) | {{vars.t0}} |
      | Detected by | (operator / on-board FDIR / post-pass review) |
      | Subsystems | {{vars.suspect_subsystems}} |
      | Severity | |
      | Status | OPEN |
  - type: narrative
    id: timeline_intro
    title: "2. Timeline"
    text: |
      Every entry below is stamped with **on-board CUC** and ground UTC. Note that S1
      verification and S12 transition packets carry no on-board timestamp
      (`tm_builder.py:143`); those rows are placed by archive order between their
      bracketing timestamped neighbours and are marked `~`.
  - { type: events, id: timeline, severity_min: 1, include_synthetic_s12: true,
      resolve_names: event_catalog, group_by: none,
      show: [utc, cuc, id, name, severity, subsystem, aux_text],
      correlate: { window_s: 30, with: [s12_transitions, s19_fires, commands, parameter_steps] } }
  - { type: plot, id: symptom, title: "3. Symptoms — suspect parameters around T0",
      style: multi_axis,
      axes: [ { label: "V", parameters: [eps.bus_voltage] },
              { label: "%", parameters: [eps.bat_soc], side: right, range: [0, 100] },
              { label: "A", parameters: [eps.bat_current, eps.sa_a_current, eps.sa_b_current] } ],
      annotations: { eclipse: true, contacts: true, t0_marker: "{{vars.t0}}" } }
  - { type: plot, id: context, title: "Context", style: time_series,
      parameters: parameter_sets.context }
  - { type: plot, id: states, style: step,
      parameters: [obdh.mode, aocs.mode, payload.mode, eps.load_shed_stage, ttc.tm_tier],
      show_transitions_table: true }
  - { type: stats, id: prepost, title: "4. Before / after comparison",
      groups: [ { label: "Suspects", parameters: parameter_sets.suspects } ],
      columns: [count, min, min_at, max, max_at, mean, stddev, first, last, ool_red_s],
      split_at: "{{vars.t0}}",          # renders two side-by-side stat blocks
      limits_from: mcs/limits.yaml }
  - { type: alarms, id: autonomy, title: "5. On-board autonomy response",
      severity_min: 1, pair_entries_and_clears: true, unpaired_policy: report,
      show_definition: true,
      s19_correlation: { enabled: true, rules_from: monitoring/s19_rules.yaml,
                         show: [ea_id, trigger_kind, trigger_id, action_func_id, fired_at, latency_s] } }
  - { type: commands, id: recovery, title: "6. Ground response",
      source: [s1_verification, ground_archive_tc_log, event_0x030B],
      show: [utc, seq, name, service, subtype, position, accept, execute,
             error_code, error_text, reason, latency_s] }
  - { type: completeness, id: gaps, title: "7. Telemetry availability during the anomaly",
      sids: [1, 2, 3, 4, 5, 6], expected_cadence: from_config, gap_threshold_multiple: 2.0,
      breakdown: [per_sid],
      show: [expected, received, missing_pct, longest_gap_s, gap_list, store_wrap, crc_errors],
      correlate_with: [contacts, store_overflow] }
  - type: narrative
    id: analysis
    title: "8. Analysis, root cause and actions"
    text: |
      **Observed behaviour:**
      **Expected behaviour (design reference):**
      **Candidate causes** (list, each with the evidence that supports or refutes it):
      **Root cause:**
      **Immediate recovery actions taken:**
      **Corrective/preventive actions:**
      **Recurrence check** — re-run this report over a wider period and state whether the
      signature appears elsewhere.
  - { type: appendix, id: raw,
      include: [packet_counts_by_service, apids, undecodable, spec_yaml, dataset_json],
      embed_dataset: true }
output: { formats: [html, json], self_contained: true, save_to: workspace/reports }
```

### Architecture & API

**Primary output: one self-contained HTML file.** Justification, specific to this repo and this classroom: (a) the suite is deliberately CDN-free and offline — `delayed_tm_viewer.py:12-14` and its `/vendor` fallback at `:334-343` show that is an existing project value, and `docs/DEPLOYMENT.md` describes lab networks with no internet; (b) students are on browsers only in shape B (`DEPLOYMENT.md` §1) so a file they can open, keep, email and upload to a VLE beats a server-rendered page; (c) inline SVG needs no JS at all, so the report renders in any browser, prints correctly, and survives being archived; (d) a PDF toolchain (wkhtmltopdf/Chromium) cannot be assumed on Windows lab machines that installed via `deploy/install.ps1`. **PDF is an optional build step** (`--pdf`, headless Chromium/`weasyprint` if importable, otherwise an honest error). **JSON and CSV sidecars** are also emitted (`output.formats`) — JSON because `appendix.embed_dataset` makes the HTML re-analysable, CSV because marking and student post-processing want a spreadsheet.

**Where it lives.** `packages/smo-common/src/smo_common/reporting/` — smo_common already owns the store (`dump_store.py`) and both consumers already import it (`tools/delayed_tm_viewer.py:52`, `smo-mcs/server.py:979`). Modules:

| Module | Responsibility |
|---|---|
| `spec.py` | pydantic v2 models for the report YAML, `extra="forbid"`, `load_spec()`, `validate_spec()` |
| `decode.py` | **the one** correct decoder set: S3.25 HK, S5 events (fixes `tm_report.py:167-179`), S12.9/.10, S1.x, S15.14, S11.18, S6.x. Reuses `pus_reports.py` where it already exists — move it to smo_common and re-export from smo-mcs so there is no third copy |
| `dataset.py` | `TelemetryDataset.from_dumps(paths, catalogs)` / `.merge_archive(...)`: one pass over packets → per-parameter series, event list, alarm list, verification list, store status, CRC/undecodable counts, packet census. Owns `zero_cuc_policy` |
| `periods.py` | resolve `period:` → `(start_cuc, end_cuc, label, metadata)`; pass/orbit resolution via `OrbitPropagator.contact_windows` (as `pass_gap_analysis.py:53`) or the planner API; breakpoint spans from `workspace/breakpoints/*.json` (`engine.py:2474`) |
| `analysis.py` | stats (incl. `ool_*` from `mcs/limits.yaml`), completeness/gap detection from `hk_structures.yaml interval_s`, per-pass/per-orbit binning, event↔alarm↔command↔S19 correlation |
| `render_html.py` | inline-SVG renderer; no JS required; `<details>` for large tables |
| `render_pdf.py`, `render_csv.py` | optional |
| `api.py` | `register_report_routes(app, get_dump_dir, config_dir, *, prefix="/api/reports", page_path="/reports", owner="")` — same shape as `register_dump_routes` (`dump_store.py:348`) |
| `templates/*.yaml` | the three above, plus `templates/index.yaml` |
| `cli.py` | `python -m smo_common.reporting --template pass_report --period latest-pass -o out.html` — replaces `tools/tm_report.py`, which becomes a thin shim |

**API** (mounted by the MCS next to `register_dump_routes` at `smo-mcs/server.py:979-980`, and by the standalone viewer in `make_app`, `delayed_tm_viewer.py:270`):

```
GET  /api/reports/templates                 -> [{id,title,audience,description,period_kind}]
GET  /api/reports/templates/{id}            -> raw YAML (text/yaml) for "edit a copy"
POST /api/reports/templates/{id}            -> save a user template (workspace/report-templates/)
POST /api/reports/validate  {spec}          -> {ok, errors[{path,msg}], warnings[]}
GET  /api/reports/periods?kind=pass|orbit|day|breakpoint&limit=20
                                            -> [{id,label,start_utc,end_utc,station,max_el,dumps[]}]
POST /api/reports/preview   {spec|template_id, vars, period_override}
                                            -> JSON report model (sections + series + tables),
                                               no HTML: the UI renders the preview
POST /api/reports/render    {…}&format=html|pdf|json|csv
                                            -> file; also written to workspace/reports
GET  /api/reports                           -> saved renders (filename, title, period, bytes, mtime)
GET  /api/reports/file?name=…               -> download; name validated exactly like
                                               dump_store.resolve_dump_file (dump_store.py:217)
GET  /reports                               -> the browser page
```

Rendering a day report over many dumps is CPU-bound; `render` runs in a thread executor and long jobs return `202 {job_id}` with `GET /api/reports/jobs/{id}` — the MCS event loop must not stall while students are commanding.

### UI spec

Two surfaces, one component.

**A. Delayed TM viewer (`tools/delayed_tm_viewer.py`, port 8092)** — add a `REPORT` section below the existing controls (`INDEX_HTML` at `:349`, controls block `:408-417`):
- Row 1 — **Template**: `<select>` from `/api/reports/templates` + "Edit YAML…" which opens a textarea pre-filled with the template and a *Validate* button wired to `/api/reports/validate` (errors listed with `path: message`).
- Row 2 — **Period**: kind selector (`Pass | Orbit | Day | Breakpoint span | Dumps selected | Absolute`); for pass/orbit/day it populates a second `<select>` from `/api/reports/periods`, showing `Svalbard 04:12–04:24Z · max el 71° · 2 dumps · 4 812 pkts`; for "Dumps selected" it reuses the existing dump multi-select (`renderDumpsList`, `:474`). A live badge shows which dump files the period resolves to, so the student sees the archive→period mapping rather than guessing.
- Row 3 — **Vars**: auto-generated inputs from the template's `vars:` block (the anomaly template gets `anomaly_id`, `title`, `t0`, `suspect_subsystems`).
- Buttons — **Preview** (calls `/preview`, renders section-by-section into the existing panel area using the already-vendored Chart.js for interactive plots), **Download HTML**, **Download PDF** (disabled with a tooltip if the server reports no PDF backend), **Download JSON/CSV**, **Save template**.
- Preview shows a **provenance strip** at the top: dumps used, packet count, CRC errors, undecodable count, % completeness, and an explicit "N packets carry no on-board timestamp; placed by archive order" note when `zero_cuc_policy` fired.

**B. MCS (port 9090)** — the students' door. Today "DELAYED TM" is only an `<a href="/dumps">` in the header (`smo-mcs/static/index.html:847-851`). Add **"REPORTS"** beside it, pointing at `/reports` (same relative-href reasoning as the comment at `:847-849`). `/reports` is the same self-contained page served by `register_report_routes`, i.e. one implementation, two hosts — exactly the pattern `dump_store._render_page` (`dump_store.py:436`) established. The page lists previously generated reports (title, period, generated-at, size, open/download) above the generator form, so a student arriving mid-exercise finds the instructor's report without generating anything.

Accessibility/consistency: reuse the existing dark palette variables from `delayed_tm_viewer.INDEX_HTML:356-359`; every table gets a caption; every plot gets a `<title>`/`<desc>` in the SVG and a "show data" `<details>` with the numbers, because an SVG-only report is unusable to a screen reader and useless to a student who wants the values.

---

## B. Config editor

### Config inventory & editability classification

"Live-editable" today is **nothing**: `SimulationEngine.__init__` loads every config once (`engine.py:134-141, 227, 268, 286, 375, 387, 627, 631, 666, 776`) and there is no reload path in the simulator or the MCS (`grep` for `reload_config`/`/api/reload` finds none). The only reload anywhere is the planner's `GET /api/config/alignment?recheck=1`, which re-reads `orbit.yaml` + `planner.yaml` **for the alignment report only** (`smo-planner/server.py:865-871`) — the propagator keeps the old TLE. The "Live?" column below is therefore the *target* state the design should reach, with today's state stated.

| File | Purpose | Live-editable? | Blast radius |
|---|---|---|---|
| `mission.yaml` | identity, APIDs, ports, `network.sim_tick_hz`, `dump_dir`, `time_source` (`schemas.py:7-37`) | **No — restart all three services.** Ports/APIDs change the wire | **Very high.** APID change breaks TM routing; port change orphans MCS/planner; `dump_dir` moves the archive out from under the MCS. Note `ground_segment:` (`mission.yaml:30-46`) is **silently dropped** — no schema field |
| `orbit.yaml` | TLE, `t0_epoch`, **canonical ground stations** (`schemas.py:47-60`) | **No — restart simulator + planner + MCS** | **Very high.** Station list changes contact statistics (own file documents 39→103 passes, `orbit.yaml:37-40`); `t0_epoch` changes every eclipse and pass in the run |
| `simulation.yaml` | sim tuning: speed, phases, TTC pacing, HK default-enabled SIDs, EPS initial/thresholds/shed ladder, TCS environment, failure onset, scenario defaults (`schemas.py:530-546`) | **No — restart simulator.** Best candidate for a future reload of `speed`/`failures`/`scenario_defaults` only | Medium–high. `eps.load_shed.*` and `event_thresholds` change FDIR behaviour mid-exercise; `housekeeping.default_enabled_sids` changes commissioning |
| `subsystems/eps.yaml` | battery, arrays, line power, param_ids (`schemas.py:75-92`) | No — restart simulator | High. Feeds the planner budget via `null` resolution (`planner_config.py:100-120`) |
| `subsystems/aocs.yaml` | wheels, modes, deadband (`schemas.py:99-105`) | No — restart simulator | Medium |
| `subsystems/tcs.yaml` | zones, heaters, coupling (`schemas.py:119-129`) | No — restart simulator | Medium |
| `subsystems/obdh.yaml` | watchdog, CPU baseline (`schemas.py:131-136`) | No — restart simulator | Medium |
| `subsystems/ttc.yaml` | **RF plan** `ul_freq_mhz`/`dl_freq_mhz` (`:16-17`), TM rate ladder tiers (`schemas.py:138-220`) | No — restart simulator (+ rfsim) | **Very high.** Frequencies must agree with `ground_stations.yaml` and `rfsim.yaml`; tier `bps` drives both TM pacers (`engine.py:1983`, `simulation.yaml` ttc block) |
| `subsystems/payload.yaml` | imager, storage (`schemas.py:306`) | No — restart simulator | Medium; planner budget reads `total_storage_mb`/`image_size_mb` |
| `subsystems/fdir.yaml` | FDIR enable + rules (`schemas.py:326-328`) | No — restart simulator | High — changes autonomy |
| `subsystems/memory_map.yaml` | — | **Must not edit: DEAD.** `load_memory_map()` (`loader.py:183-188`) targets it but **is never called**; both real readers use `memory/memory_map.yaml` (`memory_map.py:211-212`, `smo-mcs/server.py:608`) | Zero — and that is the hazard: edits appear to work and do nothing |
| `memory/memory_map.yaml` | the real on-board address space (S6) | No — restart simulator + MCS | High — S6 dump/check addresses |
| `telemetry/parameters.yaml` | parameter id/name/units/subsystem (`schemas.py:341-347`) | No — restart everything | **Interface-defining.** Names are referenced by `mcs/displays.yaml`, procedures, report specs; ids by `limits.yaml`, `s12_definitions.yaml`, `s19_rules.yaml` |
| `telemetry/hk_structures.yaml` | **the HK wire format** — order, `pack_format`, `scale`, `interval_s` (`schemas.py:330-339`) | No — restart everything | **Highest.** Changing order/format silently invalidates **every already-archived dump**, because dumps store raw bytes and the decoder is config-driven (`tm_report.py:101-124`). Any change must bump a catalogue version stamped into reports |
| `commands/tc_catalog.yaml` | TC definitions, `func_id`, `position`, criticality (`schemas.py:362-374`) | No — restart MCS (+ simulator validators) | High — `positions.yaml allowed_func_ids` and procedures reference it |
| `events/event_catalog.yaml` | event ids/names/severities (`schemas.py:376-386`) | No — restart simulator + MCS | High — ids are S19 triggers (`s19_rules.yaml` `kind: event`) |
| `monitoring/s12_definitions.yaml` | on-board limit monitors | No — restart simulator | High — drives S12 alarms and `kind: param` S19 triggers |
| `monitoring/s19_rules.yaml` | event-action autonomy (`s19_rules.yaml:1-29`) | No — restart simulator | **Very high.** File itself documents 17/26 rules that once dual-fired (defect #70) and lists known trigger drift |
| `fdir/fault_propagation.yaml` | fault cascade + shed stage thresholds | No — restart simulator | High; `soc_threshold` is cross-checked against `simulation.yaml` at load with only a warning (`engine.py:701`) |
| `fdir/procedures/*.yaml` | on-board recovery procedures | No — restart simulator | Medium |
| `obcp/*.yaml` | on-board control procedures (S18) | No — restart simulator (`engine.py:286`) | Medium |
| `scenarios/*.yaml` | training scenarios (`schemas.py:555-561`) | No — restart simulator (`engine.py:375`); **must not edit while that scenario is running** | Low blast radius, high pedagogic value — the safest "student-visible" edit surface |
| `mcs/limits.yaml` | ground yellow/red bands (`schemas.py:349-355`) | No — restart MCS | Medium; also the basis for report `ool_*` |
| `mcs/displays.yaml` | display pages/widgets (`schemas.py:563-591`) | No — restart MCS | Medium; widget `parameter` strings must exist in `parameters.yaml` |
| `mcs/positions.yaml` | **role/permission model**: `allowed_services`, `allowed_func_ids`, `visible_tabs` (`schemas.py:577-588`) | No — restart MCS | High — it is the only access control in the suite; must be instructor-only |
| `mcs/pus_services.yaml` | PUS service enablement (`schemas.py:632`) | No — restart MCS | Medium |
| `planning/planner.yaml` | horizons, refresh, budget, `alignment.on_divergence` (`planner_config.py:186-196`) | **Partially** — `?recheck=1` re-reads it for alignment (`smo-planner/server.py:865-871`); everything else needs a planner restart | Medium |
| `planning/ground_stations.yaml` | **RF augmentation only** (G/T, `tx_freq_mhz`/`rx_freq_mhz`, antenna) — geometry is a mirror | No — restart MCS/planner | High. Geometry divergence is only *warned* about (`smo-mcs/server.py:791-799`); frequency divergence is checked by **nothing** |
| `planning/activity_types.yaml`, `standard_plans.yaml`, `imaging_targets.yaml` | planning inputs | No — restart planner | Low–medium |
| `procedures/**/*.md`, `procedures/procedure_index.yaml`, `manual/*.md` | operator documentation | No — restart MCS/doc viewer | Low. **Markdown editor, not a schema form** |
| `rfsim.yaml` | RF bridge; **already strict — unknown keys raise `ConfigError`** (`smo-rfsim/config.py:38-44, 76-88`) | No — restart rfsim | High with `--rf`; the model to copy |
| `aocs/aocs_mode_codes.yaml` | — | **Must not edit: DEAD.** No reader in any `.py` or `.html` | Zero; same trap as `subsystems/memory_map.yaml` |
| `xtce/eosat1.xml`, `smdl/*.xml` | **generated artefacts** of `tools/generate_xtce.py` / `generate_smdl.py`; nothing in `packages/` reads them | **Must not edit — regenerate** | Zero at runtime; hand-edits are lost on regeneration |

### Validation design

Four gates, run in order, all before anything touches `configs/`.

**1. Schema, and never silently drop a key.** The schemas declare no `model_config` at all (`grep 'extra=' schemas.py` → empty), so pydantic v2's default `extra="ignore"` applies. That is exactly how `mission.yaml`'s whole `ground_segment:` block became decorative (`mission.yaml:30-46`), how `eps.yaml`'s `solar_panels`/`power_lines` were dead until declared (`schemas.py:87-91`), why `tcs.yaml`'s `thermal_coupling` had to be added (`schemas.py:124-128`), why `orbit.yaml`'s `t0_epoch` was dropped (`schemas.py:54-59`), and why `rfsim.yaml`'s `channel.frequency_hz` was dead for the project's life (`smo-rfsim/config.py:8-10`).

The editor **must not** rely on the loader to detect this. Two-step:
- *Immediately, with no runtime risk:* `unknown_key_report(path, raw_yaml, model)` walks the parsed YAML against `model.model_fields` recursively (descending into `BaseModel` and `list[BaseModel]` fields) and returns every key the loader will ignore. The editor renders these as a red **"IGNORED BY LOADER"** band on the form, next to the key, with the schema path. The schema-generated form itself is built from `model.model_json_schema()`, so it can only ever offer real fields — a form-driven edit cannot introduce a dead key.
- *Then, as its own work package:* declare the known offenders (`MissionConfig.ground_segment` as an informational sub-model) and flip `model_config = ConfigDict(extra="forbid")` on the schema set, guarded by a test that loads every file in `configs/eosat1/**` and asserts zero extras.

**2. Range and physical plausibility.** Migrate the numbers already asserted in prose into `Field(ge=…, le=…)` (`TMRateTier` already does the pattern with validators at `schemas.py:180-201`), plus a `plausibility.py` rule set: `soc_stage_thresholds_pct` strictly descending; `load_shed.bus_voltage_v` between `battery.soc_0_v` and `soc_100_v`; `tm_rate_tiers[].bps` ascending and each tier's channel rate ≥ the 1351.5 bps of HK the shipped structures generate (the arithmetic in `simulation.yaml:44-52`); `min_elevation_deg` 0–90; `lat_deg` ±90, `lon_deg` ±180; `interval_s` > 0; `pack_format` a valid `struct` code; TLE lines 69 chars with a valid checksum; `t0_epoch` parseable ISO-8601.

**3. Cross-file consistency** — a registry of `CrossCheck(id, files, severity, run(configs) -> [Issue])` in `smo_common/config/crosschecks.py`, usable from the editor, from service start-up, and from a CLI/CI gate exactly like `planner_config.main()` (`planner_config.py:394-418`). Seed set, all motivated by something real in the tree:

| id | What it compares | Today |
|---|---|---|
| `stations.geometry` | `orbit.yaml ground_stations` ↔ `planning/ground_stations.yaml` ↔ `planning/planner.yaml stations` | `check_station_alignment` covers only 2 of the 3 files (`planner_config.py:233-349`); the MCS separately warns on the 3rd pairing (`smo-mcs/server.py:791-799`). Unify, honour `alignment.on_divergence` (`planner_config.py:154-155`) |
| `stations.membership` | station in planner but not orbit, or vice-versa | covered by the above; keep the messages verbatim (`planner_config.py:303-312`) |
| `rf.frequencies` | `ttc.yaml ul_freq_mhz/dl_freq_mhz` (`:16-17`) ↔ `ground_stations.yaml tx_freq_mhz/rx_freq_mhz` (`:22-23`…) ↔ `rfsim.yaml channel.frequency_hz/uplink_frequency_hz` (`:67-68`) ↔ `ttc_basic.UL_BAND_MHZ/DL_BAND_MHZ` ↔ `tc_catalog TTC_SET_*_FREQ` ranges | **Nothing checks this.** All three files assert agreement only in header comments (`ttc.yaml:1-14`, `rfsim.yaml:3-10`, `ground_stations.yaml:1-3`). This is the motivating example — implement it first |
| `eps.shed_ladder` | `simulation.yaml eps.load_shed.soc_stage_thresholds_pct` ↔ `fdir/fault_propagation.yaml` stage `soc_threshold` | warn-only, buried in engine start-up (`engine.py:701`) |
| `hk.wire` | every `hk_structures.yaml param_id` ∈ `parameters.yaml`; `pack_format` valid; packed size within packet limits | none |
| `monitoring.triggers` | `s19_rules.yaml` `kind: event` ids ∈ `event_catalog.yaml`; `kind: param` ids ∈ `parameters.yaml` and ideally monitored by `s12_definitions.yaml`; `action_func_id` ∈ `tc_catalog.yaml func_id` | none at load; the known drift is recorded in a *test* (`s19_rules.yaml:22-27`) |
| `limits.coverage` | `mcs/limits.yaml param_id` ∈ `parameters.yaml`; red band ⊇ yellow band; ground limits vs `s12_definitions.yaml` on-board limits (divergence is legitimate — **display** it) | none |
| `displays.refs` | `mcs/displays.yaml` and `positions.yaml` widget `parameter` names ∈ `parameters.yaml`; `allowed_func_ids` ⊆ `tc_catalog`; `visible_tabs` ⊆ known tabs; `manual_sections` ⊆ `manual/` | none |
| `planner.budget` | `planner.yaml budget` `null` fields resolve against `eps.yaml`/`payload.yaml`/`ttc.yaml` (`planner_config.py:100-120`) | resolved at runtime, never validated |
| `dead_config` | any file the loader never reads (`subsystems/memory_map.yaml`, `aocs/aocs_mode_codes.yaml`) | none |
| `store.dump_dir` | `mission.yaml dump_dir` vs `SMO_DUMP_DIR` in each running service (`dump_store.py:110-131`) | none |

Each `Issue` carries `severity ∈ {error, warn, info}`, the exact `file:line` of each side (from a comment-preserving parse), a one-line explanation, and — where mechanical — a **suggested fix** the UI can apply with one click ("set `ground_stations.yaml[Troll].lat_deg` to `-72.012` to match `orbit.yaml`").

**4. Diff / preview before apply.** Three views on one screen: (a) **textual** unified diff of the YAML, produced from a `ruamel.yaml` round-trip so comments, ordering and the many explanatory blocks in these files survive — those comments *are* the teaching material and a naive `yaml.safe_dump` would destroy `simulation.yaml`, `s19_rules.yaml`, `ttc.yaml`; (b) **semantic** diff of `model_dump()` before/after, which is what actually changes behaviour and which catches "this comment-only edit changes nothing"; (c) **effects**: restart scope (which services), cross-check deltas (issues introduced/resolved), and reverse references ("3 scenarios and 2 procedures name this parameter"). Apply is blocked while any `error`-severity issue stands, unless the instructor explicitly overrides with a mandatory reason recorded in the audit log.

### Version control recommendation

**Options.**
- *(a) Commit into the main repo.* Diffs and history for free, and the config genuinely lives there. But: the working tree is uncommitted today (and `.git` is not even present in this snapshot), so the first UI commit would sweep up unrelated source changes, `.DS_Store`, `configs/data/.fuse_hidden*` and the vendor symlink mess (`fix_vendor_symlinks.sh`); instructor config edits would interleave with development history; and it requires `git` on the classroom server, which `deploy/install.ps1` does not install.
- *(b) Shadow git repo scoped to `configs/`.* Real diffs, real history, tags, `log -p`, blame, cheap storage, portable — without touching the source repo or its dirty tree.
- *(c) Snapshot/revision store.* Trivially portable, zero dependencies, but re-implements diff, history and revert badly, and gives up everything a colleague could do with the history offline.

**Choice: (b), with (c) as an automatic fallback behind one interface.** Concretely: `workspace/config-history/` is a bare-ish git dir whose work-tree is `configs/` —
`git --git-dir=workspace/config-history/.git --work-tree=configs …`. It is created and `git add -A` seeded on first use ("revision 0 — initial baseline"), never interacts with the source repo, and is excluded from it via `.gitignore` (`workspace/` already is). `HistoryBackend` has two implementations, `GitHistoryBackend` and `SnapshotHistoryBackend` (timestamped zip + `index.json`, selected automatically when `git` is not on `PATH`); the API surface is identical, so a Windows lab without git degrades to snapshots rather than to nothing.

- **Revision** = a commit. Message = one-line summary + a machine-readable trailer (`SMO-Actor:`, `SMO-Role:`, `SMO-Reason:`, `SMO-CrossCheck: 2 warn 0 error`, `SMO-RestartScope: simulator,planner`), so the audit survives a bare `git clone` of the history.
- **History browsing** = `git log --follow -- <path>`; **diff between revisions** = `git diff a..b -- <paths>`; **revert** = `git checkout <rev> -- <paths>` staged as a *new* revision (never a rewrite, so a bad revert is itself revertible).
- **Named baselines / "mission config sets"** = annotated tags `baseline/<name>` plus `workspace/config-history/baselines.json` (name, description, author, created, revision, checks-clean flag). Applying a baseline is `checkout <tag> -- configs/` staged as a new revision. Per-exercise variants are branches (`exercise/debris-impact`) — an instructor can prepare "the config set for the CERISE ADCS exercise" ahead of class and switch to it between sessions, which is the single most useful thing this feature buys.

### API

Hosted on the **simulator's instructor server (port 8080)**. Reasons: it is already "the instructor's door" in `docs/DEPLOYMENT.md` §2.1; it owns `config_dir` and is the process that must be restarted for a change to take effect (`engine.py:85-141`); and the MCS is explicitly the *students'* port ("9090 ← the only port a student strictly needs", `DEPLOYMENT.md` §2.4), so putting a config-write surface there would put it in front of every student. The MCS and planner get **read-only** proxies (`GET /api/config/*`) so students can browse the configuration and its history — valuable teaching — but no mutating route.

There is **no authentication anywhere in the suite** (`DEPLOYMENT.md` §2.4 says so outright). Introduce the minimum that works: `SMO_INSTRUCTOR_TOKEN` (generated by `start.sh` if unset and printed in the start-up banner), required as `Authorization: Bearer …` on every mutating endpoint; read endpoints open. This is a classroom trust boundary, not a security product — say so in the docs.

```
GET  /api/config/tree                      inventory + classification + sha256 + drift-vs-running
GET  /api/config/file?path=…               {text, parsed, model_json_schema, unknown_keys[], readonly, class}
GET  /api/config/schema?path=…             JSON Schema (pydantic model_json_schema)
POST /api/config/validate    {changes[]}   schema + plausibility + cross-file  -> issues[]
POST /api/config/diff        {changes[]}   {text_diff, semantic_diff, effects{restart_scope, refs}}
POST /api/config/apply       {message, reason, changes[], override_errors?}   [token]
                                           -> {revision, issues[], restart_required[]}
GET  /api/config/history?path=&limit=      [{revision, ts, actor, message, files[], tags[]}]
GET  /api/config/revisions/{rev}?path=     file content at that revision
GET  /api/config/diff/{a}..{b}?path=       diff between revisions
POST /api/config/revert      {rev, paths[]}                                   [token]
GET  /api/config/baselines                 [{name, description, revision, created, author, clean}]
POST /api/config/baselines   {name, description}                              [token]
POST /api/config/baselines/{name}/apply                                       [token]
GET  /api/config/crosschecks?ids=          run the registry standalone -> issues[]
GET  /api/config/audit?limit=              append-only audit entries
POST /api/config/reload      {paths[]}     [token]  reload the small live-safe set; 409 otherwise
POST /api/sim/restart        {confirm}     [token]  restart the engine with the on-disk config
GET  /api/config/drift                     per-service: config hash at start vs on disk now
```

CLI parity, for CI and for instructors who prefer a terminal: `python -m smo_common.config.crosschecks --config configs/eosat1 [--json]`, exit 0 clean / 2 divergent — the same contract `planner_config.main()` already offers (`planner_config.py:394-418`), so the existing station check folds in rather than being duplicated.

### UI spec

A single page at `http://<server>:8080/config`, three columns.

**Left — file tree.** Grouped exactly as the classification table: *Mission & orbit*, *Simulation tuning*, *Spacecraft subsystems*, *Telemetry & commands* (marked ⚠ interface-defining), *Monitoring & FDIR*, *Scenarios*, *MCS*, *Planning*, *RF*, *Documentation*, *Generated / do-not-edit* (greyed, non-selectable, with the reason on hover), *Dead config* (struck through, "no code reads this file"). Each node shows a dot when it differs from the running services' loaded config, and a lock when it is instructor-only.

**Centre — editor**, two tabs.
- **Form** (default): generated from `model_json_schema()`. Objects → fieldsets, `list[BaseModel]` → repeatable cards with add/remove/reorder, enums → selects, numbers → inputs carrying the schema's min/max as HTML constraints and the pydantic `description` as help text (the schemas are already richly described — `schemas.py:10-24`, `planner_config.py:46-183` — so this is free documentation). Any key present in the file but absent from the schema appears at the bottom of its section in a red **"IGNORED BY LOADER"** band with a link to the explanation; nothing can be silently dropped and nothing can be silently added.
- **YAML**: plain textarea with server-side lint on blur (`/api/config/validate`), preserving comments. Switching Form→YAML round-trips through ruamel so comments survive; switching YAML→Form re-parses and reports anything the form cannot represent rather than eating it.

**Right — status rail**, always visible: *Validation* (schema + plausibility errors, click to focus the field), *Cross-checks* (grouped by check id; each issue shows both sides with `file:line`, severity chip, and a **Fix** button where a mechanical fix exists), *Effects* (restart scope per service, references), *History* (last 10 revisions for this file, each with author/message/time, click → diff, right-click → revert).

**Review & Apply modal**: changed-file list, side-by-side text diff, semantic diff, cross-check delta ("resolves `rf.frequencies`; introduces `stations.geometry` (warn)"), restart scope, mandatory commit message, mandatory reason if overriding an error, and a checkbox "restart the simulator now" (disabled with an explanation while a scenario is running).

**Baselines panel**: list of named baselines with description and cleanliness, "Create baseline from current", "Apply baseline" (shows the full diff first), and an exercise switcher.

**Read-only student view** on the MCS: same tree and same form rendering, all inputs disabled, history and diffs browsable, banner "Configuration is read-only from this console." Being able to *read* `s19_rules.yaml` next to the FDIR display, and to see that the instructor changed the shed thresholds at 14:07, is a teaching feature, not a leak.

### Safety rules

1. **Nothing is edited in place.** Changes are staged, validated, then written with `os.replace()` per file under a single process-wide lock, and committed to history in the same critical section. A crash mid-apply leaves either the old or the new file, never a half-written YAML that kills the next engine start-up.
2. **A config edit never affects a running simulation, and the UI must say so.** The engine reads config once (`engine.py:134-141`). Each service records the sha256 of every file it loaded at start; `/api/config/drift` compares that to disk; the editor shows a persistent amber banner *"Simulator is running with configuration from revision r14; 3 files have changed since"* with a **Restart simulator** button. Silence here is the failure mode — an instructor who edits a threshold and sees no change will conclude the editor is broken.
3. **Refuse to apply while a scenario is active** (`scenario_engine.is_active()`, surfaced at `instructor/app.py:176-195`) unless explicitly forced with a reason; always refuse to edit the `scenarios/*.yaml` file of the *running* scenario.
4. **Breakpoints are state-only and config-blind.** `BreakpointManager.save/load` (`breakpoints.py:20-167`) captures params, subsystem state, TM stores, OBCP, memory, HK enables and orbit clock — but no configuration. Loading a breakpoint taken under config A into an engine started under config B silently mixes them, and breakpoints persist across restarts (`engine.py:2482-2500`). **Stamp every breakpoint with the config revision id and a config hash at save; at load, compare and warn loudly (refuse by default, force available) on mismatch** (`engine.py:2522-2536`). Also list, in the editor, which persisted breakpoints a pending change would invalidate.
5. **Interface-defining files get an extra gate.** Editing `telemetry/hk_structures.yaml` or `parameters.yaml` requires a typed confirmation and bumps a catalogue version that the report generator stamps into every report, because existing dumps in `workspace/dumps` are raw bytes decoded by that config (`tm_report.py:101-124`) and become undecodable-but-not-obviously-wrong. The editor lists how many archived dumps predate the change.
6. **Generated and dead files are never writable** through the editor: `xtce/*.xml` and `smdl/*.xml` (regenerate via `tools/generate_xtce.py` / `generate_smdl.py`), `subsystems/memory_map.yaml`, `aocs/aocs_mode_codes.yaml`.
7. **Errors block, warnings inform.** For the station check, honour the file's own policy: `planner.yaml alignment.on_divergence: warn|error|ignore` (`planner_config.py:154-155`) maps directly to blocking behaviour, so the instructor's declared strictness is respected rather than second-guessed.
8. **The audit log is append-only** JSONL at `workspace/config-history/audit.jsonl` — `{ts, actor, role, ip, action, paths[], revision, message, reason, issues_summary, restart_scope, overridden}` — mirrored into commit trailers so it survives a clone. It is never rewritten by revert; a revert is a new entry.
9. **Token-gated mutation, open read.** All write endpoints require `SMO_INSTRUCTOR_TOKEN`; the MCS and planner mount read-only proxies only. The token is printed once at start-up and never logged with requests.
10. **Every apply is revertible in one click**, and the editor proves it: after apply it shows "reverted by clicking here" with the exact revert revision it would create.

---

## Work packages

Effort: S ≈ ≤1 day, M ≈ 2–4 days, L ≈ 1–2 weeks. "MVP" = ship before showing it to a class.

### A — Report generator

| # | Package | Files | Acceptance criteria (testable) | Effort | Depends | MVP |
|---|---|---|---|---|---|---|
| A1 | **One correct decoder set** — move `pus_reports.py` into smo_common, add S3/S5/S1 decoders, **fix the S5 body parse** | new `smo-common/.../reporting/decode.py`; `smo-mcs/.../pus_reports.py` → re-export shim; `tools/tm_report.py:100-179` deleted in favour of it | `tests/test_common/test_report_decode.py`: a packet built by `TMBuilder.build_event_packet("SAFE MODE ENTRY")` decodes to `description == "SAFE MODE ENTRY"` exactly (today's `tm_report.py:172` yields a byte-prefixed string); S1.2 decodes `error_code` + reason; S12.9 18-byte form decodes all 7 fields; existing `tests/test_mcs` pus_reports tests still pass unchanged | M | — | ✅ |
| A2 | **Timestamp every TM packet** — give `_pack_tm` a real default CUC | `smo-common/.../telemetry/tm_builder.py:143-144`; verify all 22 `service_dispatch.py` call sites | Property test: for every `_pack_tm` call site, a packet built with a non-zero time source has `secondary.cuc_time != 0`; a dump containing S1/S12 packets yields a strictly monotonic time axis; `dump_store.summarise_dump` `first_cuc/last_cuc` unchanged for HK-only dumps | S | — | ✅ |
| A3 | **`TelemetryDataset`** — one decode pass over N dumps → series, events, alarms, verifications, store status, census; `zero_cuc_policy` | `reporting/dataset.py` | Decoding the same dump twice gives identical output; a dump with a truncated trailer stops cleanly (matches `dump_store.py:161-163`); `zero_cuc_policy: interpolate` places a zero-CUC S1 packet strictly between its neighbours; 10 MB of dumps decodes in < 5 s | M | A1 | ✅ |
| A4 | **Report spec model** — pydantic, `extra="forbid"`, `validate_spec()` | `reporting/spec.py` | An unknown key anywhere in a spec is an error naming the path; a `parameters:` entry not in `parameters.yaml` is an error naming the parameter; all three shipped templates validate clean | M | — | ✅ |
| A5 | **Period resolution** — pass / orbit / day / breakpoint span / dump / absolute / relative | `reporting/periods.py` | `kind: pass, select: latest` over the 48 h from `orbit.yaml t0_epoch` returns the same window `tools/pass_gap_analysis.py` reports; orbit boundaries are ~94.5 min apart; a breakpoint span resolves from two `workspace/breakpoints/*.json` names; period resolution never reads the dump filename timestamp when `timebase: cuc` | M | A3 | ✅ |
| A6 | **Analysis** — stats, OOL time from `mcs/limits.yaml`, gap detection from `interval_s`, pass/orbit binning, correlation | `reporting/analysis.py` | Synthetic series with a known 40 s hole in SID 1 (1.0 s cadence) reports exactly one gap of 40 s and `missing_pct` within 0.1 %; a parameter held 30 s above `red_high` reports `ool_red_s == 30`; an S12 entry with no clear is reported as unpaired; an S19 rule whose trigger fired is correlated with `latency_s ≥ 0` | L | A3, A5 | ✅ |
| A7 | **HTML renderer** — self-contained, inline SVG, no JS required | `reporting/render_html.py` | Output contains no `<script src=`, no `http://`/`https://` references, and no `/vendor` path; opens correctly with the network disabled; every plot has `<title>`+`<desc>` and a `<details>` data table; a 24 h report over 6 SIDs is < 8 MB with `embed_dataset: false` | M | A4, A6 | ✅ |
| A8 | **Three shipped templates** | `reporting/templates/{pass_report,daily_ops_review,anomaly_investigation}.yaml` | Each renders end-to-end against a recorded fixture dump set in `tests/fixtures/dumps/`; each produces every declared section with either data or an explicit "no data in period" statement — never a silently empty section; a content test asserts the pass report contains an AOS/LOS/max-elevation line and a command-outcome table | M | A7 | ✅ |
| A9 | **HTTP API + `register_report_routes`** | `reporting/api.py`; mount in `smo-mcs/server.py` (beside `:979-980`) and `tools/delayed_tm_viewer.py:270-344` | `GET /api/reports/templates` returns 3 on both hosts; `POST /render` returns a downloadable HTML; a filename with `../` is rejected 400 exactly as `dump_store.resolve_dump_file` does; a 24 h render does not block the MCS websocket (measured: TM broadcast latency unchanged during render) | M | A7, A8 | ✅ |
| A10 | **CLI + retire `tools/tm_report.py`** | `reporting/cli.py`; `tools/tm_report.py` → 20-line shim that warns and delegates | `python tools/tm_report.py workspace/dumps/` still produces a report file (backwards compatible); `python -m smo_common.reporting --template daily_ops_review --period latest-day -o x.html` works; `tests/test_delayed_tm_viewer.py` still passes | S | A8 | ✅ |
| A11 | **Viewer UI — report panel** | `tools/delayed_tm_viewer.py` `INDEX_HTML` | Selecting a template + period + Preview renders sections in-page; Download produces the same bytes as the API; validation errors from a hand-edited spec are listed with their spec path | M | A9 | ✅ |
| A12 | **MCS UI — REPORTS link + page** | `smo-mcs/static/index.html` (beside `:850`) | The header link is present for every position; `/reports` lists previously generated reports; works from a second machine on the network (relative href, same reasoning as `index.html:847-849`) | S | A9 | ✅ |
| A13 | **PDF build step** | `reporting/render_pdf.py` | With a PDF backend importable, `format=pdf` returns a valid multi-page PDF; without one, returns 501 with an actionable message and the UI button is disabled with a tooltip | S | A7 | later |
| A14 | **CSV / JSON export + `embed_dataset`** | `reporting/render_csv.py` | Per-parameter CSV round-trips to the same statistics as the HTML table; the embedded JSON in an appendix re-loads into `TelemetryDataset` and reproduces the report | S | A6 | later |
| A15 | **Ground-archive merge** — commands + alarms from MCS SQLite | `reporting/dataset.py`, `smo-mcs/server.py` | With `ground_archive.enabled: true`, the command table shows command *names* (from `tc_log`, `tm_archive.py:59-69`), not just request-ids; with it false, the report says so rather than showing an empty table | M | A3, A9 | later |
| A16 | **Report templates as a teaching artefact** — doc page + a worked example per template | `docs/REPORTING_GUIDE.md`, `configs/eosat1/manual/` | Each template's YAML is reproduced with commentary explaining what a real Pass/Daily/Anomaly report contains and why each section exists | S | A8 | later |

**Ordering:** A1 → A2 → A3 → A4/A5 (parallel) → A6 → A7 → A8 → A9 → A10/A11/A12 → A13–A16.

### B — Config editor

| # | Package | Files | Acceptance criteria (testable) | Effort | Depends | MVP |
|---|---|---|---|---|---|---|
| B1 | **Unknown-key reporter** (no runtime change) | `smo-common/.../config/introspect.py` | Run over `configs/eosat1/**` it reports exactly the known dead keys, including `mission.yaml ground_segment` (`mission.yaml:30-46`); a test asserts the list is non-empty today and pins its contents so a regression is visible | S | — | ✅ |
| B2 | **Config registry** — one table of file → purpose → schema → class → restart scope → readers | `smo-common/.../config/registry.py` | Every file under `configs/eosat1/**` is either classified or explicitly listed as unclassified; a test fails when a new config file is added without a registry entry; `subsystems/memory_map.yaml` and `aocs/aocs_mode_codes.yaml` are classified `dead`, `xtce/`/`smdl/` as `generated` | M | — | ✅ |
| B3 | **Cross-check registry + CLI** — starting with `rf.frequencies`, `stations.geometry`, `eps.shed_ladder`, `hk.wire` | `smo-common/.../config/crosschecks.py`; fold in `planner_config.check_station_alignment` | `python -m smo_common.config.crosschecks --config configs/eosat1` exits 0 on the shipped config; mutating `ttc.yaml:17 dl_freq_mhz` to 2300.0 produces an `error` naming `ground_stations.yaml` and `rfsim.yaml` with line numbers and exits 2; mutating a `planner.yaml` station lat reproduces the existing `planner_config` message verbatim | L | B2 | ✅ |
| B4 | **Plausibility rules** | `smo-common/.../config/plausibility.py` | Reversing `soc_stage_thresholds_pct` is an error; a `tm_rate_tiers` bps below the 1351.5 bps HK load is a warning quoting `simulation.yaml:44-52`; an invalid `pack_format` is an error; a bad TLE checksum is an error | M | B2 | ✅ |
| B5 | **Comment-preserving read/write + diff** | `smo-common/.../config/roundtrip.py` (ruamel) | Load-and-save of every file in `configs/eosat1/**` is byte-identical; changing one scalar changes exactly one line in the unified diff; the header comment blocks of `simulation.yaml`, `s19_rules.yaml`, `ttc.yaml` survive intact | M | — | ✅ |
| B6 | **History backend** — git-shadow + snapshot fallback | `smo-common/.../config/history.py` | On a machine with git, apply → `git log` shows the commit with the `SMO-Actor` trailer; revert restores content and creates a *new* revision; with `git` removed from PATH the same test passes against the snapshot backend; the source repo's status is unaffected (verified by hashing the parent dir listing) | L | B5 | ✅ |
| B7 | **Apply pipeline** — stage → validate → diff → atomic write → commit → audit | `smo-common/.../config/apply.py` | An error-severity issue blocks apply (409) unless `override_errors` + `reason`; a simulated crash between file writes leaves every file either fully old or fully new; the audit JSONL gains exactly one entry per apply and is never rewritten | M | B3, B4, B5, B6 | ✅ |
| B8 | **HTTP API on the instructor server + token** | `smo-simulator/.../instructor/app.py` (beside `:21-31`); `start.sh` prints the token | All mutating endpoints return 401 without the token; read endpoints work without it; `GET /api/config/tree` lists the classification; `POST /apply` returns a revision id; existing instructor endpoints are unaffected | M | B7 | ✅ |
| B9 | **Drift detection + restart control** | `engine.py` (record load hashes), `instructor/app.py` | `GET /api/config/drift` reports zero drift immediately after start and non-zero after an apply; `POST /api/sim/restart` brings the engine up on the new config and the drift clears; restart is refused while a scenario is active without `confirm` | M | B8 | ✅ |
| B10 | **UI — tree + schema-generated form + YAML tab + validation rail** | `smo-simulator/.../instructor/static/` (new `config.html` or a tab in `index.html`) | Every registry-classified file renders a form from its schema with no per-file hand-written code; unknown keys show the red IGNORED band; generated/dead files are non-editable with a stated reason; edits round-trip through the YAML tab without losing comments | L | B8 | ✅ |
| B11 | **UI — Review & Apply modal (diff + effects + cross-checks)** | same | The modal shows text diff, semantic diff, restart scope and cross-check delta; apply is disabled while an error stands; a mandatory message is enforced | M | B10 | ✅ |
| B12 | **UI — history, diff between revisions, revert** | same | History lists revisions per file; selecting two shows a diff; revert produces a new revision and the file matches the chosen one byte-for-byte | M | B10, B6 | ✅ |
| B13 | **Named baselines / mission config sets** | `config/history.py`, UI panel | Creating a baseline tags the current revision; applying it produces the exact tagged content as a new revision; the diff is shown before applying; baselines survive a service restart | M | B12 | later |
| B14 | **Breakpoint ↔ config revision stamping** | `breakpoints.py:20-100`, `engine.py:2482-2536` | A breakpoint saved under revision r5 and loaded under r7 logs a warning and is refused by default; `force: true` loads it; old breakpoints without a stamp load with a warning (backwards compatible) | S | B7 | later |
| B15 | **Read-only config browser on MCS/planner** | `smo-mcs/server.py`, `smo-planner/server.py` | Students can browse the tree, files and history from 9090; every mutating route is absent (404), not merely disabled | S | B8 | later |
| B16 | **Flip schemas to `extra="forbid"`** — declare `MissionConfig.ground_segment` and any other known extras first | `smo-common/.../config/schemas.py` | `tests/test_common/test_no_dropped_keys.py` loads every file under `configs/eosat1/**` and asserts zero unknown keys; every existing test suite still passes; `configs/eosat1/mission.yaml` is unchanged | M | B1, B2 | later |
| B17 | **Live-reload for the safe subset** (`simulation.yaml speed/failures/scenario_defaults`, `scenarios/*`, `mcs/limits.yaml`, `mcs/displays.yaml`) | `engine.py`, `smo-mcs/server.py`, `instructor/app.py` | `POST /api/config/reload` with a live-safe path takes effect without restart and is observable in `/api/state`; with a restart-required path it returns 409 naming the services | L | B9 | later |
| B18 | **Markdown editor for `procedures/**` and `manual/**`** | UI | Preview renders the markdown; save creates a revision; the procedure index cross-check flags a procedure file not listed in `procedure_index.yaml` | M | B10 | later |
| B19 | **CI gate** | `tests/test_content/test_crosschecks.py` | The full cross-check registry runs in CI against the shipped config and fails the build on any `error` | S | B3 | later |

**Ordering:** B1 → B2 → B3/B4/B5 (parallel) → B6 → B7 → B8 → B9 → B10 → B11 → B12 → B13–B19.

**Cross-cutting order between A and B:** A1–A3 and B1–B3 are independent and can run in parallel. If only one can ship first, **ship A** — the report generator is additive, has no way to damage a running class, and immediately raises the value of the dumps already being produced. B touches the files every service reads at start-up, so it needs B3/B4/B5 to be solid before any UI exists.agentId: ac2998d996c1ca3fb (use SendMessage with to: 'ac2998d996c1ca3fb', summary: '<5-10 word recap>' to continue this agent)
<usage>subagent_tokens: 255168
tool_uses: 86
duration_ms: 849703</usage>
