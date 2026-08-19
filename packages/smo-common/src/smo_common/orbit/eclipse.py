"""SMO Common — the SINGLE eclipse/illumination authority.

THE SHADOW MODEL (stated, not implied)
--------------------------------------
Conical Earth shadow with a real penumbra, computed from apparent disc
geometry:

* the Earth is a sphere of ``earth_radius_km`` (6371 km mean radius — the
  figure ``orbit.yaml earth_radius_km`` carries and the propagator passes in);
* the Sun is a disc of radius :data:`SUN_RADIUS_KM` at the true geometric
  position handed in (no light-time or aberration correction — irrelevant at
  the ~1 s resolution this simulator runs at);
* the illuminated fraction of the solar disc as seen from the spacecraft is
  the circle-circle overlap of the apparent solar disc with the apparent Earth
  disc. It is exactly 0.0 in the umbra, exactly 1.0 in full sun, and the
  geometric overlap fraction in the penumbra.

Why conical-with-penumbra and not the old cylindrical shell: a cylinder has no
penumbra at all, so illumination steps 1 -> 0 between two samples and every
consumer agrees only if it happens to sample at the same instant — which is
exactly the coherence defect this module exists to remove. At EO-Sat-1's
~513 km the penumbra crossing lasts ~10 s of a ~2100 s eclipse and the umbra
cone is ~2.4 km narrower than the cylinder: both physically small, both
honest, and now computed in ONE place.

Definitions used across the whole suite (simulator, MCS, planner, tools):

``sun_fraction``  fraction of the solar disc visible, 0.0 ... 1.0. This is the
                  illumination every power / thermal / optical consumer must
                  multiply by; it is the authority.
``in_eclipse``    ``sun_fraction <= 0.0`` — full umbra, no direct sunlight.
                  This is the flag AOCS mode logic, the CSS model, the star
                  tracker blinding model and the ground displays use.
``in_penumbra``   ``0.0 < sun_fraction < 1.0``.

Nothing anywhere else in the tree may derive eclipse from a beta angle, an
orbit-phase accumulator, a hard-coded eclipse fraction, or a second shadow
model. Consumers read :class:`~smo_common.orbit.propagator.OrbitState`
(``in_eclipse`` / ``sun_fraction``), which the propagator fills from
:func:`shadow_state` below; a ground tool that must predict a FUTURE eclipse
propagates the orbit and calls this same function, so its present-time value
is identical to the simulator's.
"""
from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np

#: Solar photospheric radius (km), IAU 2015 nominal.
SUN_RADIUS_KM = 695700.0

#: Human-readable name of the shadow model, published alongside the flag so an
#: operator can tell which geometry produced it.
SHADOW_MODEL = "conical umbra + penumbra (spherical Earth, finite solar disc)"


class ShadowState(NamedTuple):
    """Illumination of the spacecraft by the Sun at one instant."""

    sun_fraction: float   # 0.0 = umbra, 1.0 = full sun
    in_eclipse: bool      # True only in the umbra (sun_fraction <= 0)
    in_penumbra: bool     # True in the partial-shadow annulus


def _occulted_disc_fraction(theta: float, a_sun: float, a_body: float) -> float:
    """Fraction of the solar disc OCCULTED by the occulting body.

    ``theta``  angular separation of the two disc centres (rad)
    ``a_sun``  apparent angular radius of the Sun (rad)
    ``a_body`` apparent angular radius of the occulting body (rad)
    """
    if theta >= a_sun + a_body:
        return 0.0                      # discs disjoint — full sun
    if theta <= a_body - a_sun:
        return 1.0                      # solar disc fully covered — umbra
    if theta <= a_sun - a_body:
        # Occulting body wholly inside the solar disc (annular). Cannot happen
        # from LEO behind the Earth, but the formula must stay sane.
        return (a_body * a_body) / (a_sun * a_sun)

    # Partial overlap — penumbra. Circle-circle lens area / solar disc area.
    d, r1, r2 = theta, a_sun, a_body
    cos1 = (d * d + r1 * r1 - r2 * r2) / (2.0 * d * r1)
    cos2 = (d * d + r2 * r2 - r1 * r1) / (2.0 * d * r2)
    phi1 = math.acos(max(-1.0, min(1.0, cos1)))
    phi2 = math.acos(max(-1.0, min(1.0, cos2)))
    area = (r1 * r1 * (phi1 - math.sin(2.0 * phi1) / 2.0)
            + r2 * r2 * (phi2 - math.sin(2.0 * phi2) / 2.0))
    return max(0.0, min(1.0, area / (math.pi * r1 * r1)))


def shadow_state(
    sc_pos_eci: np.ndarray,
    sun_pos_eci: np.ndarray,
    earth_radius_km: float = 6371.0,
) -> ShadowState:
    """THE eclipse computation. Every consumer in the suite ends up here.

    ``sc_pos_eci`` / ``sun_pos_eci`` are ECI position vectors in km, both
    measured from the Earth's centre.
    """
    r = np.asarray(sc_pos_eci, dtype=float)
    s = np.asarray(sun_pos_eci, dtype=float)
    r_mag = float(np.linalg.norm(r))
    if r_mag <= earth_radius_km or r_mag == 0.0:
        # Degenerate (uninitialised state / sub-surface): report full sun
        # rather than fabricate an eclipse.
        return ShadowState(1.0, False, False)

    to_sun = s - r
    d_sun = float(np.linalg.norm(to_sun))
    if d_sun == 0.0:
        return ShadowState(1.0, False, False)
    to_sun_hat = to_sun / d_sun
    to_earth_hat = -r / r_mag            # Earth's centre as seen from the s/c

    a_sun = math.asin(min(1.0, SUN_RADIUS_KM / d_sun))
    a_earth = math.asin(min(1.0, earth_radius_km / r_mag))
    cos_theta = float(np.clip(np.dot(to_sun_hat, to_earth_hat), -1.0, 1.0))
    theta = math.acos(cos_theta)

    frac = max(0.0, min(1.0, 1.0 - _occulted_disc_fraction(theta, a_sun, a_earth)))
    return ShadowState(frac, frac <= 0.0, 0.0 < frac < 1.0)


def sun_fraction(
    sc_pos_eci: np.ndarray,
    sun_pos_eci: np.ndarray,
    earth_radius_km: float = 6371.0,
) -> float:
    """Illuminated fraction of the solar disc (0.0 umbra ... 1.0 full sun)."""
    return shadow_state(sc_pos_eci, sun_pos_eci, earth_radius_km).sun_fraction


def is_in_eclipse(
    sc_pos_eci: np.ndarray,
    sun_pos_eci: np.ndarray,
    earth_radius_km: float = 6371.0,
) -> bool:
    """True in the UMBRA (no direct sunlight at all).

    The suite-wide boolean spelling of :func:`shadow_state` — a thin wrapper,
    so there is exactly one geometry behind both spellings.
    """
    return shadow_state(sc_pos_eci, sun_pos_eci, earth_radius_km).in_eclipse


def eclipse_fraction(
    sc_pos_eci: np.ndarray,
    sun_pos_eci: np.ndarray,
    earth_radius_km: float = 6371.0,
) -> float:
    """Occulted fraction of the solar disc — ``1 - sun_fraction``.

    This used to be a SECOND, independent shadow model (cylindrical shell with
    an invented 50 km "penumbra transition zone") sitting next to
    ``is_in_eclipse`` and free to disagree with it. It is now derived from the
    one model.
    """
    return 1.0 - sun_fraction(sc_pos_eci, sun_pos_eci, earth_radius_km)
