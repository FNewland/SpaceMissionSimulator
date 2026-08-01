"""The one place the FPA thermal envelope is defined (DECISIONS §3, G3-d).

Before this module, five files carried five different answers for the same
detector: `subsystems/tcs.yaml` event thresholds (-50/-5 warn, -60/0 alarm),
`mcs/limits.yaml` (-20/+15 yellow, -25/+20 red on the node; -17/+15 and
-18/+8 on the two payload copies), `monitoring/s12_definitions.yaml`
(-20/+15 and -16.5/+15), `subsystems/payload.yaml` (a -15 C setpoint with no
band at all) and the operations manual (-18/+8 yellow, -20/+12 red, plus a
"cool to <= -12 C" readiness rule that appears nowhere else). The textbook
adds a sixth (-15 to -3 C).

Two things had to happen before the disagreement was even *decidable*:

* Phase B fixed the setpoint bug — `tcs_basic` used the setpoint as an
  additive environment offset (`target - 20`), so the node asymptoted to
  ambient minus 35 K and never to the setpoint. Until that landed, no band
  could be derived from behaviour because the behaviour was wrong.
* Phase E made the setpoint commandable (`PAYLOAD_COOLER_SETPOINT`, S8
  func 36, uint16 centi-kelvin) over a -20…0 C envelope. That turned "the FPA
  sits at -15" into "the FPA sits wherever the operator put it", which
  invalidates every band that was pinned to -15.

So the band is **derived from the corrected model's measured behaviour**, and
this module is the derivation. `subsystems/tcs.yaml: fpa_envelope` holds the
inputs — TCS owns the FPA node (brief 05 WP-1; the payload *reads* it) — and
everything downstream is computed here:

    monitoring/s12_definitions.yaml   0x0408, 0x0601
    mcs/limits.yaml                   0x0408, 0x0601, 0x0617
    subsystems/tcs.yaml               event_thresholds.*.fpa
    planning/activity_types.yaml      fpa_temp_min_c / fpa_temp_max_c
    the TCS and payload models        readiness, over/under-temp events
    configs/eosat1/manual/*.md        the quoted bands

`tests/test_content/test_fpa_envelope_single_source.py` recomputes all of it
and fails if any file has drifted.

Measured behaviour this derivation is built on (10 000 s of steady state,
seeded, single FPA node, payload in the loop):

    cooler ON, setpoint -15 C   node -15.01 C, sigma 0.138 K, [-15.46, -14.59]
    cooler ON, setpoint -20 C   node -19.99 C, sigma 0.110 K
    cooler ON, setpoint   0 C   node  +0.01 C, sigma 0.110 K
    cooler OFF                  node +11.43 C, sigma 0.902 K, peak +12.41
    detector (0x0601)           node - 0.44 K, sigma 0.120 K
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["FPAEnvelope", "Band"]


@dataclass(frozen=True)
class Band:
    """A closed temperature band in degrees Celsius."""
    low: float
    high: float

    def shifted(self, delta: float) -> "Band":
        return Band(round(self.low + delta, 3), round(self.high + delta, 3))

    def as_tuple(self) -> tuple[float, float]:
        return (self.low, self.high)


@dataclass(frozen=True)
class FPAEnvelope:
    """Inputs from `subsystems/tcs.yaml: fpa_envelope`, and everything derived.

    Every derived band is a function of the commandable setpoint envelope, the
    measured control noise and the measured uncooled equilibrium — never of a
    remembered number. Widening the command envelope therefore widens the
    limits automatically, which is the failure the old configuration had: the
    Phase E setpoint envelope reached -20 C while the payload detector's S12
    low limit sat at -16.5 C, so a *legal* command produced a red alarm.
    """

    # ── Commandable regulation envelope (S8 func 36) ──
    setpoint_min_c: float = -20.0
    setpoint_max_c: float = 0.0
    setpoint_default_c: float = -15.0

    # ── Measured behaviour of the corrected model ──
    #: 3-sigma of the regulated node about its setpoint.
    control_3sigma_k: float = 0.5
    #: Warmest the node reaches with the cooler off. This is a *legitimate*
    #: state (payload off between passes), so it has to be inside the bands.
    uncooled_max_c: float = 12.5

    # ── Offsets down the payload thermal chain ──
    #: 0x0601 payload.fpa_temp — the cooler's cold finger, below the bracket.
    detector_offset_c: float = -0.5
    #: 0x0617 payload.detector_temp — the die, above the finger by its own
    #: dissipation. Net offset from the bracket node is therefore ~0.
    die_offset_c: float = 0.5

    # ── Imaging readiness (a science limit, not a health limit) ──
    #: Dark current makes the product unusable above this. The textbook, the
    #: FPA_OVERTEMP event and the planner precondition already agreed on -3.
    imaging_max_c: float = -3.0
    #: Regulation tolerance for "the loop has settled", relative to the ACTIVE
    #: setpoint — this is what makes readiness survive a commanded setpoint
    #: change instead of being pinned to -15.
    ready_band_below_k: float = 1.0
    ready_band_above_k: float = 5.0

    # ── Limit-band construction margins ──
    warning_margin_k: float = 1.5
    alarm_margin_k: float = 5.0

    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, tcs_cfg: Any) -> "FPAEnvelope":
        """Build from a `TCSConfig` (or any object/dict with `fpa_envelope`)."""
        raw = tcs_cfg.get("fpa_envelope") if isinstance(tcs_cfg, dict) \
            else getattr(tcs_cfg, "fpa_envelope", None)
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raw = {f: getattr(raw, f) for f in cls.__dataclass_fields__
                   if hasattr(raw, f)}
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        return cls(**known)

    # ── Derived bands ─────────────────────────────────────────────────
    def node_warning(self) -> Band:
        """`tcs.temp_fpa` (0x0408) yellow band.

        Low: the coldest commandable setpoint, plus its own 3-sigma, plus a
        margin — anything colder means the loop has lost control, not that the
        operator chose a cold setpoint.
        High: the uncooled equilibrium plus a margin — the cooler being off is
        normal, so it must not raise an alarm.
        """
        return Band(
            round(self.setpoint_min_c - self.control_3sigma_k
                  - self.warning_margin_k, 3),
            round(self.uncooled_max_c + self.warning_margin_k, 3),
        )

    def node_alarm(self) -> Band:
        return Band(
            round(self.setpoint_min_c - self.control_3sigma_k
                  - self.alarm_margin_k, 3),
            round(self.uncooled_max_c + self.alarm_margin_k, 3),
        )

    def detector_warning(self) -> Band:
        """`payload.fpa_temp` (0x0601) — the cold finger."""
        return self.node_warning().shifted(self.detector_offset_c)

    def detector_alarm(self) -> Band:
        return self.node_alarm().shifted(self.detector_offset_c)

    def die_warning(self) -> Band:
        """`payload.detector_temp` (0x0617) — the die."""
        return self.node_warning().shifted(
            self.detector_offset_c + self.die_offset_c)

    def die_alarm(self) -> Band:
        return self.node_alarm().shifted(
            self.detector_offset_c + self.die_offset_c)

    # ── Readiness ─────────────────────────────────────────────────────
    def ready_band(self, setpoint_c: float | None = None) -> Band:
        """Regulation window about the ACTIVE setpoint."""
        sp = self.setpoint_default_c if setpoint_c is None else setpoint_c
        return Band(round(sp - self.ready_band_below_k, 3),
                    round(sp + self.ready_band_above_k, 3))

    def is_ready(self, temp_c: float, setpoint_c: float | None = None) -> bool:
        """Thermally ready to image.

        Two conditions, and both are needed. Regulation alone is not enough —
        the setpoint envelope reaches 0 C, and a perfectly regulated 0 C
        detector is far too warm to image with. An absolute bound alone is not
        enough either: it would call the FPA ready while it was still on its
        way down through -4 C, which is what the planner's old
        `fpa_temp_max_c: -5` did.
        """
        band = self.ready_band(setpoint_c)
        return band.low <= temp_c <= band.high and temp_c <= self.imaging_max_c

    def imaging_band(self) -> Band:
        """What the planner may schedule imaging in (`activity_types.yaml`)."""
        return Band(self.setpoint_min_c, self.imaging_max_c)

    # ── Event thresholds ──────────────────────────────────────────────
    def overtemp_c(self) -> float:
        """`FPA_OVERTEMP` (0x0604): warmer than the science can tolerate."""
        return self.imaging_max_c

    def undertemp_c(self) -> float:
        """`FPA_UNDERTEMP` (0x0605): colder than the loop can command.

        The old -18 C fired on any setpoint at or below -17, which Phase E
        made an ordinary thing to command.
        """
        return round(self.setpoint_min_c - self.control_3sigma_k, 3)
