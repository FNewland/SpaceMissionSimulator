"""The launch scripts, the deployment guide and the installation manual must
agree with each other and with the code (brief 08 D-1…D-5, WP-11).

Every defect in that block was a *documentation-versus-behaviour* defect: the
`--public` banner claimed loopback while three servers hard-coded `0.0.0.0`;
one printed URL used `localhost` while every other line used `$ACCESS_HOST`;
`start.sh` reached the network in an offline classroom; `deploy/start-all.sh`
started three of the eight services the guide describes. None of them is
catchable by a unit test of any one file — they are contracts *between*
artefacts, which is where this project's recurring defects live.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
START_SH = (REPO / "start.sh").read_text()
DEPLOY = REPO / "deploy"
DEPLOY_SCRIPTS = sorted(DEPLOY.glob("start-*.sh"))
DEPLOYMENT_MD = (REPO / "docs" / "DEPLOYMENT.md").read_text()
INSTALL_MANUAL = (DEPLOY / "INSTALLATION_MANUAL.html").read_text()
ENV_CONF = (DEPLOY / "smo-env.conf").read_text()

#: The service ports the suite is documented to expose, and the launcher line
#: that must start each one.
TOOL_PORTS = {
    8092: "tools/delayed_tm_viewer.py",
    8093: "tools/orbit_tools.py",
    8095: "tools/doc_viewer.py",
    8096: "tools/scenario_generator.py",
    8097: "tools/adcs_sim_viewer.py",
}
CORE_PORTS = {8080: "smo-simulator", 9090: "smo-mcs", 9091: "smo-planner"}


# ── Bind addresses: the banner and the sockets must agree (D-2) ─────────

@pytest.mark.parametrize("rel", [
    "packages/smo-simulator/src/smo_simulator/server.py",
    "packages/smo-mcs/src/smo_mcs/server.py",
    "packages/smo-planner/src/smo_planner/server.py",
])
def test_core_servers_accept_a_bind_address(rel):
    """All three hard-coded `0.0.0.0` while start.sh claimed loopback."""
    src = (REPO / rel).read_text()
    assert '"--host"' in src, f"{rel} has no --host argument"
    assert "SMO_BIND" in src, f"{rel} does not honour $SMO_BIND"


def _launch_lines(src: str, svc: str) -> list[str]:
    """Lines that actually invoke a console script, not lines that mention it
    in an install hint or a comment."""
    out = []
    joined = re.sub(r"\\\n\s*", " ", src)   # fold shell line continuations
    for ln in joined.splitlines():
        s = ln.strip()
        if s.startswith("#") or s.startswith("echo"):
            continue
        if re.match(rf"^{re.escape(svc)}\s+--", s):
            out.append(s)
    return out


def test_start_sh_passes_the_bind_to_every_core_service():
    for svc in CORE_PORTS.values():
        lines = _launch_lines(START_SH, svc)
        assert lines, f"start.sh does not launch {svc}"
        for ln in lines:
            assert '--host "$SMO_BIND"' in ln, \
                f"start.sh starts {svc} without --host: {ln}"


def test_local_mode_binds_loopback():
    """`--public` must be the thing that opens the sockets up, not a banner."""
    assert 'SMO_BIND="${SMO_BIND:-127.0.0.1}"' in START_SH
    assert 'SMO_BIND="${SMO_BIND:-0.0.0.0}"' in START_SH


def test_deploy_scripts_pass_their_configured_bind():
    """MCS_BIND / PLANNER_BIND / SIMULATOR_BIND / GATEWAY_BIND were declared in
    smo-env.conf and read by nobody."""
    expected = {
        "start-simulator.sh": "SIMULATOR_BIND",
        "start-mcs.sh": "MCS_BIND",
        "start-planner.sh": "PLANNER_BIND",
        "start-gateway.sh": "GATEWAY_BIND",
    }
    for name, var in expected.items():
        src = (DEPLOY / name).read_text()
        assert var in src, f"deploy/{name} ignores ${var}"
        assert var in ENV_CONF, f"smo-env.conf does not define {var}"
    all_sh = (DEPLOY / "start-all.sh").read_text()
    for var in expected.values():
        assert var in all_sh, f"deploy/start-all.sh ignores ${var}"


# ── Printed URLs must be reachable addresses (D-4) ──────────────────────

def test_every_printed_url_uses_the_access_host():
    """`Radio Status: http://localhost:8094` was the live instance of #90."""
    bad = [ln.strip() for ln in START_SH.splitlines()
           if "http://localhost" in ln and not ln.strip().startswith("#")]
    assert not bad, f"start.sh prints a hard-coded localhost URL: {bad}"


# ── No network access from a start script (D-3) ────────────────────────

def test_no_start_script_installs_from_the_network_unasked():
    scripts = {"start.sh": START_SH}
    scripts.update({f"deploy/{p.name}": p.read_text() for p in DEPLOY_SCRIPTS})
    offenders = []
    for name, src in scripts.items():
        for ln in src.splitlines():
            s = ln.strip()
            if s.startswith("#") or "pip install" not in s:
                continue
            if "--no-index" in s or "echo" in s:
                continue
            # An explicit, documented opt-in is allowed.
            if "SMO_ALLOW_NETWORK_INSTALL" in src and \
                    src.index("SMO_ALLOW_NETWORK_INSTALL") < src.index(s):
                continue
            offenders.append(f"{name}: {s}")
    assert not offenders, (
        "a start script reaches the network — in an offline classroom this "
        f"stalls and misattributes the failure: {offenders}")


def test_start_sh_explains_the_offline_path():
    assert "wheels" in START_SH
    assert "SMO_ALLOW_NETWORK_INSTALL" in START_SH


# ── Readiness, not sleep (D-1) ─────────────────────────────────────────

@pytest.mark.parametrize("script", ["start.sh", "deploy/start-all.sh"])
def test_dependants_wait_on_a_readiness_probe(script):
    src = (REPO / script).read_text()
    assert "wait_for_http" in src and "wait_for_port" in src, \
        f"{script} has no readiness probe"
    assert "die " in src or "die(" in src, \
        f"{script} does not abort when a service fails to come up"
    # The simulator is the root dependency and must be probed before the MCS
    # is launched: a dead simulator must not leave an empty MCS console up.
    folded = re.sub(r"\\\n\s*", " ", src)
    mcs_launch = _launch_lines(src, "smo-mcs")
    assert mcs_launch, f"{script} does not launch smo-mcs"
    assert folded.index('wait_for_http "Simulator"') < folded.index(mcs_launch[0]), \
        f"{script} starts the MCS before probing the simulator"


@pytest.mark.parametrize("script", ["start.sh", "deploy/start-all.sh"])
def test_a_dead_service_stops_the_suite(script):
    """A bare `wait` waited for ALL children, so a dead simulator left a
    frozen but plausible console up."""
    src = (REPO / script).read_text()
    lines = [ln.strip() for ln in src.splitlines()]
    assert "wait" not in lines[-15:], \
        f"{script} still ends with a bare `wait`"
    assert "kill -0" in src, f"{script} does not supervise its children"


# ── Every service that exists is started, and every started service is
#    documented at the port it actually uses ──────────────────────────────

@pytest.mark.parametrize("port,launcher", sorted(TOOL_PORTS.items()))
def test_start_sh_starts_every_tool_viewer(port, launcher):
    assert launcher in START_SH, f"start.sh never starts {launcher}"
    assert str(port) in START_SH, f"start.sh does not use port {port}"


@pytest.mark.parametrize("port,launcher", sorted(TOOL_PORTS.items()))
def test_start_all_starts_every_tool_viewer(port, launcher):
    """deploy/start-all.sh used to start only the three core services, so a
    classroom launched from deploy/ silently lost five tools."""
    src = (DEPLOY / "start-all.sh").read_text()
    assert launcher in src, f"deploy/start-all.sh never starts {launcher}"
    assert str(port) in src, f"deploy/start-all.sh does not use port {port}"


@pytest.mark.parametrize("port", sorted(TOOL_PORTS) + sorted(CORE_PORTS))
def test_every_started_port_is_in_the_deployment_guide(port):
    assert str(port) in DEPLOYMENT_MD, \
        f"port {port} is started but not documented in docs/DEPLOYMENT.md"
    assert str(port) in INSTALL_MANUAL, \
        f"port {port} is started but not in deploy/INSTALLATION_MANUAL.html"


def test_every_tool_port_is_configurable_from_smo_env():
    for var in ("DELAYED_TM_PORT", "ORBIT_TOOLS_PORT", "DOC_VIEWER_PORT",
                "SCENARIO_GEN_PORT", "ADCS_VIEWER_PORT", "START_TOOLS",
                "TOOLS_BIND"):
        assert var in ENV_CONF, f"smo-env.conf does not define {var}"
        assert var in (DEPLOY / "start-all.sh").read_text(), \
            f"deploy/start-all.sh ignores ${var}"


# ── The Phase F surfaces are documented where an instructor will look ───

@pytest.mark.parametrize("doc,text", [
    ("docs/DEPLOYMENT.md", DEPLOYMENT_MD),
    ("deploy/INSTALLATION_MANUAL.html", INSTALL_MANUAL),
])
@pytest.mark.parametrize("needle", ["/config", "/reports", "/dumps"])
def test_the_hosted_pages_are_documented(doc, text, needle):
    assert needle in text, f"{doc} does not mention {needle}"


def test_the_config_editor_is_documented_as_instructor_only():
    """8080 is the instructor's door; 9090 is the students'. The write surface
    exists only on 8080 and the guide must say so, because that is the whole
    safety argument for hosting a config-write API at all."""
    assert "8080/config" in DEPLOYMENT_MD or "8080</code>/config" in DEPLOYMENT_MD \
        or re.search(r"8080.{0,120}/config", DEPLOYMENT_MD, re.S), \
        "docs/DEPLOYMENT.md does not place the config editor on 8080"
    assert re.search(r"read-only", DEPLOYMENT_MD, re.I), \
        "docs/DEPLOYMENT.md does not say the MCS/planner copies are read-only"


def test_the_config_editor_write_surface_is_only_on_the_simulator():
    sim = (REPO / "packages/smo-simulator/src/smo_simulator/instructor/app.py"
           ).read_text()
    assert "readonly=False" in sim
    for rel in ("packages/smo-mcs/src/smo_mcs/server.py",
                "packages/smo-planner/src/smo_planner/server.py"):
        src = (REPO / rel).read_text()
        assert "register_config_routes" in src
        assert "readonly=True" in src, \
            f"{rel} mounts the config editor writable — 9090/9091 are the " \
            f"students' ports"
