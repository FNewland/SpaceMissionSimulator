# Project status — what is still open

**Updated:** 2026-08-01, after the 2026-08 build (phases A–G).

This file replaces four overlapping documents that used to sit in the
repository root — `REMEDIATION_PLAN.md` (May), `VV_FINDINGS_2026-06.md`,
`FUNCTIONALITY_AUDIT_2026-06.md` and `SPACECDF_RECONCILE.md` — all of which
are now in `docs/archive/` with an index. They described overlapping and
partly contradictory sets of work, most of which has since landed.

**It carries only what is still open.** Everything closed is in `DEFECTS.md`,
which has been the project's real changelog, and in `docs/CHANGELOG.md`.

---

## Still open

| # | Item | Severity | Effort | Why it is still open |
|---|---|---|---|---|
| **S-1** | **13 S19 rules dispatch an S8 func that contradicts their description**, and 4 more have trigger ids that contradict their names | Critical (teaching) | S, after a decision | This needs a *per-rule* ruling from Flight Ops. Blanket-remapping S19 was tried in the 2026-06 session and was wrong. Held by strict-xfail gates in `tests/test_content/test_s19_rules_content.py`, registered in `tests/test_content/test_strict_xfail_register.py` with a `2026-10` deadline. |
| **S-2** | **`obc_reboot.md` and `software_upload.md` quote S8 funcs for OBDH operations that route to AOCS, EPS, payload and TCS handlers** | Major | S, after a decision | Either the procedures or the S8 map is wrong; which one is a Flight-Ops call. Same strict-xfail register, same deadline. |
| **S-3** | **~60 of 64 markdown procedures are not runnable** | Major | M/L | The framework exists; only NOM-002/003/008/009 carry `runnable:` links. Adding a link is a per-procedure Flight-Ops decision: a document is only automatable if its `**TC:**` annotations are the whole of its commanding, and most contingency procedures deliberately are not. |
| **S-4** | **No ground → S18/OBCP compiler; S18 is not in the command builder** | Major | M | The canonical `command`/`wait` step model maps 1:1 onto the OBCP step model, so this is small and well-defined — it simply has not been built. `to_obcp`/`compile_obcp` appear nowhere in `packages/`. |
| **S-5** | **Imaging targets and the ground track are not drawn on the planner map** | Major | S | `/api/imaging/targets` is implemented and the geometry has been correct since #61. The planner's `index.html` calls it **zero** times. Opportunity selection is a geometry argument with no geometry on screen. |
| **S-6** | **No unified PUS routing table** | Major | M | Truth is still re-declared in `engine._check_tc_acceptance`, the bootloader allowlist, `service_dispatch._func_id_owner`/`_func_id_subsys`, `mcs/pus_services.yaml` and the MCS catalogue. `pus_services.yaml` is the obvious promotion candidate — it is already the right shape and has no production consumers. |
| **S-7** | **One `ground_stations.yaml`** | Minor | S | `mission.yaml: ground_segment` is gone (2026-08), but `orbit.yaml` and `planning/` still each carry a station list. |
| **S-8** | **Orphaned PUS parser in `smo-common`, diverged from the MCS copy** | Minor | S | Two parsers for one wire format. Not currently causing a defect. |
| **S-9** | **20 parameters are declared stubs** | Minor | M | `stub: true` is an honest declaration — an S20 read returns 0.0 and the catalogue says so — but the honest fix is to compute them. Ratcheted at 20 by `tests/test_content/test_parameter_chain.py`. |
| **S-10** | **Textbook reconciliation** | Major | S–M | The errata list is in `docs/plan_2026-08/TEXTBOOK_CHANGES.md`. Writing it into the book is outside this repository. |
| **S-11** | **MCS JavaScript is 9 000+ lines inline in one HTML file** | Major | M–L | Every JS defect in the #63–#79 batch was found by a human reading the file; it is untestable except by browser. Extracting it to modules is the prerequisite for further display work. |
| **S-12** | **SpaceCDF reconciliation items** | Major | — | Out of this repository. |

## Explicitly not being done

Recorded so they stop being re-raised.

| Item | Decision |
|---|---|
| GNU Radio bridge (#18) | **Deprioritised.** The native DSP path covers the syllabus after #53–#56. |
| Radio dashboard placeholders (#19) | **Closed as superseded** by #53–#56 (spectrum, eye and channel displays are live). |
| Cleanup registers #20/#21/#22/#32/#33 | **Closed**, folded into the numbered items above and into `DEFECTS.md`. |
| A config-driven `DisplayEngine` | **Rejected.** `displays/engine.py` and `displays/widgets.py` were deleted in the 2026-08 build rather than revived; the trending capability they aspired to is the archive query API behind the TRENDS tab. |
| A 3D attitude visualiser | **Not worth the effort** against the teaching mission. |
| A second "wide" wall display | **Rejected.** The second planner UI was a divergence liability and is gone; `/wide` is now a CSS layout mode of the one page. |
| Propulsion at all | **Built, on Franz's explicit instruction.** The textbook audit's independent recommendation was the opposite — a 6U at 450 km decays inside the FCC 5-year rule, so the honest answer for this mission class is *no propulsion*, taught as the decision not to carry it. The counter-argument is real and is recorded in `docs/plan_2026-08/` for the courseware to address. |

## Closed in the 2026-08 build

Summarised; see `docs/CHANGELOG.md` for detail.

* Working tree committed, branched and tagged; repository root consolidated.
* Reproducibility: seeded per-component RNG, sub-stepped tick, √dt noise
  scaling, complete state round-trip with an enforcing registry.
* Power budget closed with an honest loss stack and a load-bearing solar array
  drive; protection ladder un-inverted.
* Attitude dynamics, sensor models, MEKF, control and mode gating built from
  nothing.
* Propulsion built; ΔV propagates to the ephemeris via a measured TLE re-fit.
* Command argument schema with engineering-unit encoding and four drift gates.
* Report generator and configuration editor.
* Ground limit checking live; FDIR runs end to end; TRENDS page; command
  history archived.
* Classroom launch hardened; dead code deleted; contract-test bundle complete;
  FPA thermal envelope single-sourced.
