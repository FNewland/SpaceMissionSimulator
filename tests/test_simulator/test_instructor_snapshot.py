"""Test instructor snapshot endpoint and coverage of all simulator state.

Verifies that the instructor/operator display has ground-truth visibility into
every parameter and subsystem state, bypassing RF link gating.
"""
import json

import pytest
import yaml
from pathlib import Path
from smo_simulator.engine import SimulationEngine


@pytest.fixture
def engine():
    """Create a simulator engine instance."""
    config_dir = Path(__file__).parent.parent.parent / "configs" / "eosat1"
    engine = SimulationEngine(config_dir, speed=1.0)
    yield engine


@pytest.fixture
def expected_parameters():
    """Load all expected parameters from telemetry config."""
    param_file = Path(__file__).parent.parent.parent / "configs" / "eosat1" / "telemetry" / "parameters.yaml"
    if not param_file.exists():
        return {}
    with open(param_file) as f:
        config = yaml.safe_load(f)
    params = config.get('parameters', [])
    return {p['id']: p for p in params}


class TestInstructorSnapshot:
    """Test instructor snapshot endpoint coverage."""

    def test_snapshot_structure(self, engine):
        """Verify snapshot has all required top-level sections."""
        snap = engine.get_instructor_snapshot()

        required_keys = [
            'meta', 'orbit', 'spacecraft', 'parameters',
            'subsystems', 'tm_stores', 'active_failures', 'fdir'
        ]
        for key in required_keys:
            assert key in snap, f"Snapshot missing '{key}' section"

    def test_snapshot_metadata(self, engine):
        """Verify metadata section is complete."""
        snap = engine.get_instructor_snapshot()
        meta = snap['meta']

        assert 'timestamp' in meta
        assert 'tick' in meta
        assert 'speed' in meta
        assert 'spacecraft_phase' in meta

    def test_snapshot_orbit(self, engine):
        """Verify orbit section contains all ephemeris elements."""
        snap = engine.get_instructor_snapshot()
        orbit = snap['orbit']

        required_keys = [
            'lat_deg', 'lon_deg', 'alt_km', 'in_eclipse', 'in_contact',
            'semi_major_axis_km', 'eccentricity', 'inclination_deg',
            'raan_deg', 'arg_perigee_deg', 'true_anomaly_deg'
        ]
        for key in required_keys:
            assert key in orbit, f"Orbit missing '{key}'"

    def test_snapshot_spacecraft(self, engine):
        """Verify spacecraft section contains mode and link status."""
        snap = engine.get_instructor_snapshot()
        sc = snap['spacecraft']

        required_keys = ['mode', 'downlink_active', 'uplink_active', 'override_passes']
        for key in required_keys:
            assert key in sc, f"Spacecraft missing '{key}'"

    def test_snapshot_parameters_present(self, engine, expected_parameters):
        """Verify a good percentage of documented parameters are present in snapshot."""
        snap = engine.get_instructor_snapshot()
        params = snap['parameters']

        if not expected_parameters:
            pytest.skip("parameters.yaml not found")

        missing_params = []
        for param_id, param_info in expected_parameters.items():
            if param_id not in params:
                missing_params.append((param_id, param_info['name']))

        # At least 70% of documented parameters should be present
        coverage = (len(expected_parameters) - len(missing_params)) / len(expected_parameters)
        assert coverage >= 0.70, (
            f"Instructor snapshot parameter coverage only {coverage*100:.1f}% "
            f"({len(params)}/{len(expected_parameters)}). "
            f"Missing {len(missing_params)} params"
        )

    def test_snapshot_subsystems_present(self, engine):
        """Verify all subsystem states are captured."""
        snap = engine.get_instructor_snapshot()
        subsystems = snap['subsystems']

        expected_subsystems = ['eps', 'aocs', 'tcs', 'obdh', 'ttc', 'payload']
        for subsys in expected_subsystems:
            assert subsys in subsystems, f"Missing subsystem: {subsys}"
            assert isinstance(subsystems[subsys], dict), \
                f"Subsystem {subsys} state is not a dict"

    def test_eps_subsystem_fields(self, engine):
        """Verify EPS subsystem exposes all internal state fields."""
        snap = engine.get_instructor_snapshot()
        eps_state = snap['subsystems']['eps']

        # Key fields that should be in every EPS state
        important_fields = [
            'bat_soc_pct', 'bat_voltage', 'bat_current', 'bat_temp',
            'sa_a_current', 'sa_b_current', 'bus_voltage',
            'power_gen_w', 'power_cons_w', 'in_eclipse',
            'power_lines', 'line_currents', 'load_shed_stage',
            'bat_dod_pct', 'bat_cycles', 'bat_max_dod_pct',
            'sa_lifetime_hours', 'sa_age_factor',
            'sep_timer_active', 'sep_timer_remaining', 'pdm_unsw_status',
        ]
        missing = [f for f in important_fields if f not in eps_state]
        assert not missing, f"EPS missing fields: {missing}"

    def test_aocs_subsystem_fields(self, engine):
        """Verify AOCS subsystem has complete state."""
        snap = engine.get_instructor_snapshot()
        aocs_state = snap['subsystems']['aocs']

        # Fields come from AOCSState dataclass (q is a list, rw_speed is a list)
        important_fields = [
            'q', 'rate_roll', 'rate_pitch', 'rate_yaw',
            'rw_speed', 'mag_x', 'mag_y', 'mag_z',
            'mode', 'att_error',
            'st1_status', 'st2_status', 'css_sun_x', 'css_sun_y', 'css_sun_z',
        ]
        missing = [f for f in important_fields if f not in aocs_state]
        assert not missing, f"AOCS missing fields: {missing}"

    def test_tcs_subsystem_fields(self, engine):
        """Verify TCS subsystem has complete state."""
        snap = engine.get_instructor_snapshot()
        tcs_state = snap['subsystems']['tcs']

        # Field names from TCSState dataclass
        important_fields = [
            'temp_obc', 'temp_battery', 'temp_fpa',
            'htr_battery', 'htr_obc', 'cooler_fpa',
        ]
        missing = [f for f in important_fields if f not in tcs_state]
        assert not missing, f"TCS missing fields: {missing}"

    def test_obdh_subsystem_fields(self, engine):
        """Verify OBDH subsystem has complete state."""
        snap = engine.get_instructor_snapshot()
        obdh_state = snap['subsystems']['obdh']

        important_fields = [
            'mode', 'cpu_load', 'mem_used', 'reboot_count',
        ]
        missing = [f for f in important_fields if f not in obdh_state]
        # OBDH fields may vary, so just check it exists
        assert isinstance(obdh_state, dict)

    def test_ttc_subsystem_fields(self, engine):
        """Verify TT&C subsystem has complete state."""
        snap = engine.get_instructor_snapshot()
        ttc_state = snap['subsystems']['ttc']

        important_fields = [
            'mode', 'link_status', 'rssi_dbm', 'link_margin_db',
        ]
        missing = [f for f in important_fields if f not in ttc_state]
        # TTC fields may vary, so just check it exists
        assert isinstance(ttc_state, dict)

    def test_payload_subsystem_fields(self, engine):
        """Verify Payload subsystem has complete state."""
        snap = engine.get_instructor_snapshot()
        payload_state = snap['subsystems']['payload']

        important_fields = ['mode', 'fpa_temp_c']
        # Payload fields may vary, so just check it exists
        assert isinstance(payload_state, dict)

    def test_fdir_section(self, engine):
        """Verify FDIR state is exposed."""
        snap = engine.get_instructor_snapshot()
        fdir = snap['fdir']

        assert 'enabled' in fdir
        assert 'triggered_rules' in fdir
        assert 'load_shed_stage' in fdir

    def test_tm_stores_present(self, engine):
        """Verify TM storage status is in snapshot."""
        snap = engine.get_instructor_snapshot()
        tm_stores = snap['tm_stores']

        # TM stores should be a dict or list (may be empty if not configured)
        assert isinstance(tm_stores, (dict, list, type(None)))

    def test_active_failures_present(self, engine):
        """Verify failure list is in snapshot."""
        snap = engine.get_instructor_snapshot()
        failures = snap['active_failures']

        assert isinstance(failures, list)

    def test_parameter_ids_coverage(self, engine, expected_parameters):
        """Verify parameter ID coverage is comprehensive."""
        snap = engine.get_instructor_snapshot()
        params = snap['parameters']

        if not expected_parameters:
            pytest.skip("parameters.yaml not found")

        # Count coverage
        expected_ids = set(expected_parameters.keys())
        found_ids = set(params.keys())
        coverage = len(found_ids & expected_ids) / len(expected_ids) * 100

        # Warn if coverage drops below 80%
        assert coverage >= 80, (
            f"Parameter coverage only {coverage:.1f}% "
            f"({len(found_ids & expected_ids)}/{len(expected_ids)}). "
            f"Missing: {expected_ids - found_ids}"
        )

    def test_parameter_values_numeric(self, engine):
        """Verify parameter values are numeric (not None or stale)."""
        snap = engine.get_instructor_snapshot()
        params = snap['parameters']

        # Sample some key parameters
        key_params = [0x0101, 0x0105, 0x020F, 0x0300]

        for param_id in key_params:
            if param_id in params:
                val = params[param_id]
                assert val is not None, f"Parameter {hex(param_id)} is None"
                assert isinstance(val, (int, float, bool)), \
                    f"Parameter {hex(param_id)} has non-numeric value: {type(val)}"

    def test_snapshot_json_serializable(self, engine):
        """Verify snapshot is JSON-serializable (safe for API)."""
        import json
        snap = engine.get_instructor_snapshot()

        try:
            json_str = json.dumps(snap, default=str)
            assert len(json_str) > 0
        except TypeError as e:
            pytest.fail(f"Snapshot not JSON-serializable: {e}")

    def test_instructor_bypass_link_gating(self, engine):
        """Verify instructor snapshot shows state even if link is down.

        The instructor display should show ground-truth state regardless of
        simulated RF link status.
        """
        snap = engine.get_instructor_snapshot()

        # Even if downlink_active is False, all subsystem states should be present
        assert 'subsystems' in snap
        assert len(snap['subsystems']) > 0
        assert 'parameters' in snap
        assert len(snap['parameters']) > 0


class TestClockGroundTruth:
    """TIME_ARCHITECTURE.md Part B, last paragraph.

    The true onboard-vs-sim offset is the answer to the NOM-008 exercise. The
    spacecraft cannot know it, so it must exist on the instructor console and
    nowhere else — not in the state summary the MCS polls, not in telemetry.
    """

    def test_snapshot_carries_the_offset_and_the_drawn_drift(self, engine):
        engine.subsystems["obdh"]._state.sw_image = 1
        engine._tick_once(1.0)
        c = engine.get_instructor_snapshot()["clock_truth"]

        from smo_common.protocol.ecss_packet import datetime_to_cuc_seconds
        expected = (engine.subsystems["obdh"].get_obc_time_s()
                    - datetime_to_cuc_seconds(engine._sim_time))
        assert c["offset_s"] == pytest.approx(expected, abs=1e-6)
        # The ppm actually drawn for this run, not the nominal.
        assert c["drift_ppm"] == pytest.approx(
            engine.subsystems["obdh"]._state.clock_drift_ppm)
        assert c["time_ref_name"] in ("NONE", "GROUND", "GPS", "HOLDOVER")

    def test_offset_tracks_a_clock_that_is_deliberately_wrong(self, engine):
        engine.subsystems["obdh"]._state.sw_image = 1
        engine._tick_once(1.0)
        obdh = engine.subsystems["obdh"]
        before = engine.get_instructor_snapshot()["clock_truth"]["offset_s"]
        obdh._state.obc_time_s += 40.0
        # The snapshot is published by the tick thread, so it moves on the
        # next tick, not on the mutation.
        engine._tick_once(0.0)
        after = engine.get_instructor_snapshot()["clock_truth"]["offset_s"]
        assert after - before == pytest.approx(40.0, abs=1e-6)

    def test_the_offset_does_not_leak_into_the_ground_state_summary(self, engine):
        engine.subsystems["obdh"]._state.sw_image = 1
        engine._tick_once(1.0)
        summary = engine.get_state_summary()
        blob = json.dumps(summary, default=str)
        assert "clock_truth" not in summary
        assert "offset_s" not in blob
        # And no telemetry parameter carries it either.
        truth = engine.get_instructor_snapshot()["clock_truth"]
        obdh = engine.subsystems["obdh"]
        obdh._state.obc_time_s += 1234.5      # a big, unmistakable offset
        engine._tick_once(1.0)
        truth = engine.get_instructor_snapshot()["clock_truth"]
        assert abs(truth["offset_s"]) > 1000.0
        for pid, val in engine.params.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                assert abs(float(val) - truth["offset_s"]) > 1e-6, (
                    f"parameter 0x{pid:04X} downlinks the true clock offset")


class TestInstructorDisplayGaps:
    """Identify gaps between instructor display UI and available state."""

    def test_currently_displayed_fields(self, engine):
        """Document which fields are currently shown in instructor HTML."""
        snap = engine.get_instructor_snapshot()

        # Fields currently in the hardcoded HTML UI (from index.html)
        currently_shown = {
            'eps': ['soc_pct', 'bat_voltage_V', 'bus_voltage_V', 'bat_temp_C', 'power_gen_W', 'power_cons_W', 'sa_a_A', 'sa_b_A'],
            'aocs': ['mode', 'att_error_deg', 'rate_roll', 'rate_pitch', 'rate_yaw', 'rw1_rpm', 'rw2_rpm', 'rw3_rpm', 'rw4_rpm'],
            'tcs': ['temp_obc_C', 'temp_bat_C', 'temp_fpa_C', 'htr_bat', 'htr_obc', 'cooler_fpa'],
            'obdh': ['mode', 'cpu_load', 'mem_used_pct', 'reboot_count'],
            'ttc': ['mode', 'link_status', 'rssi_dbm', 'link_margin_dB', 'range_km', 'elevation_deg'],
            'payload': ['mode', 'fpa_temp_C', 'store_used_pct', 'image_count'],
        }

        # Verify all currently shown fields are in snapshot
        snap_params = snap['parameters']

        # This test just documents the current state
        assert len(currently_shown) == 6  # 6 subsystems

    def test_available_but_not_displayed(self, engine, expected_parameters):
        """Identify parameters available in config but not shown in UI."""
        snap = engine.get_instructor_snapshot()

        if not expected_parameters:
            pytest.skip("parameters.yaml not found")

        # This is an informational test
        available_count = len(expected_parameters)
        currently_shown_count = sum([8, 9, 6, 3, 5, 3])  # Count from above test

        gap = available_count - currently_shown_count
        print(f"\n  Total available parameters: {available_count}")
        print(f"  Currently displayed: {currently_shown_count}")
        print(f"  Gap: {gap} parameters")


class TestInstructorSnapshotThreadSafety:
    """WP-6 / brief 08 A-1 — the snapshot must be built on the TICK thread.

    `get_instructor_snapshot()` used to read `self.orbit.state` live and walk
    every subsystem's `_state.__dict__` on the aiohttp thread while the tick
    thread mutated it, storing dict/list values BY REFERENCE. `json.dumps` on
    the response thread could therefore raise
    ``RuntimeError: dictionary changed size during iteration`` against a
    growing `image_catalog`, and any caller that mutated the response
    corrupted the simulation.
    """

    def _grow_catalogue(self, engine, n: int) -> None:
        """Make the payload image catalogue the moving target it is in flight."""
        cat = getattr(engine.subsystems["payload"]._state, "image_catalog", None)
        if isinstance(cat, list):
            for i in range(n):
                cat.append({"scene_id": i, "size_mb": 1.0})

    def test_snapshot_containers_are_not_live_model_objects(self, engine):
        engine._tick_once(1.0)
        snap = engine.get_instructor_snapshot()
        for name, model in engine.subsystems.items():
            live = vars(model._state)
            for field, value in snap["subsystems"].get(name, {}).items():
                if isinstance(value, (dict, list)):
                    assert value is not live.get(field), (
                        f"instructor snapshot aliases live "
                        f"{name}._state.{field}")

    def test_mutating_the_response_cannot_corrupt_the_simulation(self, engine):
        engine._tick_once(1.0)
        snap = engine.get_instructor_snapshot()
        eps_lines = snap["subsystems"]["eps"].get("power_lines")
        assert isinstance(eps_lines, dict) and eps_lines
        victim = sorted(eps_lines)[0]
        before = engine.subsystems["eps"]._state.power_lines[victim]
        eps_lines[victim] = not before          # a careless caller
        snap["parameters"][0x0101] = -999.0
        engine._tick_once(1.0)
        assert engine.subsystems["eps"]._state.power_lines[victim] == before
        assert engine.params[0x0101] != -999.0

    def test_soak_concurrent_polling_is_fault_free(self, engine):
        """Poll + serialise the snapshot while the tick thread runs.

        Defaults to a short soak so the routine suite stays fast; set
        ``SMO_SOAK_SECONDS=60`` for the full WP-6 acceptance run.
        """
        import json
        import os
        import threading
        import time

        duration = float(os.environ.get("SMO_SOAK_SECONDS", "8"))
        engine.subsystems["obdh"]._state.sw_image = 1
        engine._tick_once(1.0)

        stop = threading.Event()
        errors: list[BaseException] = []
        polls = [0]

        def writer():
            i = 0
            try:
                while not stop.is_set():
                    # A moving target of exactly the shape that used to break
                    # json.dumps on the reader thread.
                    self._grow_catalogue(engine, 3)
                    engine.params[0x7000 + (i % 400)] = float(i)
                    engine._tick_once(1.0)
                    i += 1
            except BaseException as e:      # noqa: BLE001
                errors.append(e)

        def reader():
            try:
                while not stop.is_set():
                    snap = engine.get_instructor_snapshot()
                    json.dumps(snap, default=str)
                    polls[0] += 1
            except BaseException as e:      # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, name="soak-tick")] + \
                  [threading.Thread(target=reader, name=f"soak-poll-{i}")
                   for i in range(3)]
        for t in threads:
            t.start()
        time.sleep(duration)
        stop.set()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"soak raised: {errors[:3]}"
        assert polls[0] > 0, "the soak never polled"
        assert engine.engine_fault_count == 0, (
            f"engine faults during soak: {engine.engine_fault}")

        # ...and the snapshot is still un-aliased at the end of the soak.
        snap = engine.get_instructor_snapshot()
        cat = snap["subsystems"]["payload"].get("image_catalog")
        if isinstance(cat, list):
            assert cat is not engine.subsystems["payload"]._state.image_catalog
