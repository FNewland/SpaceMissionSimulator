#!/usr/bin/env python3
"""EO-Sat-1 power-budget closure tool (Phase B, brief 01 Verification).

The durable, in-tree replacement for the one-off analyses that let the budget
drift every time the config changed. Everything is DERIVED, nothing assumed:

  * contact statistics (passes/day, mean duration, merged TX duty including
    the simulation.yaml auto-TX hold) come from the repo's own propagator and
    the canonical five-station list in orbit.yaml — the exact step whose
    absence caused the 2026-07 budget defect;
  * the solar-beta range and eclipse fraction are swept over a full year and
    the WORST case is what the closure claim is made against;
  * generation and loads are computed by ticking the production
    EPSBasicModel + TCSBasicModel (loss stack, SADA tracking, thermostat
    heater duty, tier-dependent TX power, coulombic efficiency), configured
    from configs/<mission> — no duplicated constants;
  * the sizing case is EOL (array_losses.design_life_years of aging).

Exit status: non-zero if the worst-beta nominal-ops margin ratio is below
--min-margin-ratio (default 1.10, i.e. +10 %) or if any load-shed event
occurs in the nominal profile — CI, not a human, owns the closure claim.

Usage:
    python tools/power_budget.py [--config configs/eosat1]
        [--profile quiescent|nominal|nominal_imaging|worst|all]
        [--orbits 12] [--report md|json] [--bol] [--min-margin-ratio 1.10]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in ("smo-common", "smo-simulator"):
    p = REPO_ROOT / "packages" / pkg / "src"
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import yaml  # noqa: E402

from smo_common.orbit.propagator import OrbitPropagator, GroundStation  # noqa: E402
from smo_simulator.models.eps_basic import EPSBasicModel  # noqa: E402
from smo_simulator.models.tcs_basic import TCSBasicModel  # noqa: E402
from smo_simulator.models.aocs_basic import AOCSBasicModel  # noqa: E402


# ─────────────────────────── config loading ───────────────────────────

def load_configs(config_dir: Path) -> dict:
    cfg = {}
    for name in ("orbit", "simulation"):
        cfg[name] = yaml.safe_load((config_dir / f"{name}.yaml").read_text())
    for name in ("eps", "tcs", "ttc"):
        cfg[name] = yaml.safe_load(
            (config_dir / "subsystems" / f"{name}.yaml").read_text())
    return cfg


def build_propagator(orbit_cfg: dict) -> tuple[OrbitPropagator, datetime]:
    stations = [GroundStation(
        name=g["name"], lat_deg=g["lat_deg"], lon_deg=g["lon_deg"],
        alt_km=g.get("alt_km", 0.0),
        min_elevation_deg=g.get("min_elevation_deg", 5.0))
        for g in orbit_cfg.get("ground_stations", [])]
    prop = OrbitPropagator(orbit_cfg["tle_line1"], orbit_cfg["tle_line2"],
                           ground_stations=stations)
    t0 = orbit_cfg.get("t0_epoch")
    epoch = (datetime.fromisoformat(str(t0).replace("Z", "+00:00"))
             if t0 else datetime.now(timezone.utc))
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=timezone.utc)
    prop.reset(epoch)
    return prop, epoch


def orbit_period_s(orbit_cfg: dict) -> float:
    # revs/day from TLE line 2 cols 53-63
    mm = float(orbit_cfg["tle_line2"][52:63])
    return 86400.0 / mm


# ─────────────────────────── derived geometry ───────────────────────────

def beta_sweep(orbit_cfg: dict, epoch: datetime, days: int = 365,
               sample_days: int = 5) -> list[dict]:
    """Sample |beta| and eclipse fraction across a year."""
    prop = OrbitPropagator(orbit_cfg["tle_line1"], orbit_cfg["tle_line2"])
    period = orbit_period_s(orbit_cfg)
    out = []
    for d in range(0, days, sample_days):
        t = epoch + timedelta(days=d)
        prop.reset(t)
        beta = prop.state.solar_beta_deg
        # eclipse fraction over one orbit at 30 s steps
        n_ecl = n = 0
        prop.reset(t)
        step = 30.0
        for _ in range(int(period / step)):
            st = prop.advance(step)
            n += 1
            n_ecl += 1 if st.in_eclipse else 0
        out.append({"day": d, "beta_deg": beta,
                    "eclipse_fraction": n_ecl / max(n, 1)})
    return out


def contact_stats(orbit_cfg: dict, sim_cfg: dict, epoch: datetime,
                  hours: float = 48.0) -> dict:
    prop, _ = build_propagator(orbit_cfg)
    prop.reset(epoch)
    windows = prop.contact_windows(duration_s=hours * 3600.0, step_s=10.0)
    period = orbit_period_s(orbit_cfg)
    hold_s = float((sim_cfg.get("ttc") or {}).get("auto_tx_hold_s", 300.0))
    total = hours * 3600.0
    # merged TX-on intervals: contact + hold after LOS
    ivs = []
    t_ref = epoch
    for w in windows:
        a = (datetime.fromisoformat(w["aos"]) - t_ref).total_seconds()
        b = (datetime.fromisoformat(w["los"]) - t_ref).total_seconds() + hold_s
        ivs.append((a, min(b, total)))
    ivs.sort()
    merged = []
    for a, b in ivs:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    tx_time = sum(b - a for a, b in merged)
    contact_time = sum(w["duration_s"] for w in windows)
    hi_el = [w for w in windows if w["max_elevation_deg"] >= 10.0]
    return {
        "passes_per_day": len(windows) / (hours / 24.0),
        "mean_pass_s": contact_time / max(len(windows), 1),
        "contact_duty": contact_time / total,
        "tx_duty_with_hold": tx_time / total,
        "passes_ge_10deg_per_day": len(hi_el) / (hours / 24.0),
        "passes_per_orbit": len(windows) / (total / period),
        "auto_tx_hold_s": hold_s,
        "windows": windows,
        "tx_intervals": merged,
    }


# ─────────────────────────── frame ───────────────────────────

def sun_body_lvlh(beta_deg: float, phase_deg: float) -> tuple[float, float, float]:
    """Sun unit vector in the LVLH-aligned body frame (perfect nadir hold).

    This is what the AOCS publishes at 0x02B3-B5 from the true attitude
    quaternion while holding NOMINAL, and what EPS reads (eps_basic.py:405).

    P-d (2026-08b): the tool used to build the vector in the PRE-Phase-C
    synthetic frame — (cos b cos phi, cos b sin phi, sin b), "body Z on the
    orbit normal" — while eps.yaml had already moved `sada_axis` to 'y' to
    match real LVLH (smo_common/attitude.lvlh_dcm: z = nadir, y = -orbit
    normal, x ~ velocity). Feeding a Z-normal vector into a Y-axis SADA turned
    the wing illumination law into sqrt(1 - cos^2(b) sin^2(phi)) (orbit-mean
    ~0.84) instead of the correct cos(b) (0.645 at |beta| = 49.8 deg): the
    tool over-read generation by ~28 % AND INVERTED the beta trend, labelling
    the best-generation epoch the worst.

    With u = r_hat, h_hat = orbit normal, w = h_hat x u (~velocity) and
    phi = atan2(s.w, s.u) (aocs_basic._orbit_phase_from_geometry):

        x_lvlh = s . w    =  cos(b) sin(phi)
        y_lvlh = s . (-h) = -sin(b)
        z_lvlh = s . (-u) = -cos(b) cos(phi)

    Verified component-wise against ``attitude.lvlh_dcm @ sun_hat`` to 1e-14.
    """
    b = math.radians(beta_deg)
    ph = math.radians(phase_deg)
    return (math.cos(b) * math.sin(ph), -math.sin(b),
            -math.cos(b) * math.cos(ph))


# ─────────────────────────── profiles ───────────────────────────

PROFILES = ("quiescent", "nominal", "nominal_imaging", "manoeuvre", "worst")

#: Fraction of the time OUTSIDE contact windows that the TX line is held on by
#: the auto-TX hold re-arming on operator telecommands (2026-08b P-e; see
#: `tx_state`). `quiescent` = nobody at the console; the ops profiles assume a
#: live instructed session. Documented in docs/plan_2026-08b/POWER_SIZING.md.
COMMANDING_DUTY = {
    "quiescent": 0.00,
    "nominal": 0.25,
    "nominal_imaging": 0.25,
    "manoeuvre": 0.35,
    "worst": 0.60,
}

# AOCS suite draw by mode (aocs_basic._MODE_POWER_W, Phase C brief 02 table).
# EPS bills the AOCS line from the mode-dependent draw the AOCS model
# publishes at 0x02C8, not from the 12 W line rating. Before G3-g this tool
# set no 0x02C8 at all, so every profile was billed the 12 W fallback: the
# nominal case was over-billed by 3 W and the imaging case under-billed by 3 W.
AOCS_MODE_POWER_W = {"coarse": 4.0, "nominal": 9.0, "fine": 15.0,
                     "slew": 12.0, "desat": 6.0, "eclipse": 7.0}

# Propulsion draw (prop_basic): valve/electronics while ARMED or FIRING, plus
# the 12 W catalyst-bed heater while the TCS thruster circuit is energised.
# Quiescent propulsion draws 0 W — the line being on is not a load.
PROP_VALVE_W = 2.0
PROP_CATBED_W = 12.0
#: One drag-make-up manoeuvre per orbit in the `manoeuvre` profile: 20 min of
#: catbed preheat (NOM-013) then a 32 s burn.
PROP_PREHEAT_S = 1200.0
PROP_BURN_S = 32.0


def _make_models(cfg: dict, eol: bool) -> tuple[EPSBasicModel, TCSBasicModel]:
    eps = EPSBasicModel()
    eps_cfg = dict(cfg["eps"])
    eps_cfg["simulation"] = cfg["simulation"].get("eps", {})
    eps.configure(eps_cfg)
    eps.rng.seed(20260731)
    tcs = TCSBasicModel()
    tcs_cfg = dict(cfg["tcs"])
    tcs_cfg["simulation"] = cfg["simulation"].get("tcs", {})
    tcs.configure(tcs_cfg)
    tcs.rng.seed(20260732)
    if eol:
        # Wind the array's sunlit-hour odometer forward to the design life, and
        # let the MODEL apply its own aging law from there — the tool no longer
        # carries a second copy of the rate. (2026-08b: it used to hard-code
        # 3.14e-6/h and a compounded target, neither of which matched the
        # model's linear law or eps.yaml's annual_degradation.)
        al = cfg["eps"].get("array_losses") or {}
        life_y = float(al.get("design_life_years", 3.0))
        eps._state.sa_lifetime_hours = (
            life_y * 8760.0 * EPSBasicModel.SUNLIT_FRACTION)
        eps._state.sa_age_factor = max(
            0.5, 1.0 - eps._state.sa_lifetime_hours
            * eps._sa_degradation_per_hour)
    return eps, tcs


def run_profile(cfg: dict, profile: str, epoch: datetime, orbits: int,
                eol: bool, cstats: dict) -> dict:
    """Tick EPS + TCS through `orbits` orbits of the given ops profile."""
    prop, _ = build_propagator(cfg["orbit"])
    prop.reset(epoch)
    period = orbit_period_s(cfg["orbit"])
    dt = 10.0
    eps, tcs = _make_models(cfg, eol)
    s = eps._state
    s.wing_py_deployed = True
    s.wing_my_deployed = True
    s.bat_soc_pct = 90.0
    s._prev_soc = 90.0

    # profile switches
    payload_standby = profile in ("nominal", "nominal_imaging", "manoeuvre",
                                  "worst")
    imaging = profile in ("nominal_imaging", "worst")
    imaging_through_eclipse = profile == "worst"
    manoeuvring = profile == "manoeuvre"
    hold_s = 900.0 if profile == "worst" else cstats["auto_tx_hold_s"]

    for line in ("aocs_wheels", "htr_bat", "htr_obc"):
        s.power_lines[line] = True
    s.power_lines["payload"] = payload_standby or imaging
    s.power_lines["fpa_cooler"] = imaging
    s.power_lines["prop"] = manoeuvring

    # TX timeline for this run: recompute merged windows with this hold
    prop2, _ = build_propagator(cfg["orbit"])
    prop2.reset(epoch)
    total_s = orbits * period
    windows = prop2.contact_windows(duration_s=total_s + 3600, step_s=10.0)
    tiers = {t["name"]: t for t in cfg["ttc"].get("tm_rate_tiers", [])}
    low_bps = tiers.get("LOW", {}).get("bps", 16000)
    hi_bps = tiers.get("HIGH", {}).get("bps", 128000)
    tx_iv = []
    for w in windows:
        a = (datetime.fromisoformat(w["aos"]) - epoch).total_seconds()
        b = (datetime.fromisoformat(w["los"]) - epoch).total_seconds() + hold_s
        # Dump (HIGH tier) criterion: worst profile dumps on every eligible
        # pass; nominal dumps on passes with max elevation >= 30 deg (a
        # realistic ops rule — roughly 1 in 4 passes).
        hi = (w["max_elevation_deg"] >= (10.0 if profile == "worst" else 30.0))
        tx_iv.append((a, b, hi))

    # ── Commanding duty (2026-08b P-e) ──────────────────────────────────
    # engine.py:1458 forces ttc_tx ON for `auto_tx_hold_s` (300 s) after EVERY
    # accepted telecommand, re-armed per command. The pass-derived duty above
    # therefore describes an UNATTENDED spacecraft. In a live classroom the
    # instructor and the students command continuously, so each accepted TC
    # re-arms the hold and the TX line is effectively always on inside a
    # session — the real duty far exceeds the 33 % this tool derives from
    # passes alone.
    #
    # Modelled honestly as an extra TX-on fraction outside contact windows,
    # rather than left out. The `nominal`/`worst` profiles assume an
    # instructed session; `quiescent` assumes nobody is at the console.
    cmd_duty = COMMANDING_DUTY.get(profile, 0.0)
    cmd_period = 600.0     # coarse duty cycle, well below one orbit

    def tx_state(t: float) -> tuple[bool, float]:
        for a, b, hi in tx_iv:
            if a <= t <= b:
                return True, (hi_bps if hi else low_bps)
        if cmd_duty > 0.0 and (t % cmd_period) < cmd_duty * cmd_period:
            return True, low_bps       # commanding keeps the LOW tier up
        return False, low_bps

    params: dict[int, float] = {}
    steps = int(total_s / dt)
    soc_min = 100.0
    orbit_min: dict[int, float] = {}
    shed_events = 0
    prev_stage = 0
    gen_int = 0.0
    line_energy: dict[str, float] = {}
    htr_energy_billed = 0.0
    htr_energy_tcs = 0.0
    cons_int = 0.0

    for i in range(steps):
        t = i * dt
        st = prop.advance(dt)
        # Sun vector in the TRUE LVLH body frame (see sun_body_lvlh). Published
        # at 0x02B3-B5 — the channel EPS actually prefers — and mirrored onto
        # the CSS composite 0x0245-47 for the fallback path.
        phase = AOCSBasicModel._orbit_phase_from_geometry(st)
        if st.in_eclipse or phase is None:
            params[0x02B3] = params[0x02B4] = params[0x02B5] = 0.0
            params[0x0245] = params[0x0246] = params[0x0247] = 0.0
        else:
            sun_bx, sun_by, sun_bz = sun_body_lvlh(st.solar_beta_deg, phase)
            params[0x02B3] = params[0x0245] = sun_bx
            params[0x02B4] = params[0x0246] = sun_by
            params[0x02B5] = params[0x0247] = sun_bz

        # TX line + tier
        on, bps = tx_state(t)
        s.power_lines["ttc_tx"] = on
        params[0x0506] = bps

        # payload profile
        t_orb = t % period
        in_capture = False
        # Manoeuvre timeline within the orbit: preheat, then burn.
        burn_start = PROP_PREHEAT_S
        preheat_now = manoeuvring and t_orb < PROP_PREHEAT_S
        burn_now = manoeuvring and burn_start <= t_orb < burn_start + PROP_BURN_S
        if imaging:
            in_capture = any(abs(t_orb - c) < 60.0 for c in (900.0, 1500.0, 2100.0))
            if imaging_through_eclipse:
                mode = 2
                tcs._state.cooler_fpa = True
            else:
                mode = 2 if (in_capture and not st.in_eclipse) else 1
                tcs._state.cooler_fpa = 300.0 < t_orb < 1600.0
        elif payload_standby:
            mode = 1
            tcs._state.cooler_fpa = False
        else:
            mode = 0
            tcs._state.cooler_fpa = False
        params[0x0600] = mode

        # AOCS suite: bill the mode, not the line rating (Phase C). Imaging
        # needs FINE_POINT; the payload refuses to capture without it, so the
        # 15 W is not optional. Everything else holds NOMINAL nadir at 9 W,
        # and the `worst` profile holds FINE all orbit (the abuse case).
        if imaging_through_eclipse:
            aocs_mode = "fine"
        elif imaging and in_capture:
            aocs_mode = "fine"
        elif manoeuvring and burn_now:
            aocs_mode = "slew"
        else:
            aocs_mode = "nominal"
        params[0x02C8] = AOCS_MODE_POWER_W[aocs_mode]

        # Propulsion: 20 min of catbed preheat then a 32 s burn, once per
        # orbit. Quiescent propulsion draws nothing.
        if manoeuvring:
            params[0x0B1B] = ((PROP_CATBED_W if preheat_now else 0.0)
                              + (PROP_VALVE_W if (preheat_now or burn_now)
                                 else 0.0))
        else:
            params[0x0B1B] = 0.0

        s.power_lines["fpa_cooler"] = bool(tcs._state.cooler_fpa) \
            or imaging_through_eclipse

        eps.tick(dt, st, params)
        tcs.tick(dt, st, params)

        gen_int += s.power_gen_w * dt
        cons_int += s.power_cons_w * dt
        bus = max(s.bus_voltage, 20.0)
        for ln, cur in s.line_currents.items():
            line_energy[ln] = line_energy.get(ln, 0.0) + cur * bus * dt
        htr_energy_billed += (s.line_currents.get("htr_bat", 0.0)
                              + s.line_currents.get("htr_obc", 0.0)) * bus * dt
        htr_energy_tcs += params.get(0x0411, 0.0) * dt

        soc_min = min(soc_min, s.bat_soc_pct)
        k = int(t // period)
        orbit_min[k] = min(orbit_min.get(k, 100.0), s.bat_soc_pct)
        if s.load_shed_stage > prev_stage:
            shed_events += 1
        prev_stage = s.load_shed_stage

    gen_avg = gen_int / total_s
    load_avg = cons_int / total_s
    mins = [orbit_min[k] for k in sorted(orbit_min)]
    return {
        "profile": profile,
        "gen_avg_w": gen_avg,
        "load_avg_w": load_avg,
        "margin_w": gen_avg - load_avg,
        "margin_ratio": gen_avg / load_avg if load_avg > 0 else float("inf"),
        "margin_wh_per_orbit": (gen_avg - load_avg) * period / 3600.0,
        "soc_min": soc_min,
        "orbit_mins": mins,
        "shed_events": shed_events,
        "line_avg_w": {ln: e / total_s for ln, e in sorted(line_energy.items())},
        "htr_duty_battery_pct": params.get(0x040E, 0.0),
        "heater_billed_wh": htr_energy_billed / 3600.0,
        "heater_tcs_wh": htr_energy_tcs / 3600.0,
    }


# ─────────────────────────── report ───────────────────────────

def fmt_md(cstats, sweep, results, eol, worst, best) -> str:
    L = []
    L.append("# EO-Sat-1 power budget (derived, not assumed)")
    L.append("")
    L.append(f"Sizing case: {'EOL (full loss stack + aging)' if eol else 'BOL'}")
    L.append("")
    L.append("## Contact statistics (propagator, canonical station list)")
    L.append(f"- passes/day: {cstats['passes_per_day']:.1f} "
             f"(≥10° max-el: {cstats['passes_ge_10deg_per_day']:.1f})")
    L.append(f"- mean pass: {cstats['mean_pass_s']:.0f} s; "
             f"passes/orbit: {cstats['passes_per_orbit']:.2f}")
    L.append(f"- contact duty: {cstats['contact_duty'] * 100:.1f} %; "
             f"TX duty incl. {cstats['auto_tx_hold_s']:.0f} s hold: "
             f"{cstats['tx_duty_with_hold'] * 100:.1f} %")
    L.append("")
    L.append("## Beta sweep (1 year)")
    betas = [x["beta_deg"] for x in sweep]
    ecls = [x["eclipse_fraction"] for x in sweep]
    L.append(f"- |beta| range: {min(abs(b) for b in betas):.1f}–"
             f"{max(abs(b) for b in betas):.1f} deg")
    L.append(f"- eclipse fraction range: {min(ecls):.3f}–{max(ecls):.3f}")
    L.append(f"- worst-generation epoch: day {worst['day']} "
             f"(beta {worst['beta_deg']:.1f}°, ecl {worst['eclipse_fraction']:.3f})")
    L.append(f"- max-eclipse epoch: day {best['day']} "
             f"(beta {best['beta_deg']:.1f}°, ecl {best['eclipse_fraction']:.3f})")
    L.append("")
    for tag, runs in results.items():
        L.append(f"## Profiles at {tag}")
        L.append("")
        L.append("| profile | gen W | load W | margin W | ratio | Wh/orbit "
                 "| min SoC % | sheds |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r in runs:
            L.append(f"| {r['profile']} | {r['gen_avg_w']:.1f} | "
                     f"{r['load_avg_w']:.1f} | {r['margin_w']:+.1f} | "
                     f"{r['margin_ratio']:.2f} | "
                     f"{r['margin_wh_per_orbit']:+.1f} | "
                     f"{r['soc_min']:.1f} | {r['shed_events']} |")
        L.append("")
        for r in runs:
            L.append(f"### {r['profile']} — line-by-line (orbit-average W)")
            for ln, w in r["line_avg_w"].items():
                if w > 0.005:
                    L.append(f"- {ln}: {w:.2f}")
            L.append(f"- per-orbit min SoC trace: "
                     + ", ".join(f"{m:.1f}" for m in r["orbit_mins"]))
            L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(REPO_ROOT / "configs" / "eosat1"))
    ap.add_argument("--profile", default="all",
                    choices=list(PROFILES) + ["all"])
    ap.add_argument("--orbits", type=int, default=12)
    ap.add_argument("--report", default="md", choices=["md", "json"])
    ap.add_argument("--bol", action="store_true",
                    help="size at BOL instead of the EOL loss stack")
    ap.add_argument("--min-margin-ratio", type=float, default=1.10)
    ap.add_argument("--sweep-days", type=int, default=365)
    args = ap.parse_args()

    config_dir = Path(args.config)
    cfg = load_configs(config_dir)
    prop, epoch = build_propagator(cfg["orbit"])
    eol = not args.bol

    sweep = beta_sweep(cfg["orbit"], epoch, days=args.sweep_days)
    # worst generation = max |beta| (SADA cos-beta law); max eclipse = min |beta|
    worst = max(sweep, key=lambda x: abs(x["beta_deg"]))
    deepest = max(sweep, key=lambda x: x["eclipse_fraction"])
    cstats = contact_stats(cfg["orbit"], cfg["simulation"], epoch)

    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    results = {}
    for tag, pick in (
            (f"worst-beta (day {worst['day']}, β={worst['beta_deg']:.1f}°)", worst),
            (f"max-eclipse (day {deepest['day']}, β={deepest['beta_deg']:.1f}°)",
             deepest)):
        ep = epoch + timedelta(days=pick["day"])
        results[tag] = [run_profile(cfg, p, ep, args.orbits, eol, cstats)
                        for p in profiles]

    if args.report == "json":
        out = {"contact_stats": {k: v for k, v in cstats.items()
                                 if k not in ("windows", "tx_intervals")},
               "beta_sweep": sweep, "results": results}
        print(json.dumps(out, indent=2, default=str))
    else:
        print(fmt_md(cstats, sweep, results, eol, worst, deepest))

    # ── closure gate ──
    rc = 0
    worst_tag = next(iter(results))
    for tag, runs in results.items():
        for r in runs:
            if r["profile"] == "nominal":
                if r["shed_events"] > 0:
                    print(f"FAIL: load shed tripped in nominal profile ({tag})",
                          file=sys.stderr)
                    rc = 1
                if r["margin_ratio"] < args.min_margin_ratio:
                    print(f"FAIL: nominal margin ratio {r['margin_ratio']:.2f} "
                          f"< {args.min_margin_ratio:.2f} ({tag})",
                          file=sys.stderr)
                    rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
