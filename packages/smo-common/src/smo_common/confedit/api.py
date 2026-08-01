"""HTTP API + browser page for the config editor.

Full CRUD is mounted ONLY by the simulator's instructor server (port 8080 —
the instructor's door per docs/DEPLOYMENT.md). The MCS and planner mount
the same function with ``readonly=True``: students can browse files,
classification, history, diffs and cross-checks — a teaching feature — but
every mutating route is **absent** (404), not merely disabled in the UI.

Routes (``prefix`` = ``/api/config``)::

    GET  <prefix>/files                     inventory + classification
    GET  <prefix>/file?path=…               {text, form_spec, unknown_keys…}
    GET  <prefix>/schema?path=…             the JSON form spec alone
    POST <prefix>/validate {path, text}     -> {issues[]}
    POST <prefix>/diff     {path, text}     -> {text_diff, semantic_diff,
                                                restart_scope}
    GET  <prefix>/history?path=&limit=      revisions, newest first
    GET  <prefix>/revisions/{rev}?path=     file content at a revision
    GET  <prefix>/crosschecks               run the registry standalone
    GET  <prefix>/baselines                 named baselines
    -- mutating (never registered when readonly) --
    PUT  <prefix>/file?path=  {text, message, reason?, actor?, force?}
    POST <prefix>/revert      {rev, path, reason?, actor?}
    POST <prefix>/baselines   {name, description?}
    POST <prefix>/baselines/{name}/apply
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .editor import ConfigEditError, ConfigEditor

logger = logging.getLogger(__name__)

__all__ = ["register_config_routes"]


def register_config_routes(
    app: Any,
    editor_or_config_dir: Union[ConfigEditor, str, Path],
    *,
    readonly: bool = False,
    prefix: str = "/api/config",
    page_path: Optional[str] = "/config",
    owner: str = "",
    scenario_active: Optional[Callable[[], bool]] = None,
) -> Any:
    """Mount the config API (and optional browser page) on ``app``.

    ``scenario_active`` — optional callable; while it returns True, apply
    and revert are refused unless the request sets ``force`` (brief 06 §B
    safety rule 3).
    """
    from aiohttp import web

    if isinstance(editor_or_config_dir, ConfigEditor):
        editor = editor_or_config_dir
    else:
        editor = ConfigEditor(editor_or_config_dir)

    def _err(e: ConfigEditError):
        return web.json_response(
            {"error": str(e), "issues": e.issues}, status=e.status)

    # ── read routes ─────────────────────────────────────────────────

    async def handle_files(_req):
        out = editor.list_files()
        out["readonly"] = readonly
        out["owner"] = owner
        return web.json_response(out)

    async def handle_file(req):
        try:
            out = editor.get_file(req.query.get("path", ""))
        except ConfigEditError as e:
            return _err(e)
        out["readonly"] = readonly or not out["editable"]
        return web.json_response(out)

    async def handle_schema(req):
        from .formgen import form_spec_for
        path = req.query.get("path", "")
        return web.json_response(
            {"path": path, "form_spec": form_spec_for(path)})

    async def handle_validate(req):
        try:
            body = await req.json()
            issues = editor.validate(body.get("path", ""),
                                     body.get("text", ""))
        except ConfigEditError as e:
            return _err(e)
        except ValueError:
            return web.json_response({"error": "invalid JSON body"},
                                     status=400)
        return web.json_response({
            "issues": issues,
            "ok": not any(i["severity"] == "error" for i in issues)})

    async def handle_diff(req):
        try:
            body = await req.json()
            out = editor.diff(body.get("path", ""), body.get("text", ""))
        except ConfigEditError as e:
            return _err(e)
        except ValueError:
            return web.json_response({"error": "invalid JSON body"},
                                     status=400)
        return web.json_response(out)

    async def handle_history(req):
        try:
            limit = min(int(req.query.get("limit", 50)), 500)
        except ValueError:
            limit = 50
        try:
            revs = editor.history(req.query.get("path") or None, limit)
        except ConfigEditError as e:
            return _err(e)
        return web.json_response({"backend": editor.backend.kind,
                                  "revisions": revs})

    async def handle_revision(req):
        rev = req.match_info.get("rev", "")
        path = req.query.get("path", "")
        try:
            content = editor.file_at(rev, path)
        except ConfigEditError as e:
            return _err(e)
        if content is None:
            return web.json_response(
                {"error": f"{path} not present at {rev}"}, status=404)
        return web.json_response({"rev": rev, "path": path, "text": content})

    async def handle_revdiff(req):
        rev_a = req.match_info.get("a", "")
        rev_b = req.match_info.get("b", "")
        try:
            text = editor.diff_revisions(rev_a, rev_b,
                                         req.query.get("path") or None)
        except ConfigEditError as e:
            return _err(e)
        return web.json_response({"a": rev_a, "b": rev_b, "diff": text})

    async def handle_crosschecks(_req):
        return web.json_response({"issues": editor.crosschecks()})

    async def handle_baselines(_req):
        return web.json_response({"baselines": editor.list_baselines(),
                                  "backend": editor.backend.kind})

    # ── mutating routes ─────────────────────────────────────────────

    def _scenario_guard(body: dict):
        if scenario_active is not None and scenario_active() \
                and not body.get("force"):
            raise ConfigEditError(
                "a training scenario is active — config changes are "
                "refused (repeat with force=true and a reason to override)",
                409)

    async def handle_put_file(req):
        try:
            body = await req.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"},
                                     status=400)
        path = req.query.get("path") or body.get("path", "")
        try:
            _scenario_guard(body)
            out = editor.apply(
                path, body.get("text", ""),
                actor=str(body.get("actor", "instructor")),
                role=str(body.get("role", "instructor")),
                message=str(body.get("message", "")),
                reason=str(body.get("reason", "")),
                force=bool(body.get("force", False)))
        except ConfigEditError as e:
            return _err(e)
        return web.json_response(out)

    async def handle_revert(req):
        try:
            body = await req.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"},
                                     status=400)
        try:
            _scenario_guard(body)
            out = editor.revert(
                str(body.get("rev", "")), str(body.get("path", "")),
                actor=str(body.get("actor", "instructor")),
                reason=str(body.get("reason", "")))
        except ConfigEditError as e:
            return _err(e)
        return web.json_response(out)

    async def handle_baseline_create(req):
        try:
            body = await req.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"},
                                     status=400)
        try:
            out = editor.create_baseline(
                str(body.get("name", "")),
                str(body.get("description", "")),
                actor=str(body.get("actor", "instructor")))
        except ConfigEditError as e:
            return _err(e)
        return web.json_response(out)

    async def handle_baseline_apply(req):
        name = req.match_info.get("name", "")
        try:
            body = await req.json()
        except ValueError:
            body = {}
        try:
            _scenario_guard(body)
            out = editor.apply_baseline(
                name, actor=str(body.get("actor", "instructor")),
                reason=str(body.get("reason", "")))
        except ConfigEditError as e:
            return _err(e)
        return web.json_response(out)

    async def handle_page(_req):
        page = (_PAGE_TEMPLATE
                .replace("__API__", json.dumps(prefix))
                .replace("__READONLY__", json.dumps(readonly))
                .replace("__OWNER__", json.dumps(owner or "config")))
        return web.Response(text=page, content_type="text/html")

    prefix = prefix.rstrip("/")
    app.router.add_get(prefix + "/files", handle_files)
    app.router.add_get(prefix + "/file", handle_file)
    app.router.add_get(prefix + "/schema", handle_schema)
    app.router.add_post(prefix + "/validate", handle_validate)
    app.router.add_post(prefix + "/diff", handle_diff)
    app.router.add_get(prefix + "/history", handle_history)
    app.router.add_get(prefix + "/revisions/{rev}", handle_revision)
    app.router.add_get(prefix + "/diff/{a}..{b}", handle_revdiff)
    app.router.add_get(prefix + "/crosschecks", handle_crosschecks)
    app.router.add_get(prefix + "/baselines", handle_baselines)
    if not readonly:
        # Students are blocked by construction: on readonly hosts these
        # routes DO NOT EXIST (404), they are not merely disabled.
        app.router.add_put(prefix + "/file", handle_put_file)
        app.router.add_post(prefix + "/revert", handle_revert)
        app.router.add_post(prefix + "/baselines", handle_baseline_create)
        app.router.add_post(prefix + "/baselines/{name}/apply",
                            handle_baseline_apply)
    if page_path:
        app.router.add_get(page_path, handle_page)
    return app


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Mission Configuration</title>
<style>
:root { --bg:#0a1226; --panel:#0f1d33; --border:#1f3050; --text:#cfe;
        --dim:#7a90ad; --accent:#5fb7ff; --warn:#f59e0b; --err:#ff7080;
        --ok:#69d18a; }
* { box-sizing:border-box; }
html,body { background:var(--bg); color:var(--text); margin:0; height:100%;
            font-family:'JetBrains Mono',Consolas,monospace; font-size:12px; }
header { padding:8px 14px; background:linear-gradient(180deg,#0f1d33,#0b1525);
         border-bottom:1px solid var(--border); display:flex; gap:14px;
         align-items:center; }
h1 { font-size:13px; letter-spacing:2px; color:var(--accent); margin:0; }
.badge { background:#5a2020; color:#ffc9c9; border-radius:3px; padding:2px 8px;
         font-size:10px; letter-spacing:1px; }
#layout { display:grid; grid-template-columns: 290px 1fr 320px;
          height: calc(100vh - 40px); }
#tree { border-right:1px solid var(--border); overflow-y:auto; padding:6px; }
#tree h3 { font-size:10px; color:var(--dim); letter-spacing:1.5px;
           margin:10px 4px 3px; text-transform:uppercase; }
.file { padding:2px 6px; cursor:pointer; border-radius:3px;
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.file:hover { background:#15294a; }
.file.active { background:#1a3a6a; }
.file.locked { color:#5b6b80; text-decoration:line-through; cursor:not-allowed; }
.file .rs { color:var(--dim); font-size:9px; }
#center { display:flex; flex-direction:column; overflow:hidden; }
#edhead { padding:6px 10px; border-bottom:1px solid var(--border);
          display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
#path { color:var(--accent); }
#note { color:var(--dim); font-size:11px; width:100%; }
textarea { flex:1; background:#0b1424; color:#dce8f5; border:none;
           padding:10px; font:inherit; font-size:12px; resize:none;
           outline:none; white-space:pre; }
button { background:#1a3a6a; color:var(--text); border:1px solid #2a5a8a;
         border-radius:3px; padding:4px 10px; cursor:pointer; font:inherit;
         font-size:11px; }
button:hover { background:#2a5a8a; }
button:disabled { opacity:.4; cursor:not-allowed; }
input { background:#0d1830; color:var(--text); border:1px solid #2a4a7a;
        border-radius:3px; padding:3px 6px; font:inherit; }
#rail { border-left:1px solid var(--border); overflow-y:auto; padding:8px; }
#rail h3 { font-size:10px; color:var(--dim); letter-spacing:1.5px;
           margin:12px 0 4px; text-transform:uppercase; }
.issue { padding:3px 6px; margin:2px 0; border-left:3px solid var(--dim);
         font-size:11px; }
.issue.error { border-color:var(--err); color:#ffc9c9; }
.issue.warning { border-color:var(--warn); color:#ffe1a8; }
.rev { padding:3px 6px; margin:2px 0; border-left:3px solid #2a4a7a;
       font-size:11px; }
.rev .id { color:var(--accent); cursor:pointer; }
.rev .tr { color:var(--dim); }
pre { background:#0b1424; border:1px solid var(--border); border-radius:4px;
      padding:8px; overflow:auto; max-height:260px; font-size:11px; }
.diff-add { color:var(--ok); } .diff-del { color:var(--err); }
#formview { overflow-y:auto; padding:10px; display:none; flex:1; }
.ffield { margin:3px 0; padding:2px 6px; }
.ffield .fname { color:var(--accent); }
.ffield .fdesc { color:var(--dim); font-size:10.5px; }
.ffield .frange { color:var(--warn); font-size:10.5px; }
.ignored { background:#4a1520; border-left:3px solid var(--err);
           padding:4px 8px; margin:4px 0; color:#ffc9c9; }
</style></head><body>
<header>
  <h1>MISSION CONFIGURATION</h1>
  <span id="owner" style="color:var(--dim)"></span>
  <span class="badge" id="ro-badge" style="display:none">READ-ONLY —
    configuration is edited from the instructor console (port 8080)</span>
</header>
<div id="layout">
  <div id="tree"></div>
  <div id="center">
    <div id="edhead">
      <span id="path">select a file</span>
      <button id="tab-yaml">YAML</button>
      <button id="tab-form">Schema form</button>
      <span style="flex:1"></span>
      <button id="btn-validate">Validate</button>
      <button id="btn-diff">Diff</button>
      <input id="msg" placeholder="commit message" size="24">
      <input id="reason" placeholder="reason (audit)" size="18">
      <button id="btn-apply">Apply</button>
      <span id="note"></span>
    </div>
    <textarea id="editor" spellcheck="false"></textarea>
    <div id="formview"></div>
  </div>
  <div id="rail">
    <h3>Validation</h3><div id="issues">–</div>
    <h3>Diff preview</h3><pre id="diff">–</pre>
    <h3>History <span id="backend" style="color:var(--dim)"></span></h3>
    <div id="history">–</div>
    <h3>Baselines</h3>
    <div id="baselines">–</div>
    <div id="bl-create" style="margin-top:4px">
      <input id="bl-name" placeholder="baseline name" size="14">
      <button id="btn-baseline">Create</button>
    </div>
    <h3>Cross-checks</h3><div id="xchecks">–</div>
  </div>
</div>
<script>
const API = __API__;
const READONLY = __READONLY__;
const $ = id => document.getElementById(id);
let CURRENT = null, FILES = [], GROUPS = [];

function esc(s){ const d=document.createElement('div');
                 d.textContent=String(s); return d.innerHTML; }

async function loadTree(){
  const d = await (await fetch(API + "/files")).json();
  FILES = d.files; GROUPS = d.groups;
  $("owner").textContent = d.owner + " — " + d.config_dir +
      " · history: " + d.backend;
  $("backend").textContent = "(" + d.backend + ")";
  if (READONLY || d.readonly) $("ro-badge").style.display = "inline";
  const tree = $("tree"); tree.innerHTML = "";
  for (const g of GROUPS){
    const files = FILES.filter(f => f.group === g.key);
    if (!files.length) continue;
    tree.insertAdjacentHTML("beforeend", `<h3>${esc(g.label)}</h3>`);
    for (const f of files){
      const cls = "file" + (f.editable ? "" : " locked");
      const rs = f.restart_scope.length
        ? ` <span class="rs">↻${f.restart_scope.join(",")}</span>` : "";
      tree.insertAdjacentHTML("beforeend",
        `<div class="${cls}" data-path="${esc(f.path)}"
              title="${esc(f.note || f.label)}">${esc(f.path)}${rs}</div>`);
    }
  }
  for (const el of tree.querySelectorAll(".file"))
    el.addEventListener("click", () => openFile(el.dataset.path, el));
}

async function openFile(path, el){
  const d = await (await fetch(API + "/file?path=" +
                               encodeURIComponent(path))).json();
  if (d.error){ $("note").textContent = d.error; return; }
  CURRENT = d;
  for (const e of document.querySelectorAll(".file.active"))
    e.classList.remove("active");
  if (el) el.classList.add("active");
  $("path").textContent = d.path +
      (d.interface_defining ? "  ⚠ interface-defining" : "");
  $("note").textContent = (d.restart_scope.length
      ? "restart required: " + d.restart_scope.join(", ") + ". " : "") +
      (d.note || "");
  $("editor").value = d.text;
  $("editor").readOnly = READONLY || !d.editable;
  renderForm(d);
  renderUnknown(d);
  $("issues").textContent = "–"; $("diff").textContent = "–";
  loadHistory(path);
}

function renderUnknown(d){
  if (!(d.unknown_keys||[]).length) return;
  $("issues").innerHTML = d.unknown_keys.map(u =>
    `<div class="ignored">IGNORED BY LOADER — ${esc(u.path)}:
     ${esc(u.msg)}</div>`).join("");
}

function renderForm(d){
  const fv = $("formview");
  if (!d.form_spec){
    fv.innerHTML = "<div class='fdesc' style='color:var(--dim)'>No schema " +
      "for this file — use the YAML tab.</div>";
    return;
  }
  const fields = d.form_spec.fields || d.form_spec.item_fields || [];
  const head = d.form_spec.kind === "list_file"
    ? `<div class="fdesc" style="color:var(--dim)">List file — each entry
       under <b>${esc(d.form_spec.list_key)}</b> follows the
       ${esc(d.form_spec.model)} schema below. Edit in the YAML tab.</div>`
    : `<div class="fdesc" style="color:var(--dim)">Schema
       ${esc(d.form_spec.model)} — field reference (types, ranges and the
       loader's documentation). Edit values in the YAML tab; anything not
       listed here is IGNORED by the loader.</div>`;
  const render = (fs, indent) => fs.map(f => {
    const rng = (f.min !== undefined || f.max !== undefined)
      ? `<span class="frange"> range ${f.min ?? "-∞"}…${f.max ?? "∞"}</span>` : "";
    const dv = f.default !== undefined
      ? ` <span class="tr">default ${esc(JSON.stringify(f.default))}</span>` : "";
    const sub = f.fields ? render(f.fields, indent + 14)
      : (f.items && f.items.fields ? render(f.items.fields, indent + 14) : "");
    return `<div class="ffield" style="margin-left:${indent}px">
      <span class="fname">${esc(f.name)}</span>
      <span class="tr">${esc(f.type)}${f.nullable ? "?" : ""}</span>${rng}${dv}
      ${f.description ? `<div class="fdesc">${esc(f.description)}</div>` : ""}
      </div>` + sub;
  }).join("");
  fv.innerHTML = head + render(fields, 0);
}

$("tab-yaml").addEventListener("click", () => {
  $("editor").style.display = "block"; $("formview").style.display = "none";
});
$("tab-form").addEventListener("click", () => {
  $("editor").style.display = "none"; $("formview").style.display = "block";
});

function showIssues(issues){
  if (!issues.length){
    $("issues").innerHTML = "<div class='issue' style='color:var(--ok)'>" +
      "clean — no schema, plausibility or cross-file issues</div>";
    return;
  }
  $("issues").innerHTML = issues.map(i =>
    `<div class="issue ${i.severity}">[${i.severity}]
     ${esc(i.check || i.gate)} ${esc(i.path)} — ${esc(i.msg)}
     ${(i.files||[]).length ? "<br>files: " + esc(i.files.join(", ")) : ""}
     </div>`).join("");
}

$("btn-validate").addEventListener("click", async () => {
  if (!CURRENT) return;
  const d = await (await fetch(API + "/validate", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({path: CURRENT.path,
                          text: $("editor").value})})).json();
  showIssues(d.issues || []);
});

$("btn-diff").addEventListener("click", async () => {
  if (!CURRENT) return;
  const d = await (await fetch(API + "/diff", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({path: CURRENT.path,
                          text: $("editor").value})})).json();
  const lines = (d.text_diff || "(no changes)").split("\n").map(l =>
    l.startsWith("+") ? `<span class="diff-add">${esc(l)}</span>` :
    l.startsWith("-") ? `<span class="diff-del">${esc(l)}</span>` : esc(l));
  let sem = "";
  if (d.semantic_diff && !d.semantic_diff.length && d.text_diff)
    sem = "\n(semantic: comment/formatting only — changes nothing at runtime)";
  else if (d.semantic_diff && d.semantic_diff.length)
    sem = "\nsemantic: " + d.semantic_diff.map(s =>
      `${s.path} ${s.change}` +
      (s.change === "modified" ? ` ${JSON.stringify(s.before)}→` +
       JSON.stringify(s.after) : "")).join("; ");
  $("diff").innerHTML = lines.join("\n") + esc(sem) +
    (d.restart_scope && d.restart_scope.length
      ? esc("\nrestart required: " + d.restart_scope.join(", ")) : "");
});

$("btn-apply").addEventListener("click", async () => {
  if (!CURRENT || READONLY) return;
  if (!$("msg").value){ $("note").textContent =
    "a commit message is required"; return; }
  const r = await fetch(API + "/file?path=" +
      encodeURIComponent(CURRENT.path), {method:"PUT",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({text: $("editor").value, message: $("msg").value,
                          reason: $("reason").value})});
  const d = await r.json();
  if (!r.ok){
    $("note").textContent = d.error || "apply failed";
    showIssues(d.issues || []); return;
  }
  $("note").textContent = "applied as revision " + d.revision.slice(0,12) +
    (d.restart_required.length
      ? " — restart required: " + d.restart_required.join(", ")
      : "");
  showIssues(d.issues || []);
  loadHistory(CURRENT.path);
});

async function loadHistory(path){
  const d = await (await fetch(API + "/history?path=" +
      encodeURIComponent(path) + "&limit=10")).json();
  const revs = d.revisions || [];
  if (!revs.length){ $("history").textContent = "no revisions yet"; return; }
  $("history").innerHTML = revs.map(r => `<div class="rev">
    <span class="id" data-rev="${esc(r.rev)}">${esc(r.rev.slice(0,10))}</span>
    ${esc(r.ts || "")}<br>${esc(r.message)}<br>
    <span class="tr">${esc(Object.entries(r.trailers || {})
      .map(([k,v]) => k + "=" + v).join(" "))}</span>
    ${READONLY ? "" :
      `<button data-rev="${esc(r.rev)}" class="btn-revert">revert to</button>`}
    </div>`).join("");
  for (const b of document.querySelectorAll(".btn-revert"))
    b.addEventListener("click", async () => {
      const r = await fetch(API + "/revert", {method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({rev: b.dataset.rev, path: CURRENT.path,
                              reason: $("reason").value ||
                                      "instructor revert"})});
      const dd = await r.json();
      $("note").textContent = r.ok
        ? "reverted — new revision " + dd.revision.slice(0,12)
        : (dd.error || "revert failed");
      if (r.ok) openFile(CURRENT.path);
    });
  for (const s of document.querySelectorAll(".rev .id"))
    s.addEventListener("click", async () => {
      const dd = await (await fetch(API + "/revisions/" + s.dataset.rev +
        "?path=" + encodeURIComponent(CURRENT.path))).json();
      $("diff").textContent = dd.text || dd.error || "";
    });
}

async function loadBaselines(){
  const d = await (await fetch(API + "/baselines")).json();
  const bl = d.baselines || [];
  $("baselines").innerHTML = bl.length ? bl.map(b => `<div class="rev">
      <b>${esc(b.name)}</b> ${esc(b.description || "")}
      <span class="tr">${esc((b.revision||"").slice(0,10))}</span>
      ${READONLY ? "" :
        `<button class="btn-bl-apply" data-name="${esc(b.name)}">apply</button>`}
      </div>`).join("")
    : "<span style='color:var(--dim)'>none</span>";
  for (const b of document.querySelectorAll(".btn-bl-apply"))
    b.addEventListener("click", async () => {
      const r = await fetch(API + "/baselines/" +
        encodeURIComponent(b.dataset.name) + "/apply", {method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({reason: "apply baseline"})});
      const dd = await r.json();
      $("note").textContent = r.ok
        ? "baseline applied — revision " + dd.revision.slice(0,12) +
          " — restart required: " + dd.restart_required.join(", ")
        : (dd.error || "failed");
      if (CURRENT) openFile(CURRENT.path);
    });
}

if (!READONLY) $("btn-baseline").addEventListener("click", async () => {
  const name = $("bl-name").value.trim();
  if (!name) return;
  const r = await fetch(API + "/baselines", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({name})});
  const d = await r.json();
  $("note").textContent = r.ok ? "baseline " + name + " created"
                               : (d.error || "failed");
  loadBaselines();
});
else if (READONLY) $("bl-create").style.display = "none";

async function loadCrosschecks(){
  const d = await (await fetch(API + "/crosschecks")).json();
  const issues = d.issues || [];
  $("xchecks").innerHTML = issues.length
    ? issues.map(i => `<div class="issue ${i.severity}">
        [${esc(i.check)}] ${esc(i.msg)}</div>`).join("")
    : "<div class='issue' style='color:var(--ok)'>all cross-checks clean</div>";
}

if (READONLY){
  $("btn-apply").disabled = true;
  $("msg").disabled = true; $("reason").disabled = true;
}
loadTree(); loadBaselines(); loadCrosschecks();
</script>
</body></html>
"""
