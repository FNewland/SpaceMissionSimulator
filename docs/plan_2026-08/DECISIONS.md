# Open judgement calls — decisions needed from Franz

Prepared 2026-07-31. These are the points where the audits found a genuine domain choice rather than a defect with a right answer. Franz's standing policy is *fix everything, quick wins first*, and *decide contested interfaces on researched engineering merit* — so for each one below there is a **recommendation Fable should act on by default**. Four of them (1, 2, 4, 7) change enough downstream work that they are worth two minutes of Franz's attention first.

---

## 1. ⚠ Does EO-Sat-1 carry propulsion at all?

**The tension.** Franz asked for orbit control. The propulsion audit designed it (1U green monoprop, 66.9 m/s ΔV, briefs 03). The *textbook* audit, working independently, recommended the opposite: **no propulsion**.

**The case against carrying it.** A 6U at 450 km decays inside the FCC 5-year rule — the courseware's own §3.4 says so. So deorbit does not justify propulsion, and for this mission class the honest engineering answer is to not carry it and to teach orbit maintenance as *the decision not to carry propulsion*, plus a paper ΔV exercise. The sim's manuals already say "no propulsion" in three places.

**The case for building it.** The courseware ships NOM-004, NOM-005, CTG-006, `PROP_ARM`, `PROP_ISO_CLOSE`, `htr_prop`, `HEATER_THRUSTER` and §16.8 against a propulsion system that does not exist — so today the book teaches something un-runnable either way. Drag make-up, collision avoidance and phasing are real justifications independent of deorbit. And the burn/attitude coupling is a genuinely good teaching moment that nothing else in the sim provides.

**Recommendation:** build it, per the explicit request — but the counter-argument is strong enough that if Franz would rather delete the propulsion residue and re-teach it as a trade decision, that is defensible engineering and saves a whole phase. **Fable should not start Phase D without a confirmation.**

---

## 2. ⚠ Two ground stations or five?

The book says two (Iqaluit + Troll, ~14 passes/day). The sim flies five with measured statistics (**51.5 passes/day, mean pass 447 s, median gap 33 min**). This changes every pass-planning, data-budget and eclipse-scheduling number in Ch 8 and Ch 13 — and it is *also* the root cause of the power deficit, because the previous power analysis assumed ~10 passes/day and nothing re-derived the budget when the station list tripled.

**Recommendation: five.** The sim's geometry is real and computed; the book's number is stale. Propagate to the courseware (brief 07 WP-10). But note this makes the ops day considerably busier than the book describes, so Ch 8's planning exercise needs rewriting, not just renumbering.

---

## 3. What is the FPA operating band?

Four live answers: book −15…−3 °C; MCS `limits.yaml` and the manual −18/+8 yellow, −20/+12 red; `tcs.yaml` events −50/−5 warn; planner −50…−5. Compounding it, **both achievable TCS states are outside the red limits** (cooler off → +9.6…+12.6 °C, cooler on → −22.6…−25.6 °C) — no configuration produces a green FPA on the TCS side. And the payload's FPA sits *exactly on* its S12 low limit, crossing it ~50 % of samples, so it chatters.

**Recommendation:** pick the band **after** B-6 fixes the setpoint bug, since the achievable range changes. Then single-source it, give the S12 limit ≥3σ clearance from the setpoint, and propagate to five repo files plus the book. Fable can do this without asking.

---

## 4. ⚠ What is the reaction-wheel inertia?

Book says 1.5e-5 kg·m² (nanosat MEMS class); sim says 0.005 kg·m² (200-mm class). **Neither is a 6U wheel.** The desaturation exercise is wrong by 333× until this is settled, and the whole momentum budget, the desat cadence and the NOM-007 entry criterion depend on it.

**Recommendation:** pick a real 6U reaction wheel from a current product line and derive everything from its datasheet, rather than reconciling the two wrong numbers. This is a prerequisite for AOCS C-2 and it is worth Franz confirming the class of spacecraft he wants students to believe they are flying.

---

## 5. Should pointing error be modelled realistically?

Today FINE_POINT converges to ~0.0005°, so the book's 0.18 / 0.32 / 0.42° world never occurs and four exercises have no referent.

**Recommendation: yes** — this is exactly what Franz asked for, and Phase C delivers it with a component-wise error budget (COARSE ~5°, NOMINAL 0.065°, FINE 0.013° total 3σ). Fable proceeds. The book's numbers then get rewritten from the model, not the other way round.

---

## 6. Should integration time and gain have radiometric consequences?

Without it, the COM-011 dark/flat thresholds and the COM-012 saturation decision point are un-runnable.

**Recommendation: yes, make them real** (SNR as a function of gain and integration time; `size_mb` from `lines × bands`). The alternative — rewriting those cards around the SNR and `last_scene_quality` the sim already computes — is cheaper, but making the settings real is what turns Franz's report (b) ("we do not know what values to put in") into something a student can reason about rather than look up.

---

## 7. ⚠ What is the swath / detector geometry?

Book says 240 km; sim says 45 km. **240 km swath with 4 MB frames and 10 m GSD is not self-consistent**, and the sim's 5000 px × 20 µrad is a small telescope for an ocean-colour mission. One of the two instruments is wrong and the data budget follows from it.

**Recommendation:** decide the instrument first — it is a mission-design question, not a bug — then fix whichever side disagrees. Worth Franz's call because it determines what the mission is *for*.

---

## 8. Which SoC / bus-voltage alarm ladder is canonical?

Book, `mcs/limits.yaml`, `s12_definitions.yaml` and `simulation.yaml` give four different answers for the same battery.

**Recommendation:** the ladder derived in B-4 (shed 45.5 % → EPS-safe 39 % → SoC stages 30/20/10 % → AOCS-safe 22 %), locked by a test that converts every threshold to SoC through the configured OCV curve and asserts strict monotonicity. Fable proceeds without asking.

---

## 9. Is `PAYLOAD_CAPTURE` addressed by scene or by coordinates?

Catalogue + book + MCS say `scene_id + lines`; the flight code unpacks `lat + lon` float32; the planner sends nothing at all, so every planned image is taken at 0°N 0°E. Both addressing schemes are defensible for a real EO mission; only one can be on the wire.

**Recommendation: scene_id + lines.** Three of four sources agree, scene-addressed tasking is what a real EO payload accepts, and geolocation is resolved on the ground from attitude plus ephemeris — which is also what makes the Phase C pointing work *matter* to the payload product. Change the handler. Fable proceeds without asking.

---

## Also open, carried from previous sessions

- **S19 action-func content** (roadmap N-10): 13 of 26 rules dispatch a func contradicting their description; 4 more have trigger IDs contradicting their names. Held open by strict-xfail gates. Still needs a per-rule ruling from Franz — Fable should **not** blanket-remap S19 (the 2026-06 session established that lesson).
- **SpaceHack folder** — the folder-access request was refused by the bridge. If Franz wants the RF-impairment porting (X-15) included, he needs to connect `~/SpaceHack` with the desktop app's **Add folder** picker; a session-side request cannot reach it.
