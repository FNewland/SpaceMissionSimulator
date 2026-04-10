"""Tests for smo_common.orbit modules."""
import pytest
import numpy as np
from datetime import datetime, timezone
from smo_common.orbit.propagator import OrbitPropagator, GroundStation, OrbitState
from smo_common.orbit.eclipse import is_in_eclipse, eclipse_fraction


TLE1 = "1 99001U 26001A   26068.50000000  .00000100  00000-0  10000-4 0  9990"
TLE2 = "2 99001  97.4000 120.0000 0001200  90.0000 270.0000 15.15000000 00010"


def test_propagator_init():
    gs = GroundStation(name="Svalbard", lat_deg=78.229, lon_deg=15.407, alt_km=0.458)
    prop = OrbitPropagator(TLE1, TLE2, ground_stations=[gs])
    assert prop.state.alt_km > 0


def test_propagator_advance():
    prop = OrbitPropagator(TLE1, TLE2)
    utc_before = prop.state.utc
    state2 = prop.advance(60.0)
    assert state2.utc > utc_before


def test_eclipse_detection():
    # Sun behind Earth => eclipse
    sc = np.array([-7000.0, 0.0, 0.0])  # behind Earth
    sun = np.array([1.5e8, 0.0, 0.0])   # Sun in +X
    assert is_in_eclipse(sc, sun, 6371.0) == True

    # Sun-facing => no eclipse
    sc2 = np.array([7000.0, 0.0, 0.0])
    assert is_in_eclipse(sc2, sun, 6371.0) == False


def test_eclipse_fraction():
    sc = np.array([7000.0, 0.0, 0.0])
    sun = np.array([1.5e8, 0.0, 0.0])
    assert eclipse_fraction(sc, sun) == 0.0
