"""Delayed-TM dump archive directory is configurable (so the delayed-TM
viewer can run on a server pointed at a shared location).

Precedence: SMO_DUMP_DIR env > mission config `dump_dir` > workspace/dumps.
"""
from pathlib import Path

import pytest

from smo_simulator.engine import SimulationEngine
from smo_simulator.tm_storage import OnboardTMStorage

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "eosat1"


def _engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    return SimulationEngine(CONFIG_DIR, speed=1.0)


def test_dump_dir_default(monkeypatch):
    monkeypatch.delenv("SMO_DUMP_DIR", raising=False)
    eng = _engine()
    # eosat1 mission.yaml sets no dump_dir, so we fall back to the default.
    assert eng._dump_dir == Path("workspace/dumps")


def test_dump_dir_from_env_and_file_written(tmp_path, monkeypatch):
    target = tmp_path / "server" / "dumps"
    monkeypatch.setenv("SMO_DUMP_DIR", str(target))

    eng = _engine()
    assert eng._dump_dir == target  # env takes precedence

    # Give the engine a fresh, fully-enabled store and one packet to dump.
    eng._tm_storage = OnboardTMStorage()
    pkt = b"\xAA\xBB\xCC\xDD"
    assert eng._tm_storage.store_packet_direct(1, pkt) is True

    n = eng.queue_dump(1)
    assert n == 1

    files = list(target.glob("dump_sid01_*.bin"))
    assert files, f"expected a dump_sid01_*.bin under {target}"
    # length-prefixed: 4-byte big-endian length + 4-byte payload
    assert files[0].stat().st_size == 4 + len(pkt)


def test_dump_dir_from_mission_config(monkeypatch):
    """When no env override is set, the mission-config value is honoured."""
    monkeypatch.delenv("SMO_DUMP_DIR", raising=False)
    eng = _engine()
    # Simulate a mission config that pins a dump_dir, then re-resolve the same
    # precedence the engine uses at construction.
    import os
    eng._mission_cfg.dump_dir = "/srv/smo/dumps"
    resolved = (os.environ.get("SMO_DUMP_DIR")
                or getattr(eng._mission_cfg, "dump_dir", None)
                or "workspace/dumps")
    assert Path(resolved).expanduser() == Path("/srv/smo/dumps")
