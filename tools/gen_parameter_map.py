#!/usr/bin/env python3
"""Generate `docs/PARAMETER_MAP.md` — parameter → SID → limits → S12.

Brief 08's documentation gap: "a parameter/SID/limit map generated from
config: which parameters are in which SID, which are on-demand, which are
limited, which are S12-monitored. Would have made findings 12 and 13
obvious."

It would have. 50 parameters were in no HK SID and not on-demand — unreachable
from the ground by any route — and the four new rate-ladder parameters carried
limit bands while appearing in no structure. Both are one glance at this table.

The file is generated, byte-stable and pinned in CI by
`tests/test_content/test_generated_command_reference.py`, so it cannot drift
from the configuration it describes.

Run::

    python tools/gen_parameter_map.py            # write docs/PARAMETER_MAP.md
    python tools/gen_parameter_map.py --check    # exit 1 if it would change
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REPO = Path(__file__).resolve().parents[1]
CFG = REPO / "configs" / "eosat1"
OUT = REPO / "docs" / "PARAMETER_MAP.md"


def _pid(v) -> int:
    return int(v) if isinstance(v, int) else int(str(v), 0)


def _load(rel: str) -> dict:
    return yaml.safe_load((CFG / rel).read_text()) or {}


def build() -> str:
    params = _load("telemetry/parameters.yaml")["parameters"]
    structures = _load("telemetry/hk_structures.yaml")["structures"]
    limits = _load("mcs/limits.yaml")["limits"]
    s12 = _load("monitoring/s12_definitions.yaml")
    s12_defs = (s12.get("s12_definitions") or s12.get("monitors")
                or s12.get("definitions") or [])
    derived_path = CFG / "telemetry" / "derived.yaml"
    derived = []
    if derived_path.exists():
        derived = (yaml.safe_load(derived_path.read_text()) or {}).get(
            "derived_parameters", []) or []

    sids_for: dict[int, list[int]] = {}
    sid_name: dict[int, str] = {}
    for s in structures:
        sid = int(s["sid"])
        sid_name[sid] = str(s.get("name", ""))
        for m in s.get("parameters") or []:
            sids_for.setdefault(_pid(m["param_id"]), []).append(sid)

    limit_for = {_pid(l["param_id"]): l for l in limits}
    s12_for: dict[int, list[dict]] = {}
    for d in s12_defs:
        raw = d.get("param_id", d.get("parameter_id"))
        if raw is None:
            continue
        s12_for.setdefault(_pid(raw), []).append(d)

    by_subsystem: dict[str, list[dict]] = {}
    for p in params:
        by_subsystem.setdefault(str(p.get("subsystem", "?")), []).append(p)

    L: list[str] = []
    A = L.append
    A("# Parameter map — parameter → SID → limits → S12")
    A("")
    A("**Generated** by `tools/gen_parameter_map.py` from "
      "`configs/eosat1/`. Do not edit; edit the configuration and re-run.")
    A("Byte-stability is pinned in CI, so this file cannot drift from the "
      "configuration it describes.")
    A("")
    A("Columns:")
    A("")
    A("| Column | Meaning |")
    A("|---|---|")
    A("| **HK SID** | Housekeeping structures that carry this parameter in "
      "routine telemetry. Blank means it is never downlinked routinely. |")
    A("| **S20** | `on_demand: true` — readable with `GET_PARAM` "
      "(S20.3 in this suite; see `docs/PUS_SERVICE_REFERENCE.md`). |")
    A("| **Ground limits** | Yellow/red bands from `mcs/limits.yaml`, checked "
      "by the MCS on every sample. |")
    A("| **S12** | On-board monitors from `monitoring/s12_definitions.yaml`. "
      "These fire autonomously and can drive S19 event-actions. |")
    A("| **Stub** | The catalogue declares the parameter but no model "
      "computes it, so an S20 read returns 0.0. Declared, not discovered. |")
    A("")
    A("**Reachability rule.** Every parameter must be in at least one HK SID, "
      "or be `on_demand`, or be listed in the `DERIVED_ONLY` allowlist in "
      "`tests/test_content/test_parameter_chain.py` with a reason. A "
      "parameter that is limited or S12-monitored but unreachable is a "
      "monitor that can never fire; that is a test failure, not a note.")
    A("")

    total = len(params)
    in_hk = len(sids_for)
    on_demand = sum(1 for p in params if p.get("on_demand"))
    stubs = sum(1 for p in params if p.get("stub"))
    A("## Summary")
    A("")
    A("| | Count |")
    A("|---|---:|")
    A(f"| Catalogued parameters | {total} |")
    A(f"| In at least one HK SID | {in_hk} |")
    A(f"| On-demand (S20-readable) | {on_demand} |")
    A(f"| Declared stubs (model does not compute) | {stubs} |")
    A(f"| Ground limit bands | {len(limit_for)} |")
    A(f"| S12 on-board monitors | {len(s12_defs)} |")
    A(f"| Ground-derived pseudo-parameters (`derived.yaml`) | {len(derived)} |")
    A("")

    A("## Housekeeping structures")
    A("")
    A("| SID | Name | Members |")
    A("|---:|---|---:|")
    for sid in sorted(sid_name):
        n = sum(1 for v in sids_for.values() if sid in v)
        A(f"| {sid} | {sid_name[sid]} | {n} |")
    A("")

    for subsystem in sorted(by_subsystem):
        A(f"## {subsystem}")
        A("")
        A("| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |")
        A("|---|---|---|---|:-:|---|---|:-:|")
        for p in sorted(by_subsystem[subsystem], key=lambda x: _pid(x["id"])):
            pid = _pid(p["id"])
            sids = ", ".join(str(s) for s in sorted(sids_for.get(pid, [])))
            od = "yes" if p.get("on_demand") else ""
            lim = limit_for.get(pid)
            if lim:
                lim_s = (f"{lim['yellow_low']:g}…{lim['yellow_high']:g} / "
                         f"{lim['red_low']:g}…{lim['red_high']:g}")
            else:
                lim_s = ""
            mons = s12_for.get(pid, [])
            mon_s = "<br>".join(
                f"{m['name']} ({m.get('low_limit', '')}…{m.get('high_limit', '')}"
                f", {m.get('severity', '')})" for m in mons)
            stub = "yes" if p.get("stub") else ""
            unit = str(p.get("units", "") or "")
            A(f"| `0x{pid:04X}` | {p.get('name','')} | {unit} | {sids} | {od} "
              f"| {lim_s} | {mon_s} | {stub} |")
        A("")

    if derived:
        A("## Ground-derived pseudo-parameters (`telemetry/derived.yaml`)")
        A("")
        A("Computed by the ground segment from the ephemeris and the station "
          "list. They are **not** spacecraft telemetry: no HK SID can carry "
          "them and no S20 read can return them. They sat in "
          "`parameters.yaml` — where they were permanently unreachable — "
          "until the 2026-08 build.")
        A("")
        A("| Name | Unit | Description |")
        A("|---|---|---|")
        for d in derived:
            A(f"| {d.get('name','')} | {d.get('units','') or ''} | "
              f"{d.get('description','')} |")
        A("")

    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the file on disk is stale")
    ap.add_argument("--output", default=str(OUT))
    args = ap.parse_args()

    text = build()
    out = Path(args.output)
    if args.check:
        current = out.read_text() if out.exists() else ""
        if current != text:
            print(f"{out} is stale — run tools/gen_parameter_map.py",
                  file=sys.stderr)
            return 1
        print(f"{out} is current")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"Parameter map generated: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
