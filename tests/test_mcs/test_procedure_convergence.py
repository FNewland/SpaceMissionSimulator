"""Procedure convergence — roadmap X-12, audit Q7, defect #83.

Before this there were four divergent procedure representations and only one
path between them worked. These tests pin the convergence:

  * a builder-saved procedure ROUND-TRIPS save -> list -> load -> run ->
    COMPLETED, which is the concrete gap an operator hit (the backend accepted
    a step list; the UI had no way to send one);
  * step descriptions render as readable TEXT, not raw Python dicts;
  * a ``tlm_check`` naming a parameter the runner cannot read is REJECTED at
    save time (it used to be accepted and then time out for 30 s mid-pass);
  * the canonical schema validates, and the adapters convert every shipped
    dialect without loss;
  * the ``runnable:`` links in procedure_index.yaml resolve to real steps, and
    the markdown of the linked document is servable to the UI.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_common.config.schemas import MCSDisplayConfig
from smo_common.procedures import (
    CANONICAL_SCHEMA_VERSION, describe_step, from_activity_sequence,
    from_builder_steps, from_markdown, to_canonical, validate_steps,
)
from smo_mcs.procedure_runner import ProcedureRunner, ProcedureState
from smo_mcs.server import MCSServer


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"

# A telemetry snapshot in the same shape the MCS caches from /api/state.
STATE = {
    "sc_mode": 1,
    # `aocs` is in the engine's real state summary and NOM-002's runnable
    # steps now check it: 2026-08b PL-a added an AOCS_SET_MODE(FINE_POINT) +
    # `wait_for aocs.mode == 5` to activity_types.yaml `imaging_pass`, without
    # which every planner-scheduled capture was refused by the payload's own
    # pointing gate. A stub that omits `aocs` makes the runner's vocabulary
    # check reject a step the real spacecraft can execute.
    "aocs": {"mode": 5, "att_error_deg": 0.01},
    "payload": {"mode": 2, "store_used": 12.5, "fpa_ready": True},
    "eps": {"soc_pct": 80.0, "bus_voltage_V": 28.0},
    "ttc": {"link_status": 1, "mode": 0},
}


# ── Helpers ─────────────────────────────────────────────────────────

def _server(config_dir) -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        return MCSServer(config_dir=str(config_dir), connect_host="localhost",
                         connect_port=9999, http_port=0)


def _app(server: MCSServer) -> web.Application:
    app = web.Application()
    app.router.add_post("/api/procedure/save", server._handle_proc_save)
    app.router.add_get("/api/procedure/custom", server._handle_proc_custom_list)
    app.router.add_post("/api/procedure/load", server._handle_proc_load)
    app.router.add_get("/api/procedure/status", server._handle_proc_status)
    app.router.add_get("/api/procedure/index", server._handle_proc_index)
    app.router.add_get("/api/procedure/tlm-vocabulary",
                       server._handle_proc_tlm_vocabulary)
    app.router.add_get("/api/procedures", server._handle_procedures_list)
    app.router.add_get("/api/procedures/{path:.+}", server._handle_procedure_file)
    return app


async def _client(server: MCSServer) -> TestClient:
    client = TestClient(TestServer(_app(server)))
    await client.start_server()
    return client


@pytest.fixture
def tmp_server(tmp_path):
    """Server whose config dir is a temp tree, with live telemetry present."""
    (tmp_path / "procedures").mkdir(parents=True, exist_ok=True)
    server = _server(tmp_path)
    server._latest_state = dict(STATE)
    return server


@pytest.fixture
def real_server():
    """Server on the REAL mission config (procedure index, activity types)."""
    server = _server(CONFIG_DIR)
    server._latest_state = dict(STATE)
    return server


# ── 1. The builder-saved procedure round trip ───────────────────────

BUILDER_STEPS = [
    {"type": "command", "service": 8, "subtype": 1, "data_hex": "1a02",
     "description": "PLD IMAGING"},
    {"type": "wait", "seconds": 0},
    {"type": "tlm_check", "parameter": "payload.mode", "condition": "==",
     "value": 2, "timeout_s": 5},
    {"type": "go_nogo", "label": "Imaging configuration verified?"},
]


class TestBuilderProcedureRoundTrip:

    @pytest.mark.asyncio
    async def test_save_list_load_run_completes(self, tmp_server):
        """save -> list -> load-by-path -> run -> COMPLETED.

        The whole point of defect #83: a procedure an operator builds in the
        MCS can now be executed from the MCS.
        """
        sent = []

        async def _send(service, subtype, data_hex=""):
            sent.append((service, subtype, data_hex))
            return {"status": "sent", "seq": len(sent)}

        tmp_server._procedure_runner = ProcedureRunner(
            send_command_fn=_send,
            get_telemetry_fn=tmp_server._proc_get_telemetry,
        )
        client = await _client(tmp_server)
        try:
            save = await client.post("/api/procedure/save", json={
                "name": "Round Trip", "steps": BUILDER_STEPS,
                "position": "payload_ops"})
            assert save.status == 200, await save.text()
            path = (await save.json())["path"]

            listing = await (await client.get("/api/procedure/custom")).json()
            assert any(p["path"] == path for p in listing["procedures"])

            load = await client.post("/api/procedure/load",
                                     json={"path": path})
            assert load.status == 200, await load.text()
            loaded = await load.json()
            assert loaded["state"] == "LOADED"
            assert loaded["total_steps"] == len(BUILDER_STEPS)

            await tmp_server._procedure_runner.start()
            for _ in range(60):
                await asyncio.sleep(0.05)
                if tmp_server._procedure_runner.state != ProcedureState.RUNNING:
                    break
            assert tmp_server._procedure_runner.state == ProcedureState.COMPLETED
            assert sent == [(8, 1, "1a02")]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_saved_file_carries_the_canonical_schema_version(self, tmp_server):
        client = await _client(tmp_server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "Versioned", "steps": BUILDER_STEPS})
            assert resp.status == 200
            path = Path(tmp_server.config_dir) / "procedures" / \
                (await resp.json())["path"]
            data = yaml.safe_load(path.read_text())
            assert data["schema_version"] == CANONICAL_SCHEMA_VERSION
            assert [s["type"] for s in data["steps"]] == [
                "command", "wait", "tlm_check", "go_nogo"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_explicit_step_list_can_be_run_unsaved(self, tmp_server):
        """"Run These Steps" — load a builder step list without saving."""
        client = await _client(tmp_server)
        try:
            resp = await client.post("/api/procedure/load", json={
                "name": "Unsaved", "steps": BUILDER_STEPS})
            assert resp.status == 200
            assert (await resp.json())["total_steps"] == 4
        finally:
            await client.close()


# ── 2. Readable step descriptions ───────────────────────────────────

class TestStepDescriptionsAreText:
    """`_step_desc` had no branch for tagged steps, so the operator's step
    list showed `{'type': 'wait', 'seconds': 1, ...}` verbatim."""

    @pytest.mark.parametrize("step,expected", [
        ({"type": "wait", "seconds": 5}, "Wait 5s"),
        ({"type": "go_nogo", "label": "Proceed?"}, "GO/NO-GO: Proceed?"),
        ({"type": "note", "text": "log the scene id"}, "Note: log the scene id"),
    ])
    def test_tagged_steps_render_as_text(self, step, expected):
        assert describe_step(step) == expected

    def test_tlm_check_renders_the_condition(self):
        text = describe_step({"type": "tlm_check", "parameter": "payload.mode",
                              "condition": ">=", "value": 2, "timeout_s": 30})
        assert "payload.mode" in text and ">=" in text
        assert "{" not in text

    def test_no_step_description_is_a_python_dict(self):
        runner = ProcedureRunner(send_command_fn=AsyncMock(),
                                 get_telemetry_fn=lambda p: None)
        runner.load("Builder", BUILDER_STEPS)
        for result in runner.step_results:
            description = result["description"]
            assert isinstance(description, str)
            assert "{" not in description and "'type'" not in description
            assert description.strip()

    def test_untagged_dialect_still_renders(self):
        assert describe_step({"wait_s": 5}) == "Wait 5s"
        assert describe_step(
            {"service": 8, "subtype": 1, "description": "Enable payload"}
        ) == "Enable payload"


# ── 3. Unknown tlm_check parameters are rejected at save ────────────

class TestParameterValidation:

    @pytest.mark.asyncio
    async def test_unknown_parameter_is_rejected_at_save(self, tmp_server):
        """`ttc.pa_on` is a real catalogue parameter but is NOT in the state
        snapshot the runner reads — exactly the case that used to cost a 30 s
        timeout and a FAILED procedure."""
        client = await _client(tmp_server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "Bad Param",
                "steps": [{"type": "tlm_check", "parameter": "ttc.pa_on",
                           "condition": "==", "value": 1}]})
            assert resp.status == 400
            body = await resp.json()
            assert "ttc.pa_on" in " ".join(body["problems"])
            saved = Path(tmp_server.config_dir) / "procedures" / "custom"
            assert not saved.exists() or not list(saved.glob("*.yaml"))
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_known_parameter_is_accepted(self, tmp_server):
        client = await _client(tmp_server)
        try:
            resp = await client.post("/api/procedure/save", json={
                "name": "Good Param",
                "steps": [{"type": "tlm_check", "parameter": "payload.mode",
                           "condition": "==", "value": 2}]})
            assert resp.status == 200
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_unknown_parameter_is_rejected_at_load_too(self, tmp_server):
        client = await _client(tmp_server)
        try:
            resp = await client.post("/api/procedure/load", json={
                "name": "Bad", "steps": [
                    {"type": "tlm_check", "parameter": "payload.no_such_thing",
                     "condition": "==", "value": 1}]})
            assert resp.status == 400
            assert "payload.no_such_thing" in " ".join(
                (await resp.json())["problems"])
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_vocabulary_endpoint_lists_resolvable_paths(self, tmp_server):
        client = await _client(tmp_server)
        try:
            body = await (await client.get(
                "/api/procedure/tlm-vocabulary")).json()
            assert body["source"] == "live_state"
            assert "payload.mode" in body["parameters"]
            assert "eps.soc_pct" in body["parameters"]
            assert "ttc.pa_on" not in body["parameters"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_no_telemetry_means_no_parameter_validation(self, tmp_path):
        """Honest degradation: with no state there is nothing to validate
        against, so the save is allowed and the endpoint says so."""
        (tmp_path / "procedures").mkdir(parents=True, exist_ok=True)
        server = _server(tmp_path)
        server._latest_state = {}
        client = await _client(server)
        try:
            vocab = await (await client.get(
                "/api/procedure/tlm-vocabulary")).json()
            assert vocab["source"] == "none"
            resp = await client.post("/api/procedure/save", json={
                "name": "Offline", "steps": [
                    {"type": "tlm_check", "parameter": "ttc.pa_on",
                     "condition": "==", "value": 1}]})
            assert resp.status == 200
        finally:
            await client.close()

    def test_a_missing_parameter_name_is_always_a_problem(self):
        problems = validate_steps(
            [{"type": "tlm_check", "parameter": "", "value": 1}], None)
        assert problems and "parameter" in problems[0]

    def test_suggestion_is_offered_for_a_near_miss(self):
        problems = validate_steps(
            [{"type": "tlm_check", "parameter": "payload.mde", "value": 2}],
            {"payload.mode", "eps.soc_pct"})
        assert "did you mean 'payload.mode'" in problems[0]


# ── 4. Canonical schema + adapters ──────────────────────────────────

class TestCanonicalSchemaAndAdapters:

    def test_untagged_activity_dialect_converts(self):
        steps = from_activity_sequence([
            {"service": 8, "subtype": 1, "func_id": "0x1A", "params": [2],
             "description": "PAYLOAD_SET_MODE_IMAGING"},
            {"wait_s": 5},
            {"wait_for": {"parameter": "payload.mode", "value": 2,
                          "timeout_s": 30}},
        ])
        assert [s["type"] for s in steps] == ["command", "wait", "tlm_check"]
        assert steps[0]["func_id"] == 0x1A and steps[0]["params"] == [2]
        assert steps[1]["seconds"] == 5.0
        assert steps[2]["parameter"] == "payload.mode"
        assert steps[2]["condition"] == "=="

    def test_builder_dialect_is_normalised_not_mangled(self):
        steps = from_builder_steps(BUILDER_STEPS)
        assert [s["type"] for s in steps] == [
            "command", "wait", "tlm_check", "go_nogo"]
        assert steps[0]["data_hex"] == "1a02"
        assert steps[3]["label"] == "Imaging configuration verified?"

    def test_conversion_is_idempotent(self):
        once = to_canonical(BUILDER_STEPS)
        assert to_canonical(once) == once

    def test_command_argument_fields_survive_conversion(self):
        """S3 sid / S15 store_id / imaging lat-lon must not be dropped —
        the runner packs the data field from them."""
        steps = to_canonical([
            {"service": 3, "subtype": 27, "sid": 1},
            {"service": 15, "subtype": 9, "store_id": 3},
            {"service": 8, "subtype": 1, "func_id": 28,
             "parameters": {"target_lat": 51.5, "target_lon": -0.1}},
        ])
        assert steps[0]["sid"] == 1
        assert steps[1]["store_id"] == 3
        assert steps[2]["parameters"]["target_lat"] == 51.5

    def test_every_shipped_activity_sequence_is_valid_canonical(self):
        data = yaml.safe_load(
            (CONFIG_DIR / "planning" / "activity_types.yaml").read_text())
        for activity in data["activity_types"]:
            steps = from_activity_sequence(activity.get("command_sequence", []))
            problems = validate_steps(steps, None)
            assert problems == [], f"{activity['name']}: {problems}"
            assert all(s["type"] != "note" for s in steps), (
                f"{activity['name']} has a step the adapter could not classify")

    def test_markdown_adapter_extracts_command_steps(self):
        text = (
            "# Clock sync\n"
            "**TC:** `HK_REQUEST` (Service 3, Subtype 25)\n"
            "Some prose that must not become a step.\n"
            "**TC:** `SET_TIME` (Service 9, Subtype 1)\n"
        )
        steps = from_markdown(text)
        assert [(s["service"], s["subtype"]) for s in steps] == [(3, 25), (9, 1)]
        assert all(s["type"] == "command" for s in steps)
        assert steps[1]["mnemonic"] == "SET_TIME"

    def test_unrecognised_step_becomes_a_visible_note_not_a_silent_pass(self):
        steps = to_canonical([{"totally": "unknown"}])
        assert steps[0]["type"] == "note"
        assert "Unrecognised" in steps[0]["description"]

    def test_invalid_command_is_reported(self):
        problems = validate_steps([{"type": "command", "service": 8}], None)
        assert problems and "service and subtype" in problems[0]

    def test_bad_hex_is_reported(self):
        problems = validate_steps(
            [{"type": "command", "service": 8, "subtype": 1,
              "data_hex": "zzz"}], None)
        assert problems and "not hex" in problems[0]


# ── 5. procedure_index links resolve, and the docs are servable ─────

class TestProcedureIndexLinks:

    def test_shipped_runnable_links_resolve_to_real_steps(self, real_server):
        # `kind: advisory` (2026-08b) declares a card that deliberately
        # commands NOTHING — CTG-020, the ground-station antenna failure, is
        # the only one today. It is the DECLARED exception that keeps
        # "extracts zero command steps" distinguishable from the defect in
        # which 21 documents hid their commanding behind `**Action:**` prose
        # and were silently inert, so it must not be asserted to have steps.
        linked = [e for e in real_server._procedure_index
                  if e.get("runnable")
                  and e["runnable"].get("kind") != "advisory"]
        assert len(linked) >= 4, "the exemplar links have gone missing"
        for entry in linked:
            resolved = real_server._indexed_procedure(entry["id"])
            assert resolved is not None, entry["id"]
            assert resolved["steps"], f"{entry['id']} resolved to zero steps"
            assert validate_steps(resolved["steps"], None) == []
            assert resolved["document"] == entry["file"]

    def test_every_runnable_link_names_a_real_target(self):
        index = yaml.safe_load(
            (CONFIG_DIR / "procedures" / "procedure_index.yaml").read_text())
        activity_names = {
            a["name"] for a in yaml.safe_load(
                (CONFIG_DIR / "planning" / "activity_types.yaml").read_text()
            )["activity_types"]}
        for entry in index["procedures"]:
            link = entry.get("runnable")
            if not link:
                continue
            assert link["kind"] in ("activity_type", "steps_file", "markdown",
                                    "advisory")
            if link["kind"] == "advisory":
                # An advisory card names no target by construction; what it
                # must carry is the reason it commands nothing.
                assert link.get("reason"), entry["id"]
                continue
            if link["kind"] == "activity_type":
                assert link["ref"] in activity_names, entry["id"]
            elif link["kind"] == "steps_file":
                assert (CONFIG_DIR / "procedures" / link["ref"]).exists()

    def test_activity_type_procedure_refs_resolve_to_index_ids(self):
        """Every procedure_ref names a real index entry. (Several point at a
        neighbouring id — recorded in docs/PROCEDURE_SCHEMA.md §7 as
        Flight-Ops drift rather than silently rewritten.)"""
        index_ids = {e["id"] for e in yaml.safe_load(
            (CONFIG_DIR / "procedures" / "procedure_index.yaml").read_text()
        )["procedures"]}
        for activity in yaml.safe_load(
                (CONFIG_DIR / "planning" / "activity_types.yaml").read_text()
        )["activity_types"]:
            ref = activity.get("procedure_ref")
            if ref:
                assert ref in index_ids, f"{activity['name']} -> {ref}"

    @pytest.mark.asyncio
    async def test_index_endpoint_reports_runnability(self, real_server):
        client = await _client(real_server)
        try:
            body = await (await client.get("/api/procedure/index")).json()
            assert body["schema_version"] == CANONICAL_SCHEMA_VERSION
            by_id = {p["id"]: p for p in body["procedures"]}
            assert by_id["NOM-002"]["runnable"] is True
            assert by_id["NOM-002"]["runnable_steps"] > 0
            assert by_id["NOM-001"]["runnable"] is False
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_linked_procedure_loads_and_carries_its_document(
            self, real_server):
        client = await _client(real_server)
        try:
            resp = await client.post("/api/procedure/load",
                                     json={"procedure_id": "NOM-002"})
            assert resp.status == 200, await resp.text()
            body = await resp.json()
            assert body["procedure_ref"] == "NOM-002"
            assert body["document"] == "nominal/imaging_session.md"
            assert body["total_steps"] > 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_read_only_procedure_is_refused_with_a_reason(
            self, real_server):
        client = await _client(real_server)
        try:
            resp = await client.post("/api/procedure/load",
                                     json={"procedure_id": "NOM-001"})
            assert resp.status == 404
            assert "runnable" in (await resp.json())["error"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_the_ops_manual_markdown_is_servable(self, real_server):
        """/api/procedures* had no call site in the UI at all; the Procedures
        tab now reads the document behind the procedure being executed."""
        client = await _client(real_server)
        try:
            listing = await (await client.get("/api/procedures")).json()
            assert len(listing["procedures"]) >= 60
            doc = await (await client.get(
                "/api/procedures/nominal/imaging_session.md")).json()
            assert doc["content_md"].strip()
            assert "**TC:**" in doc["content_md"]
        finally:
            await client.close()

    def test_markdown_adapter_works_on_the_linked_document(self, real_server):
        resolved = real_server._indexed_procedure("NOM-008")
        assert resolved is not None
        assert resolved["source"].startswith("markdown:")
        # The adapter used to emit command steps only. The 2026-08b procedure
        # work introduced a real branching step format, so a card can now also
        # carry wait / tlm_check / go_nogo steps — each of which the executor
        # understands (`ProcedureRunner._execute_step`). What must stay true is
        # that no step carries a type the executor would silently skip.
        # `label` and `branch` are the branching format's DECLARATIVE steps: a
        # label is a jump target and carries no action. They are listed here
        # because they are legal in a card, not because the executor runs
        # them — see docs/plan_2026-08b/MCS_REQUESTS.md §9 for the open
        # question of what `ProcedureRunner` should do with a branch.
        executable = {"command", "wait", "tlm_check", "go_nogo"}
        declarative = {"label", "branch"}
        unknown = sorted({s["type"] for s in resolved["steps"]}
                         - executable - declarative)
        assert not unknown, f"steps of an unknown type: {unknown}"
        assert any(s["type"] == "command" for s in resolved["steps"]), \
            "no **TC:** annotations were extracted"
        assert resolved["steps"], "no **TC:** annotations were extracted"


# ── 6. The UI actually calls all of it ──────────────────────────────

INDEX_HTML = (REPO_ROOT / "packages" / "smo-mcs" / "src" / "smo_mcs"
              / "static" / "index.html").read_text()


class TestProceduresTabWiring:
    """The endpoints existed; the buttons did not. `grep 'api/procedures'` in
    static/ used to return nothing at all."""

    @pytest.mark.parametrize("endpoint", [
        "/api/procedure/index",
        "/api/procedure/custom",
        "/api/procedure/tlm-vocabulary",
        "/api/procedures/",
    ])
    def test_endpoint_has_a_call_site(self, endpoint):
        assert endpoint in INDEX_HTML, f"{endpoint} is still a dead endpoint"

    @pytest.mark.parametrize("element", [
        "proc-source-select",     # activity / ops procedure / saved
        "proc-doc-view",          # the operations-manual text
        "pb-cmd-builder",         # the shared command-builder instance
        "pb-tlm-params",          # tlm_check vocabulary datalist
        "pb-btn-run",             # run the builder's steps
    ])
    def test_element_is_present(self, element):
        assert element in INDEX_HTML

    def test_builder_reuses_the_command_builder_component(self):
        """Hand-off from the MCS agent: no third command-packing copy."""
        assert "renderCommandBuilder(host, {" in INDEX_HTML
        assert "prefix: 'pbcb'" in INDEX_HTML

    def test_load_can_be_posted_by_path_and_by_procedure_id(self):
        assert "body.procedure_id = value" in INDEX_HTML
        assert "body.path = value" in INDEX_HTML

    def test_save_problems_are_shown_to_the_operator(self):
        assert "data.problems" in INDEX_HTML
