"""FDIR display model — the state behind the MCS FDIR window.

The textbook (Ch 5.20 §2 pp. 314-316, Ch 7.5 pp. 338-339, Ch 12 pp. 406-411,
Ch 15.2-15.4 pp. 497-499) expects the operator console to carry an FDIR
display: the current alert level and spacecraft mode, the on-board monitoring
table, the event-action table, an alarm/anomaly journal and the recovery
controls. Before this module the MCS had an alarm list and nothing else —
``FDIRAlarmPanel.update_s12_rules`` / ``update_s19_rules`` / ``set_fdir_level``
were never called from anywhere in the repo, so ``/api/displays/fdir-alarms``
reported "0 rules / nominal" no matter what the spacecraft was doing.

TIME: every ``ts`` / ``now`` argument here is **SIM TIME** as a Unix epoch
float — ``MCSServer.sim_now_ts()`` — because the journal rows and report ages
this model produces are read alongside telemetry ages and alarm timestamps,
which are all on the simulator's clock (spec Part D). The ``_time.time()``
fallbacks exist only so the model is usable standalone in a unit test; the MCS
always passes its clock in.

This class owns the *observed* FDIR state:

* the **reference database** — S12 monitor and S19 rule definitions read from
  ``configs/<mission>/monitoring/*.yaml``. This is ground knowledge: names and
  descriptions are not downlinked, only ids and limits are.
* the **on-board truth** — the same tables as reported by TM(12,13) and
  TM(19,128). When a report arrives it *replaces* the on-board view, so a
  monitor deleted on board disappears here and a ground/on-board divergence is
  visible rather than assumed away.
* the **live layer** — per-monitor in/out-of-limit status from TM(12,9)/(12,10),
  per-rule fire history inferred by correlating the trigger that fired, and a
  journal of S5 events and S12 transitions in one time-ordered stream.

Everything is derived from telemetry that actually reaches the ground. The one
exception is ``sc_mode``, which the spacecraft does not downlink at all; the
server feeds it in from the simulator state endpoint and it is tagged with its
source so the display can say where it came from.
"""
from __future__ import annotations

import time as _time
from collections import deque
from typing import Any, Optional

# FDIR alert levels, lowest to highest. The names match the existing
# FDIRAlarmPanel vocabulary so the two stay comparable.
FDIR_LEVELS = ("nominal", "equipment", "subsystem", "system")

# Spacecraft mode numbering — MUST match smo_simulator.engine SC_* constants.
SC_MODE_NAMES = {0: "SAFE", 1: "NOMINAL", 2: "SCIENCE", 3: "EMERGENCY"}

_JOURNAL_MAX = 400


class FDIRDisplayModel:
    """Observed FDIR state assembled from telemetry."""

    def __init__(self) -> None:
        # Ground reference database (from config; names/descriptions only).
        self._s12_ref: dict[int, dict] = {}
        self._s19_ref: dict[int, dict] = {}
        self._event_names: dict[int, dict] = {}
        self._func_names: dict[int, str] = {}
        self._param_names: dict[int, str] = {}

        # On-board tables as last reported (None = never reported).
        self._s12_onboard: Optional[dict[int, dict]] = None
        self._s19_onboard: Optional[dict[int, dict]] = None
        self._s12_report_ts: Optional[float] = None
        self._s19_report_ts: Optional[float] = None

        # Live layer.
        self._s12_live: dict[int, dict] = {}
        self._s19_fires: dict[int, dict] = {}
        self._journal: deque[dict] = deque(maxlen=_JOURNAL_MAX)
        self._journal_seq = 0

        # Strip state.
        self._sc_mode: Optional[int] = None
        self._sc_mode_source: str = "unavailable"
        self._last_action_ts: Optional[float] = None
        self._last_action_text: str = ""
        self._monitoring_enabled: Optional[bool] = None

    # ── reference database ────────────────────────────────────────

    def load_reference(self, *, s12: dict[int, dict] | None = None,
                       s19: dict[int, dict] | None = None,
                       events: dict[int, dict] | None = None,
                       funcs: dict[int, str] | None = None,
                       params: dict[int, str] | None = None) -> None:
        """Install the ground reference database (names, descriptions)."""
        if s12 is not None:
            self._s12_ref = dict(s12)
        if s19 is not None:
            self._s19_ref = dict(s19)
        if events is not None:
            self._event_names = dict(events)
        if funcs is not None:
            self._func_names = dict(funcs)
        if params is not None:
            self._param_names = dict(params)

    # ── telemetry ingest ──────────────────────────────────────────

    def ingest_s12_definitions(self, defs: list[dict],
                               ts: Optional[float] = None) -> None:
        """Absorb a TM(12,13) report — replaces the on-board monitor table."""
        now = _time.time() if ts is None else ts
        self._s12_onboard = {int(d["mon_id"]): dict(d) for d in defs}
        self._s12_report_ts = now
        # Drop live status for monitors that no longer exist on board.
        for mon_id in list(self._s12_live):
            if mon_id not in self._s12_onboard:
                self._s12_live.pop(mon_id, None)

    def ingest_s19_definitions(self, defs: list[dict],
                               ts: Optional[float] = None) -> None:
        """Absorb a TM(19,128) report — replaces the on-board rule table."""
        now = _time.time() if ts is None else ts
        self._s19_onboard = {int(d["ea_id"]): dict(d) for d in defs}
        self._s19_report_ts = now

    def ingest_s12_transition(self, transition: dict,
                              ts: Optional[float] = None) -> dict:
        """Absorb a TM(12,9)/(12,10) check transition.

        Returns the journal entry it created so the caller can broadcast it.
        """
        now = _time.time() if ts is None else ts
        param_id = int(transition.get("param_id", 0))
        mon_id = int(transition.get("mon_id", param_id))
        entered = bool(transition.get("entered"))
        live = self._s12_live.setdefault(mon_id, {
            "out_of_limits": False, "violation_count": 0,
            "last_value": None, "last_transition_ts": None,
        })
        live["last_value"] = transition.get("value")
        live["last_transition_ts"] = now
        live["out_of_limits"] = entered
        if entered:
            live["violation_count"] += 1
        # A limit violation is what fires a kind=param S19 rule on board.
        if entered:
            self._note_trigger(param_id, kind=1, ts=now)
        return self._journal_add({
            "ts": now,
            "kind": "s12_in" if entered else "s12_out",
            "mon_id": mon_id,
            "param_id": param_id,
            "value": transition.get("value"),
            "severity": transition.get("severity",
                                       3 if entered else 1),
            "low_limit": transition.get("low_limit"),
            "high_limit": transition.get("high_limit"),
            "label": self.monitor_label(mon_id, param_id),
        })

    def ingest_event(self, event_id: int, severity: int, description: str,
                     ts: Optional[float] = None,
                     rejection: Optional[dict] = None) -> Optional[dict]:
        """Absorb an S5 event into the journal and the rule-fire correlation.

        The simulator mirrors every S12 transition as a synthetic 0x9xxx /
        0xAxxx event so the on-board alarm store and the S19 dispatcher see it.
        Those are recorded against the parameter/monitor they encode rather
        than as an unknown event id, and are not duplicated in the journal —
        the real TM(12,9)/(12,10) report already produced that entry.
        """
        now = _time.time() if ts is None else ts
        from smo_mcs.pus_reports import s12_event_to_param_id
        mirrored = s12_event_to_param_id(event_id)
        if mirrored is not None:
            # Already journalled from the S12 transition report; only use it to
            # attribute rule fires (the on-board dispatcher keys off this id).
            if (event_id & 0xF000) == 0x9000:
                self._note_trigger(mirrored, kind=1, ts=now)
            return None
        self._note_trigger(event_id, kind=0, ts=now)
        meta = self._event_names.get(event_id, {})
        entry = {
            "ts": now,
            "kind": "event",
            "event_id": event_id,
            "severity": severity,
            "description": description,
            "label": meta.get("name", f"EVT_0x{event_id:04X}"),
            "subsystem": meta.get("subsystem", ""),
        }
        # A refused telecommand is an anomaly with a cause and an effect, which
        # is exactly what this journal is for — but as a plain `event` row it
        # read as one more sentence among the S12 transitions. Give it its own
        # kind so it can be filtered, coloured and rendered with the failure
        # code, the code's meaning and the on-board reason broken out
        # (defect #88).
        if rejection:
            entry["kind"] = "cmd_rej"
            entry["identity"] = rejection.get("identity", "")
            entry["stage"] = rejection.get("stage", "")
            entry["error_code"] = rejection.get("error_code")
            entry["error_text"] = rejection.get("error_text", "")
            entry["reason"] = rejection.get("reason", "")
        return self._journal_add(entry)

    def set_sc_mode(self, mode: Optional[int], source: str = "sim_state",
                    ts: Optional[float] = None) -> None:
        """Record the spacecraft mode and where it came from.

        ``ts`` is the SIM TIME of the observation; the journal row it may
        create sits alongside event and S12 rows stamped the same way.
        """
        if mode is None:
            return
        try:
            mode = int(mode)
        except (TypeError, ValueError):
            return
        if mode != self._sc_mode:
            self._journal_add({
                "ts": _time.time() if ts is None else ts,
                "kind": "mode",
                "label": f"SC MODE -> {SC_MODE_NAMES.get(mode, mode)}",
                "severity": 3 if mode in (0, 3) else 1,
            })
        self._sc_mode = mode
        self._sc_mode_source = source

    def set_monitoring_enabled(self, enabled: Optional[bool]) -> None:
        self._monitoring_enabled = enabled

    # ── rule-fire correlation ─────────────────────────────────────

    def _note_trigger(self, trigger_id: int, kind: int, ts: float) -> None:
        """Attribute a trigger to every enabled rule that matches it.

        S19 does not downlink "rule N fired" — the on-board dispatcher just
        executes the S8 function. But the matching condition is fully known to
        ground (id + namespace, per rule), so a fire can be inferred from the
        trigger telemetry that IS downlinked. Rules inferred this way are
        labelled as such in the display; they are not a report from the
        spacecraft.
        """
        for ea_id, rule in self._merged_s19().items():
            if not rule.get("enabled", True):
                continue
            if int(rule.get("trigger_kind", 0)) != kind:
                continue
            if int(rule.get("trigger_id", -1)) != int(trigger_id):
                continue
            rec = self._s19_fires.setdefault(
                ea_id, {"count": 0, "last_ts": None, "last_trigger": None})
            rec["count"] += 1
            rec["last_ts"] = ts
            rec["last_trigger"] = trigger_id
            self._last_action_ts = ts
            self._last_action_text = (
                f"S19 rule {ea_id} ({rule.get('name') or 'unnamed'}) "
                f"-> S8 func {rule.get('action_func_id')}")
            self._journal_add({
                "ts": ts,
                "kind": "rule",
                "ea_id": ea_id,
                "label": rule.get("name") or f"EA_{ea_id}",
                "action": self.func_label(rule.get("action_func_id")),
                "trigger_id": trigger_id,
                "trigger_kind": kind,
                "severity": 3,
                "inferred": True,
            })

    # ── label resolution ──────────────────────────────────────────

    def monitor_label(self, mon_id: int, param_id: Optional[int] = None) -> str:
        ref = self._s12_ref.get(mon_id)
        if ref and ref.get("name"):
            return str(ref["name"])
        if param_id is None and ref:
            param_id = ref.get("param_id")
        if param_id is not None and param_id in self._param_names:
            return self._param_names[param_id]
        return f"MON_0x{mon_id:04X}"

    def param_label(self, param_id: Optional[int]) -> str:
        if param_id is None:
            return ""
        return self._param_names.get(int(param_id), f"0x{int(param_id):04X}")

    def func_label(self, func_id: Optional[int]) -> str:
        if func_id is None:
            return ""
        name = self._func_names.get(int(func_id))
        return f"{func_id} - {name}" if name else f"S8 func {func_id}"

    def trigger_label(self, kind: int, trigger_id: int) -> str:
        if int(kind) == 1:
            return f"param {self.param_label(trigger_id)}"
        meta = self._event_names.get(int(trigger_id))
        if meta:
            return f"event {meta.get('name', '')} (0x{trigger_id:04X})"
        return f"event 0x{trigger_id:04X}"

    # ── merged views ──────────────────────────────────────────────

    def _merged_s12(self) -> dict[int, dict]:
        """Config reference overlaid with the on-board report, if any."""
        if self._s12_onboard is None:
            return {k: dict(v) for k, v in self._s12_ref.items()}
        merged = {}
        for mon_id, ob in self._s12_onboard.items():
            row = dict(self._s12_ref.get(mon_id, {}))
            row.update(ob)
            merged[mon_id] = row
        return merged

    def _merged_s19(self) -> dict[int, dict]:
        if self._s19_onboard is None:
            return {k: dict(v) for k, v in self._s19_ref.items()}
        merged = {}
        for ea_id, ob in self._s19_onboard.items():
            row = dict(self._s19_ref.get(ea_id, {}))
            row.update(ob)
            merged[ea_id] = row
        return merged

    def _journal_add(self, entry: dict) -> dict:
        self._journal_seq += 1
        entry["seq"] = self._journal_seq
        self._journal.appendleft(entry)
        return entry

    # ── derived FDIR level ────────────────────────────────────────

    def derive_level(self, active_alarms: list[dict],
                     load_shed_stage: int = 0) -> str:
        """Derive the FDIR alert level from observed telemetry.

        The spacecraft does not downlink an FDIR level (parameters 0x0700-0x0703
        are declared in parameters.yaml but are in no HK SID), so the level is
        computed on the ground from what IS observable:

        * ``system``    — spacecraft mode SAFE/EMERGENCY, or a CRITICAL alarm,
                          or load shedding at stage 3;
        * ``subsystem`` — a HIGH alarm, or two or more monitors out of limits,
                          or load shedding at stage 1-2;
        * ``equipment`` — any single monitor out of limits or a MEDIUM alarm;
        * ``nominal``   — none of the above.
        """
        sev = {str(a.get("severity", "")).upper() for a in active_alarms}
        ool = sum(1 for v in self._s12_live.values() if v.get("out_of_limits"))
        if (self._sc_mode in (0, 3) or "CRITICAL" in sev
                or int(load_shed_stage or 0) >= 3):
            return "system"
        if "HIGH" in sev or ool >= 2 or int(load_shed_stage or 0) >= 1:
            return "subsystem"
        if ool >= 1 or "MEDIUM" in sev:
            return "equipment"
        return "nominal"

    # ── display payload ───────────────────────────────────────────

    def monitor_rows(self, param_values: dict[int, float] | None = None) -> list[dict]:
        """One row per S12 monitor: definition + live status."""
        values = param_values or {}
        rows = []
        for mon_id, defn in sorted(self._merged_s12().items()):
            live = self._s12_live.get(mon_id, {})
            param_id = defn.get("param_id")
            current = values.get(int(param_id)) if param_id is not None else None
            if current is None:
                current = live.get("last_value")
            low = defn.get("low_limit")
            high = defn.get("high_limit")
            if live.get("out_of_limits"):
                status = "OUT_OF_LIMITS"
            elif live.get("last_transition_ts") is not None:
                status = "IN_LIMITS"
            elif current is not None and low is not None and high is not None:
                # No transition seen yet — compare the cached value ourselves so
                # the column is not blank for the whole first pass. Only
                # meaningful for absolute checks.
                if int(defn.get("check_type", 0)) == 0:
                    status = "IN_LIMITS" if low <= current <= high else "SUSPECT_OOL"
                else:
                    status = "UNKNOWN"
            else:
                status = "UNKNOWN"
            rows.append({
                "mon_id": mon_id,
                "name": self.monitor_label(mon_id, param_id),
                "param_id": param_id,
                "param_name": self.param_label(param_id),
                "check_type": int(defn.get("check_type", 0)),
                "check_type_name": "delta" if int(defn.get("check_type", 0)) == 1
                                   else "absolute",
                "low_limit": low,
                "high_limit": high,
                "enabled": bool(defn.get("enabled", True)),
                "severity": defn.get("severity", ""),
                "current_value": current,
                "status": status,
                "violation_count": live.get("violation_count", 0),
                "last_transition_ts": live.get("last_transition_ts"),
                "onboard": self._s12_onboard is not None
                           and mon_id in (self._s12_onboard or {}),
            })
        return rows

    def rule_rows(self) -> list[dict]:
        """One row per S19 event-action rule: definition + fire history."""
        rows = []
        for ea_id, rule in sorted(self._merged_s19().items()):
            fires = self._s19_fires.get(ea_id, {})
            kind = int(rule.get("trigger_kind", 0))
            trigger_id = int(rule.get("trigger_id", 0))
            rows.append({
                "ea_id": ea_id,
                "name": rule.get("name") or f"EA_{ea_id}",
                "trigger_kind": kind,
                "trigger_kind_name": "param" if kind == 1 else "event",
                "trigger_id": trigger_id,
                "trigger_label": self.trigger_label(kind, trigger_id),
                "action_func_id": rule.get("action_func_id"),
                "action_label": self.func_label(rule.get("action_func_id")),
                "enabled": bool(rule.get("enabled", True)),
                "fire_count": fires.get("count", 0),
                "last_fire_ts": fires.get("last_ts"),
                "fire_inferred": True,
                "onboard": self._s19_onboard is not None
                           and ea_id in (self._s19_onboard or {}),
            })
        return rows

    def journal(self, limit: int = 100) -> list[dict]:
        return list(self._journal)[:limit]

    def state_strip(self, fdir_level: str, load_shed_stage: int = 0,
                    now: Optional[float] = None) -> dict:
        """The always-visible FDIR state strip.

        ``now`` is SIM TIME (``MCSServer.sim_now_ts()``). The report ages
        below are differences against the ``ts`` values the ingest methods
        were given, which are also sim time, so a paused simulator freezes
        them instead of aging every FDIR report out within a minute.
        """
        now = _time.time() if now is None else now
        ool = [m for m, v in self._s12_live.items() if v.get("out_of_limits")]
        return {
            "fdir_level": fdir_level,
            "fdir_level_index": FDIR_LEVELS.index(fdir_level)
                                if fdir_level in FDIR_LEVELS else 0,
            "sc_mode": self._sc_mode,
            "sc_mode_name": SC_MODE_NAMES.get(self._sc_mode, "--"),
            "sc_mode_source": self._sc_mode_source,
            # The two latching FDIR flags are not downlinked as such; SAFE and
            # EMERGENCY sc_mode are the observable consequence of each latch
            # (engine._update_sc_mode), so they are reported as DERIVED.
            "safe_latched": self._sc_mode == 0,
            "emergency_latched": self._sc_mode == 3,
            "latch_source": "derived_from_sc_mode",
            "load_shed_stage": int(load_shed_stage or 0),
            "monitors_out_of_limits": len(ool),
            "monitors_out_of_limits_ids": sorted(ool),
            "monitoring_enabled": self._monitoring_enabled,
            "last_action_ts": self._last_action_ts,
            "last_action": self._last_action_text,
            "s12_report_age_s": (None if self._s12_report_ts is None
                                 else round(now - self._s12_report_ts, 1)),
            "s19_report_age_s": (None if self._s19_report_ts is None
                                 else round(now - self._s19_report_ts, 1)),
            "s12_reported": self._s12_onboard is not None,
            "s19_reported": self._s19_onboard is not None,
        }

    def display_data(self, *, active_alarms: list[dict],
                     param_values: dict[int, float] | None = None,
                     load_shed_stage: int = 0,
                     now: Optional[float] = None) -> dict[str, Any]:
        """Everything the FDIR window renders. ``now`` is sim time."""
        level = self.derive_level(active_alarms, load_shed_stage)
        monitors = self.monitor_rows(param_values)
        rules = self.rule_rows()
        return {
            "state": self.state_strip(level, load_shed_stage, now=now),
            "monitors": monitors,
            "monitor_summary": {
                "total": len(monitors),
                "enabled": sum(1 for m in monitors if m["enabled"]),
                "out_of_limits": sum(1 for m in monitors
                                     if m["status"] == "OUT_OF_LIMITS"),
            },
            "rules": rules,
            "rule_summary": {
                "total": len(rules),
                "enabled": sum(1 for r in rules if r["enabled"]),
                "fired": sum(1 for r in rules if r["fire_count"]),
            },
            "journal": self.journal(),
        }
