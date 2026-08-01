"""HTTP API + browser page for the report generator.

Mounted by the MCS (students' port 9090), the simulator instructor server
and the standalone delayed-TM viewer — one implementation, several hosts,
exactly the pattern ``dump_store.register_dump_routes`` established.

Routes (``prefix`` defaults to ``/api/reports``)::

    GET  <prefix>                    list generated reports (newest first)
    GET  <prefix>/templates          shipped template metadata
    GET  <prefix>/templates/{id}     raw template YAML
    POST <prefix>/validate           {spec_yaml} -> {ok, errors[]}
    POST <prefix>/generate           {template, period?, dumps?, vars?}
    GET  <prefix>/file?name=…        download a generated report
    GET  <page_path>                 self-contained generator/browser page

Report generation is CPU-bound (matplotlib), so ``generate`` runs in the
default thread executor — the host's event loop (and the MCS websocket)
keeps serving while a report renders.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .generate import (
    DEFAULT_REPORTS_DIR,
    generate_report,
    list_templates,
    templates_dir_for,
)
from .spec import SpecError, load_spec, validate_spec
from .catalogs import Catalogs

logger = logging.getLogger(__name__)

__all__ = ["register_report_routes"]

_SAFE_REPORT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(html|json)$")


def _resolve_report_file(reports_dir: Path, name: str) -> Path:
    """Same trust boundary as ``dump_store.resolve_dump_file``."""
    if (not isinstance(name, str) or not name or "\x00" in name
            or "/" in name or "\\" in name or Path(name).name != name
            or not _SAFE_REPORT_RE.match(name)):
        raise ValueError(f"not a report filename: {name!r}")
    candidate = (reports_dir / name)
    try:
        if reports_dir.resolve() not in candidate.resolve().parents:
            raise ValueError(f"report filename escapes the store: {name!r}")
    except OSError as e:
        raise ValueError(str(e)) from e
    if not candidate.is_file():
        raise FileNotFoundError(name)
    return candidate


def register_report_routes(
    app: Any,
    get_dump_dir: Callable[[], Path] | Path | str,
    config_dir: Path | str,
    *,
    reports_dir: Path | str | None = None,
    prefix: str = "/api/reports",
    page_path: str | None = "/reports",
    owner: str = "",
) -> Any:
    """Mount the report API (and optional browser page) on ``app``."""
    from aiohttp import web

    config_dir = Path(config_dir)
    reports_dir = Path(reports_dir or DEFAULT_REPORTS_DIR)

    def _dump_dir() -> Path:
        return Path(get_dump_dir() if callable(get_dump_dir) else get_dump_dir)

    async def handle_list(_req):
        out = []
        if reports_dir.is_dir():
            for p in sorted(reports_dir.glob("*.html"),
                            key=lambda p: p.stat().st_mtime, reverse=True):
                meta = {}
                side = p.with_suffix(".json")
                if side.is_file():
                    try:
                        meta = json.loads(side.read_text())
                    except (OSError, ValueError):
                        meta = {}
                out.append({
                    "filename": p.name,
                    "size_bytes": p.stat().st_size,
                    "mtime": datetime.fromtimestamp(
                        p.stat().st_mtime, tz=timezone.utc).isoformat(
                            timespec="seconds"),
                    "title": meta.get("title"),
                    "report_id": meta.get("report_id"),
                    "period": meta.get("period"),
                    "dumps_used": meta.get("dumps_used"),
                })
        return web.json_response({
            "owner": owner, "store": str(reports_dir),
            "count": len(out), "reports": out,
        })

    async def handle_templates(_req):
        return web.json_response({"templates": list_templates(config_dir)})

    async def handle_template_yaml(req):
        tid = req.match_info.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", tid or ""):
            return web.json_response({"error": "bad template id"}, status=400)
        path = templates_dir_for(config_dir) / f"{tid}.yaml"
        if not path.is_file():
            return web.json_response({"error": f"no template {tid}"}, status=404)
        return web.Response(text=path.read_text(), content_type="text/yaml")

    async def handle_validate(req):
        try:
            body = await req.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        text = body.get("spec_yaml") or body.get("spec") or ""
        try:
            spec = load_spec(text)
        except SpecError as e:
            return web.json_response({"ok": False, "errors": e.errors})
        errors = validate_spec(spec, Catalogs.load(config_dir))
        return web.json_response({"ok": not errors, "errors": errors})

    async def handle_generate(req):
        try:
            body = await req.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        template = body.get("template") or body.get("spec_yaml")
        if not template:
            return web.json_response(
                {"error": "missing 'template' (id) or 'spec_yaml'"}, status=400)
        period = body.get("period") or None
        dump_files = body.get("dumps") or None
        vars_ = body.get("vars") or {}
        loop = asyncio.get_running_loop()

        def _work():
            return generate_report(
                template, config_dir=config_dir, dump_dir=_dump_dir(),
                dump_files=dump_files, vars=vars_, period_override=period,
                out_dir=reports_dir)

        try:
            meta = await loop.run_in_executor(None, _work)
        except SpecError as e:
            return web.json_response({"error": "spec validation failed",
                                      "errors": e.errors}, status=422)
        except FileNotFoundError as e:
            return web.json_response({"error": str(e)}, status=404)
        except Exception as e:  # noqa: BLE001 — surface, don't 500-blank
            logger.exception("report generation failed")
            return web.json_response({"error": str(e)}, status=500)
        return web.json_response(meta)

    async def handle_file(req):
        name = (req.query.get("name") or "").strip()
        try:
            path = _resolve_report_file(reports_dir, name)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except FileNotFoundError:
            return web.json_response({"error": f"no such report: {name}"},
                                     status=404)
        ctype = "text/html" if path.suffix == ".html" else "application/json"
        return web.FileResponse(path, headers={"Content-Type": ctype})

    async def handle_page(_req):
        return web.Response(text=_PAGE_TEMPLATE.replace(
            "__API__", json.dumps(prefix)).replace(
            "__OWNER__", json.dumps(owner or "ground segment")),
            content_type="text/html")

    prefix = prefix.rstrip("/")
    app.router.add_get(prefix, handle_list)
    app.router.add_get(prefix + "/", handle_list)
    app.router.add_get(prefix + "/templates", handle_templates)
    app.router.add_get(prefix + "/templates/{id}", handle_template_yaml)
    app.router.add_post(prefix + "/validate", handle_validate)
    app.router.add_post(prefix + "/generate", handle_generate)
    app.router.add_get(prefix + "/file", handle_file)
    if page_path:
        app.router.add_get(page_path, handle_page)
    return app


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Delayed-TM Reports</title>
<style>
:root { --bg:#0a1226; --panel:#0f1d33; --border:#1f3050; --text:#cfe;
        --dim:#7a90ad; --accent:#5fb7ff; --warn:#f59e0b; }
* { box-sizing:border-box; }
html,body { background:var(--bg); color:var(--text); margin:0;
            font-family:'JetBrains Mono',Consolas,monospace; font-size:12px; }
header { padding:10px 16px; background:linear-gradient(180deg,#0f1d33,#0b1525);
         border-bottom:1px solid var(--border); display:flex; gap:16px;
         align-items:center; }
h1 { font-size:14px; letter-spacing:2px; color:var(--accent); margin:0; }
h2 { font-size:11px; color:var(--dim); letter-spacing:1.5px; margin:18px 0 6px; }
.sub { color:var(--dim); font-size:11px; }
main { padding:14px 16px; max-width:1100px; }
button, a.btn { background:#1a3a6a; color:var(--text); border:1px solid #2a5a8a;
                border-radius:3px; padding:4px 10px; cursor:pointer; font:inherit;
                font-size:11px; text-decoration:none; display:inline-block; }
button:hover, a.btn:hover { background:#2a5a8a; }
select, input { background:#0d1830; color:var(--text); border:1px solid #2a4a7a;
                border-radius:3px; padding:4px 6px; font:inherit; }
table { width:100%; border-collapse:collapse; margin-top:8px; }
th,td { border-bottom:1px solid #16223a; padding:5px 8px; text-align:left; }
th { color:var(--dim); font-weight:500; }
.row { display:flex; gap:10px; align-items:center; margin:6px 0; flex-wrap:wrap; }
.err { color:#ff8080; white-space:pre-wrap; }
.ok { color:#69d18a; }
#vars-row label { color:var(--dim); margin-right:4px; }
.empty { color:var(--warn); padding:10px 0; }
</style></head><body>
<header>
  <h1>DELAYED-TM REPORTS</h1>
  <span class="sub" id="sub"></span>
  <button id="refresh">Refresh</button>
</header>
<main>
  <h2>GENERATE</h2>
  <div class="row">
    <label for="tmpl">Template</label>
    <select id="tmpl"></select>
    <label for="pkind">Period</label>
    <select id="pkind">
      <option value="">template default</option>
      <option value="dump">all dumps (envelope)</option>
      <option value="pass">latest pass</option>
      <option value="orbit">latest orbit</option>
      <option value="day">latest UTC day</option>
      <option value="absolute">absolute…</option>
    </select>
    <span id="abs-row" style="display:none">
      <input id="p-start" size="20" placeholder="2026-03-10T04:00:00Z">
      →
      <input id="p-end" size="20" placeholder="2026-03-10T06:00:00Z">
    </span>
  </div>
  <div class="row" id="vars-row"></div>
  <div class="row">
    <button id="generate">Generate report</button>
    <span id="status"></span>
  </div>
  <div class="err" id="errors"></div>

  <h2>GENERATED REPORTS</h2>
  <div id="list"><span class="empty">loading…</span></div>
</main>
<script>
const API = __API__;
const OWNER = __OWNER__;
const $ = (id) => document.getElementById(id);
let TEMPLATES = [];

function fmtBytes(n){ return n<1048576 ? (n/1024).toFixed(1)+" kB"
                                       : (n/1048576).toFixed(2)+" MB"; }

async function loadTemplates(){
  const d = await (await fetch(API + "/templates")).json();
  TEMPLATES = d.templates || [];
  $("tmpl").innerHTML = TEMPLATES.map(t =>
    `<option value="${t.id}">${t.title || t.id}</option>`).join("");
  renderVars();
}

function renderVars(){
  const t = TEMPLATES.find(x => x.id === $("tmpl").value);
  const row = $("vars-row");
  row.innerHTML = "";
  if (!t || !t.vars) return;
  for (const [k, v] of Object.entries(t.vars)){
    row.insertAdjacentHTML("beforeend",
      `<label for="var-${k}">${k}</label>` +
      `<input id="var-${k}" data-var="${k}" value="${String(v)
         .replace(/"/g,"&quot;")}" size="18">`);
  }
}

async function refresh(){
  let d;
  try { d = await (await fetch(API)).json(); }
  catch(e){ $("sub").textContent = "cannot reach " + API; return; }
  $("sub").textContent = OWNER + " — " + d.count + " report(s)";
  if (!d.reports.length){
    $("list").innerHTML = '<span class="empty">No reports yet. Pick a ' +
      'template above and press Generate (needs delayed-TM dumps in the ' +
      'archive).</span>';
    return;
  }
  $("list").innerHTML = "<table><tr><th>REPORT</th><th>PERIOD</th>" +
    "<th>GENERATED (UTC)</th><th>SIZE</th><th></th></tr>" +
    d.reports.map(r => `<tr>
      <td>${r.title || r.filename}</td>
      <td>${r.period ? (r.period.start_utc + " → " + r.period.end_utc) : "--"}</td>
      <td>${r.mtime}</td><td>${fmtBytes(r.size_bytes)}</td>
      <td><a class="btn" target="_blank"
             href="${API}/file?name=${encodeURIComponent(r.filename)}">open</a>
          <a class="btn" download="${r.filename}"
             href="${API}/file?name=${encodeURIComponent(r.filename)}">download</a>
      </td></tr>`).join("") + "</table>";
}

async function generate(){
  $("status").textContent = "generating…"; $("errors").textContent = "";
  const body = { template: $("tmpl").value, vars: {} };
  for (const inp of document.querySelectorAll("#vars-row input"))
    body.vars[inp.dataset.var] = inp.value;
  const pk = $("pkind").value;
  if (pk === "absolute")
    body.period = { kind:"absolute", start:$("p-start").value,
                    end:$("p-end").value };
  else if (pk) body.period = { kind: pk };
  try {
    const r = await fetch(API + "/generate", { method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify(body) });
    const d = await r.json();
    if (!r.ok){
      $("status").textContent = "failed";
      $("errors").textContent = d.errors
        ? d.errors.map(e => e.path + ": " + e.msg).join("\n")
        : (d.error || "error");
      return;
    }
    $("status").innerHTML = '<span class="ok">done — ' + d.filename +
      " (" + d.packets + " pkts)</span>";
    refresh();
  } catch(e){ $("status").textContent = "failed: " + e; }
}

$("pkind").addEventListener("change",
  () => $("abs-row").style.display =
    $("pkind").value === "absolute" ? "inline" : "none");
$("tmpl").addEventListener("change", renderVars);
$("generate").addEventListener("click", generate);
$("refresh").addEventListener("click", () => { loadTemplates(); refresh(); });
loadTemplates(); refresh();
</script>
</body></html>
"""
