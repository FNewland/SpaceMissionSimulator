"""Fixtures for E2E browser tests.

Manages the full system stack (simulator + RF bridge + MCS) as
subprocesses, and provides a Playwright page fixture pointed at
the MCS web UI.
"""

import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "configs" / "eosat1"
RFSIM_CONFIG = CONFIG_DIR / "rfsim.yaml"

# Default ports (from configs)
SIM_TM_PORT = 8002
SIM_HTTP_PORT = 8080
BRIDGE_TM_PORT = 8012
MCS_PORT = 9090


def _wait_for_port(port: int, host: str = "localhost", timeout: float = 30.0) -> bool:
    """Wait until a TCP port is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except (ConnectionRefusedError, OSError, socket.timeout):
            time.sleep(0.3)
    return False


def _port_in_use(port: int) -> bool:
    """Check if a port is in use."""
    try:
        with socket.create_connection(("localhost", port), timeout=0.5):
            return True
    except (ConnectionRefusedError, OSError):
        return False


@pytest.fixture(scope="module")
def system_stack():
    """Start the full system stack and yield when ready.

    Uses default ports (8001/8002/8080 for sim, 8012/8011 for bridge,
    9090 for MCS). Skips if ports are already in use.
    """
    # Check ports are free
    for port in [SIM_TM_PORT, SIM_HTTP_PORT, BRIDGE_TM_PORT, MCS_PORT]:
        if _port_in_use(port):
            pytest.skip(f"Port {port} already in use — system may be running")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    procs = []

    try:
        # 1. Start Simulator
        sim_proc = subprocess.Popen(
            ["smo-simulator", "--config", str(CONFIG_DIR)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT))
        procs.append(sim_proc)

        if not _wait_for_port(SIM_TM_PORT, timeout=15):
            raise RuntimeError("Simulator failed to start (port 8002)")

        # 2. Enable pass override
        time.sleep(1.0)
        try:
            data = json.dumps({"type": "override_passes", "enabled": True}).encode()
            req = urllib.request.Request(
                f"http://localhost:{SIM_HTTP_PORT}/api/command",
                data=data, headers={"Content-Type": "application/json"},
                method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # Will retry later

        # 3. Start RF Bridge
        bridge_proc = subprocess.Popen(
            ["smo-rfsim", "--config", str(RFSIM_CONFIG),
             "--mode", "RF", "--radio-web"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT))
        procs.append(bridge_proc)

        if not _wait_for_port(BRIDGE_TM_PORT, timeout=15):
            raise RuntimeError("RF Bridge failed to start (port 8012)")

        # 4. Start MCS (connected through bridge)
        mcs_proc = subprocess.Popen(
            ["smo-mcs", "--config", str(CONFIG_DIR),
             "--port", str(MCS_PORT),
             "--connect", f"localhost:{BRIDGE_TM_PORT}",
             "--tc-port", "8011"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT))
        procs.append(mcs_proc)

        if not _wait_for_port(MCS_PORT, timeout=15):
            raise RuntimeError("MCS failed to start (port 9090)")

        # 5. Start Planner
        planner_proc = subprocess.Popen(
            ["smo-planner", "--config", str(CONFIG_DIR), "--port", "9091"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT))
        procs.append(planner_proc)

        # 6. Start Delayed TM Viewer
        dtm_proc = subprocess.Popen(
            ["python", str(PROJECT_ROOT / "tools" / "delayed_tm_viewer.py"),
             "--port", "8092",
             "--dumps", str(PROJECT_ROOT / "workspace" / "dumps")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT))
        procs.append(dtm_proc)

        # 7. Start Orbit Tools
        orb_proc = subprocess.Popen(
            ["python", str(PROJECT_ROOT / "tools" / "orbit_tools.py"),
             "--serve", "--port", "8093"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT))
        procs.append(orb_proc)

        # Wait for ground tools
        _wait_for_port(9091, timeout=10)
        _wait_for_port(8092, timeout=10)
        _wait_for_port(8093, timeout=10)

        # Give everything time to stabilize
        time.sleep(3.0)

        yield {
            "sim_http": f"http://localhost:{SIM_HTTP_PORT}",
            "mcs_url": f"http://localhost:{MCS_PORT}",
            "procs": procs,
        }

    finally:
        for proc in procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


RADIO_PORT = 8094


@pytest.fixture(scope="module")
def mcs_page(system_stack, browser):
    """Provide a Playwright page connected to the MCS."""
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    page.goto(system_stack["mcs_url"])

    # Wait for WebSocket connection (generous timeout for RF lock)
    try:
        page.wait_for_selector("#ws-dot.connected", timeout=20000)
    except Exception:
        # Fallback: check if page loaded at all
        page.wait_for_load_state("domcontentloaded")

    yield page
    context.close()


@pytest.fixture(scope="module")
def multi_window(system_stack, browser):
    """Provide three side-by-side browser windows: MCS, Radio, Instructor.

    Returns a dict with 'mcs', 'radio', 'instructor' Page objects.
    All three are visible simultaneously when using --headed.
    """
    # Screen layout: MCS (left half), Radio (top-right), Instructor (bottom-right)
    ctx = browser.new_context(viewport={"width": 960, "height": 1080})

    # MCS — main operator display
    mcs = ctx.new_page()
    mcs.goto(system_stack["mcs_url"])
    try:
        mcs.wait_for_selector("#ws-dot.connected", timeout=20000)
    except Exception:
        mcs.wait_for_load_state("domcontentloaded")

    # Radio dashboard — RF lock indicators
    radio = ctx.new_page()
    radio.goto(f"http://localhost:{RADIO_PORT}")
    radio.wait_for_load_state("domcontentloaded")

    # Instructor — sim control, pass override, failures
    instructor = ctx.new_page()
    instructor.goto(f"{system_stack['sim_http']}")
    instructor.wait_for_load_state("domcontentloaded")

    # Delayed TM Viewer — stored TM dumps (optional)
    delayed_tm = ctx.new_page()
    try:
        delayed_tm.goto("http://localhost:8092", timeout=10000)
        delayed_tm.wait_for_load_state("domcontentloaded")
    except Exception:
        delayed_tm.set_content("<h1>Delayed TM Viewer — not available</h1>")

    # Orbit Tools — TLE/state vector converter (optional)
    orbit_tools = ctx.new_page()
    try:
        orbit_tools.goto("http://localhost:8093", timeout=10000)
        orbit_tools.wait_for_load_state("domcontentloaded")
    except Exception:
        orbit_tools.set_content("<h1>Orbit Tools — not available</h1>")

    # Planner — pass scheduling, budgets (optional)
    planner = ctx.new_page()
    try:
        planner.goto("http://localhost:9091", timeout=10000)
        planner.wait_for_load_state("domcontentloaded")
    except Exception:
        planner.set_content("<h1>Planner — not available</h1>")

    yield {
        "mcs": mcs,
        "radio": radio,
        "instructor": instructor,
        "delayed_tm": delayed_tm,
        "orbit_tools": orbit_tools,
        "planner": planner,
    }
    ctx.close()
