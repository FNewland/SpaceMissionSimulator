"""SMO Common — Delayed-TM (S15) dump store.

Single source of truth for **where** archived delayed-telemetry dumps live,
**how** they are enumerated, fetched and summarised, and **how** they are
served over HTTP.

Background
----------
When the operator commands an S15.9 "start dump", the simulator paces the
stored packets down the (simulated) RF link *and* archives the complete raw
dump to a file — the ground segment's tape of everything the spacecraft
played back, including whatever the link dropped. Those files are the only
durable record of delayed telemetry.

Historically the archive was a plain directory that a **separate**
single-file tool (``tools/delayed_tm_viewer.py``) read straight off the
local filesystem. That works on one machine and only on one machine: in a
server + client classroom the files exist on the server's disk and a student
browser has no route to them at all (defect #90).

This module fixes that by giving the store:

* one **resolution order**, honoured by every writer and every reader::

      explicit argument (CLI flag)
        > SMO_DUMP_DIR environment variable
        > mission config `dump_dir:` (configs/<mission>/mission.yaml)
        > <cwd>/workspace/dumps

* one **on-disk format**: ``dump_sid<NN>_<YYYYMMDDThhmmssZ>.bin``, holding
  length-prefixed raw CCSDS/PUS packets (uint32 big-endian length, then that
  many bytes, repeated).

* one **HTTP API** (:func:`register_dump_routes`) that any process owning a
  store can mount, so the store is reachable by a browser on another
  machine without a shared filesystem:

  ==========================================  ===============================
  ``GET  <prefix>``                           JSON listing + metadata
  ``GET  <prefix>/file?name=<filename>``      raw dump bytes (download)
  ``GET  <prefix>/summary?name=<filename>``   decoded/summarised JSON view
  ``GET  <prefix>/<filename>``                alias for ``/file``
  ``GET  <page_path>``                        self-contained browser page
  ==========================================  ===============================

  Every filename that reaches this module goes through
  :func:`resolve_dump_file`, which refuses anything that is not a plain
  basename inside the store. These endpoints are network-exposed.

The API is mounted by the MCS (the ground segment owns the ground archive,
and the MCS is the port students already have open) and by the simulator's
instructor server (the process that actually writes the files, so a split
simulator/MCS deployment still has a working route). See
``docs/DEPLOYMENT.md``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "DUMP_DIR_ENV",
    "DEFAULT_DUMP_DIR",
    "DUMP_GLOB",
    "DUMP_NAME_RE",
    "DumpPathError",
    "resolve_dump_dir",
    "parse_dump_filename",
    "iter_packets",
    "read_dump",
    "count_packets",
    "list_dumps",
    "resolve_dump_file",
    "summarise_dump",
    "register_dump_routes",
]

#: Environment variable that overrides the mission-config setting.
DUMP_DIR_ENV = "SMO_DUMP_DIR"

#: Final fallback, relative to the process working directory.
DEFAULT_DUMP_DIR = "workspace/dumps"

#: Glob matching every archive file in a store.
DUMP_GLOB = "dump_*.bin"

#: Canonical archive filename: ``dump_sid<NN>_<YYYYMMDDThhmmssZ>.bin``.
DUMP_NAME_RE = re.compile(r"^dump_sid(\d+)_(\d{8}T\d{6}Z)\.bin$")

#: Characters permitted in a fetchable dump filename.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.bin$")


class DumpPathError(ValueError):
    """A requested dump filename is not a plain file inside the store."""


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

def resolve_dump_dir(mission_cfg: Any = None, explicit: str | os.PathLike | None = None) -> Path:
    """Resolve the delayed-TM dump store directory.

    Precedence (highest first):

    1. ``explicit`` — a CLI flag such as ``--dumps``.
    2. ``$SMO_DUMP_DIR``.
    3. ``mission_cfg.dump_dir`` (``dump_dir:`` in ``mission.yaml``).
    4. ``workspace/dumps`` relative to the process working directory.

    The result is ``expanduser()``-ed but deliberately **not** resolved to an
    absolute path, so the default stays relative to whatever working
    directory the service was started in (that is what every start script
    already relies on).
    """
    chosen = (
        (str(explicit) if explicit else None)
        or os.environ.get(DUMP_DIR_ENV)
        or (getattr(mission_cfg, "dump_dir", None) if mission_cfg is not None else None)
        or DEFAULT_DUMP_DIR
    )
    return Path(chosen).expanduser()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def parse_dump_filename(name: str) -> tuple[int | None, datetime | None]:
    """Return ``(store_id, utc_datetime)`` parsed from a canonical filename.

    Either element is ``None`` when the name does not follow the convention.
    """
    m = DUMP_NAME_RE.match(name)
    if not m:
        return None, None
    store_id = int(m.group(1))
    try:
        ts = datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        ts = None
    return store_id, ts


def iter_packets(data: bytes) -> Iterator[bytes]:
    """Yield raw packets from the length-prefixed archive byte stream."""
    off = 0
    n_bytes = len(data)
    while off + 4 <= n_bytes:
        (n,) = struct.unpack(">I", data[off:off + 4])
        off += 4
        if off + n > n_bytes:
            break  # truncated trailer — stop rather than raise
        yield data[off:off + n]
        off += n


def read_dump(path: Path) -> Iterator[bytes]:
    """Yield raw packet bytes from a length-prefixed ``.bin`` dump file."""
    return iter_packets(Path(path).read_bytes())


def count_packets(path: Path) -> int:
    """Number of packets in a dump file (0 if unreadable)."""
    try:
        return sum(1 for _ in read_dump(path))
    except OSError:
        return 0


def list_dumps(dump_dir: Path | str) -> list[dict[str, Any]]:
    """Return metadata for every dump in ``dump_dir``, oldest first.

    Each entry carries ``filename``, ``store_id`` (plus a ``sid`` alias kept
    for the standalone viewer), ``timestamp``, ``size_bytes``, ``mtime`` and
    ``packet_count``. ``path`` is included for in-process callers; network
    clients should ignore it — it is a server-side path.

    A missing store is not an error: it returns ``[]`` (an empty store is a
    perfectly normal state before the first dump of a run).
    """
    dump_dir = Path(dump_dir)
    out: list[dict[str, Any]] = []
    if not dump_dir.is_dir():
        return out
    for p in sorted(dump_dir.glob(DUMP_GLOB)):
        if not p.is_file():
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        store_id, ts = parse_dump_filename(p.name)
        out.append({
            "filename": p.name,
            "path": str(p),
            "store_id": store_id,
            "sid": store_id,
            "timestamp": ts.isoformat() if ts else None,
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "packet_count": count_packets(p),
        })
    out.sort(key=lambda d: d.get("timestamp") or d.get("mtime") or "")
    return out


def resolve_dump_file(dump_dir: Path | str, filename: str) -> Path:
    """Map a client-supplied ``filename`` onto a real file inside the store.

    This is the security boundary for the network-exposed fetch endpoints.
    It refuses, with :class:`DumpPathError`:

    * empty names, ``.`` / ``..``;
    * anything containing a path separator or a NUL byte (so ``../etc/passwd``
      and its URL-encoded forms never resolve);
    * names that are not ``<safe chars>.bin``;
    * absolute paths and drive letters;
    * anything that — after following symlinks — lands outside the store.

    Raises :class:`FileNotFoundError` when the name is well-formed but no
    such dump exists.
    """
    if not isinstance(filename, str) or not filename:
        raise DumpPathError("missing dump filename")
    if "\x00" in filename:
        raise DumpPathError("illegal dump filename")
    if "/" in filename or "\\" in filename:
        raise DumpPathError(f"dump filename must not contain a path: {filename!r}")
    if filename in (".", "..") or Path(filename).name != filename:
        raise DumpPathError(f"dump filename must be a plain name: {filename!r}")
    if Path(filename).is_absolute() or (len(filename) > 1 and filename[1] == ":"):
        raise DumpPathError(f"dump filename must be relative: {filename!r}")
    if not _SAFE_NAME_RE.match(filename):
        raise DumpPathError(f"not a dump archive filename: {filename!r}")

    root = Path(dump_dir)
    candidate = root / filename
    # Belt and braces: a symlink inside the store must not escape it either.
    try:
        real_root = root.resolve()
        real_candidate = candidate.resolve()
    except OSError as e:  # pragma: no cover - unusual filesystem state
        raise DumpPathError(f"cannot resolve dump path: {e}") from e
    if real_root not in real_candidate.parents:
        raise DumpPathError(f"dump filename escapes the store: {filename!r}")

    if not candidate.is_file():
        raise FileNotFoundError(filename)
    return candidate


# ---------------------------------------------------------------------------
# Summarising
# ---------------------------------------------------------------------------

def summarise_dump(path: Path) -> dict[str, Any]:
    """Decode a dump far enough to describe it without any mission catalogue.

    Returns packet counts per PUS service/subtype, the APIDs seen, the CUC
    time span and a CRC error count. This is the "what is actually in this
    file" view a student needs before deciding whether to download it; the
    full parameter-level decode (with engineering units and plots) lives in
    ``tools/delayed_tm_viewer.py``, which needs the mission catalogues.
    """
    from smo_common.protocol.ecss_packet import decommutate_packet, parse_cuc_time

    path = Path(path)
    store_id, ts = parse_dump_filename(path.name)
    counts: dict[tuple[int, int], int] = {}
    apids: set[int] = set()
    first_cuc: int | None = None
    last_cuc: int | None = None
    crc_errors = 0
    total = 0
    undecodable = 0

    for raw in read_dump(path):
        total += 1
        pkt = decommutate_packet(raw)
        if pkt is None:
            undecodable += 1
            continue
        apids.add(pkt.primary.apid)
        if not pkt.crc_valid:
            crc_errors += 1
        sec = pkt.secondary
        if sec is None:
            continue
        key = (sec.service, sec.subtype)
        counts[key] = counts.get(key, 0) + 1
        cuc = sec.cuc_time
        if cuc:
            first_cuc = cuc if first_cuc is None else min(first_cuc, cuc)
            last_cuc = cuc if last_cuc is None else max(last_cuc, cuc)

    def _utc(cuc: int | None) -> str | None:
        if cuc is None:
            return None
        try:
            return parse_cuc_time(cuc).replace(tzinfo=timezone.utc).isoformat()
        except (ValueError, OverflowError):  # pragma: no cover - defensive
            return None

    try:
        size = path.stat().st_size
    except OSError:  # pragma: no cover - defensive
        size = 0

    return {
        "filename": path.name,
        "store_id": store_id,
        "timestamp": ts.isoformat() if ts else None,
        "size_bytes": size,
        "packet_count": total,
        "undecodable_packets": undecodable,
        "crc_errors": crc_errors,
        "apids": sorted(apids),
        "first_cuc": first_cuc,
        "last_cuc": last_cuc,
        "first_utc": _utc(first_cuc),
        "last_utc": _utc(last_cuc),
        "packet_counts": [
            {"service": s, "subtype": st, "count": n}
            for (s, st), n in sorted(counts.items())
        ],
    }


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

def _public_meta(entry: dict[str, Any]) -> dict[str, Any]:
    """Strip server-side paths out of a listing entry before serving it."""
    return {k: v for k, v in entry.items() if k != "path"}


def register_dump_routes(
    app: Any,
    get_dump_dir: Callable[[], Path] | Path | str,
    *,
    prefix: str = "/api/dumps",
    page_path: str | None = "/dumps",
    owner: str = "",
) -> Any:
    """Mount the delayed-TM dump API (and optional browser page) on ``app``.

    ``get_dump_dir`` may be a path or a zero-argument callable returning one,
    so a service that resolves its store lazily (or can be reconfigured) still
    serves the current location.

    ``owner`` is a human-readable label for the serving process (e.g. ``MCS``)
    reported in the listing, so a student can tell which service they are
    looking at.

    Returns ``app`` for chaining.
    """
    from aiohttp import web

    def _dir() -> Path:
        return Path(get_dump_dir() if callable(get_dump_dir) else get_dump_dir)

    async def handle_list(_req):
        dump_dir = _dir()
        dumps = [_public_meta(d) for d in list_dumps(dump_dir)]
        return web.json_response({
            "owner": owner,
            "store": str(dump_dir),
            "exists": dump_dir.is_dir(),
            "count": len(dumps),
            "total_bytes": sum(d["size_bytes"] for d in dumps),
            "dumps": dumps,
        })

    def _requested_name(req) -> str:
        return (req.query.get("name") or req.match_info.get("name") or "").strip()

    async def handle_file(req):
        name = _requested_name(req)
        try:
            path = resolve_dump_file(_dir(), name)
        except DumpPathError as e:
            logger.warning("Rejected delayed-TM dump request %r: %s", name, e)
            return web.json_response({"error": str(e)}, status=400)
        except FileNotFoundError:
            return web.json_response({"error": f"no such dump: {name}"}, status=404)
        return web.FileResponse(path, headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{path.name}"',
        })

    async def handle_summary(req):
        name = _requested_name(req)
        if not name:
            # Whole-store summary — cheap metadata for every dump.
            return web.json_response({
                "store": str(_dir()),
                "dumps": [_public_meta(d) for d in list_dumps(_dir())],
            })
        try:
            path = resolve_dump_file(_dir(), name)
        except DumpPathError as e:
            logger.warning("Rejected delayed-TM summary request %r: %s", name, e)
            return web.json_response({"error": str(e)}, status=400)
        except FileNotFoundError:
            return web.json_response({"error": f"no such dump: {name}"}, status=404)
        return web.json_response(summarise_dump(path))

    async def handle_page(_req):
        return web.Response(text=_render_page(prefix, owner), content_type="text/html")

    prefix = prefix.rstrip("/")
    app.router.add_get(prefix, handle_list)
    app.router.add_get(prefix + "/", handle_list)
    # Specific routes first — aiohttp resolves in registration order, so
    # "/file" and "/summary" must win over the "{name}" catch-all below.
    app.router.add_get(prefix + "/file", handle_file)
    app.router.add_get(prefix + "/summary", handle_summary)
    app.router.add_get(prefix + "/{name}", handle_file)
    app.router.add_get(prefix + "/{name}/summary", handle_summary)
    if page_path:
        app.router.add_get(page_path, handle_page)
    return app


def _render_page(api_prefix: str, owner: str) -> str:
    """A dependency-free HTML page listing and downloading the store.

    Deliberately self-contained (no CDN, no vendored JS) and served from the
    same origin as the API, so it works from any browser on the network with
    no extra port, no shared filesystem and no CORS configuration.
    """
    return _PAGE_TEMPLATE.replace("__API__", json.dumps(api_prefix)) \
                         .replace("__OWNER__", json.dumps(owner or "ground segment"))


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Delayed TM Archive</title>
<style>
:root { --bg:#0a1226; --panel:#0f1d33; --border:#1f3050; --text:#cfe;
        --dim:#7a90ad; --accent:#5fb7ff; --warn:#f59e0b; }
* { box-sizing:border-box; }
html,body { background:var(--bg); color:var(--text); margin:0;
            font-family:'JetBrains Mono',Consolas,monospace; font-size:12px; }
header { padding:10px 16px; background:linear-gradient(180deg,#0f1d33,#0b1525);
         border-bottom:1px solid var(--border); display:flex; gap:16px; align-items:center; }
h1 { font-size:14px; letter-spacing:2px; color:var(--accent); margin:0; }
.sub { color:var(--dim); font-size:11px; }
main { padding:14px 16px; }
button, a.btn { background:#1a3a6a; color:var(--text); border:1px solid #2a5a8a;
                border-radius:3px; padding:4px 10px; cursor:pointer; font:inherit;
                font-size:11px; text-decoration:none; display:inline-block; }
button:hover, a.btn:hover { background:#2a5a8a; }
table { width:100%; border-collapse:collapse; margin-top:12px; }
th,td { border-bottom:1px solid #16223a; padding:5px 8px; text-align:left; }
th { color:var(--dim); font-weight:500; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
pre { background:var(--panel); border:1px solid var(--border); border-radius:4px;
      padding:10px; overflow:auto; max-height:340px; }
.empty { color:var(--warn); padding:14px 0; }
.store { color:var(--dim); }
</style>
</head><body>
<header>
  <h1>DELAYED TM ARCHIVE</h1>
  <span class="sub" id="sub"></span>
  <button id="refresh">Refresh</button>
</header>
<main>
  <div class="store" id="store"></div>
  <div id="list"></div>
  <h2 style="font-size:11px;color:var(--dim);letter-spacing:1.5px;">SUMMARY</h2>
  <pre id="summary">Select "summary" on a dump above.</pre>
</main>
<script>
const API = __API__;
const OWNER = __OWNER__;
const $ = (id) => document.getElementById(id);

function fmtBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " kB";
  return (n / 1048576).toFixed(2) + " MB";
}

async function refresh() {
  $("sub").textContent = "loading…";
  let d;
  try {
    const r = await fetch(API);
    d = await r.json();
  } catch (e) {
    $("sub").textContent = "cannot reach " + API + " (" + e + ")";
    return;
  }
  $("sub").textContent = OWNER + " — " + d.count + " dump(s), " + fmtBytes(d.total_bytes || 0);
  $("store").textContent = "store: " + d.store + (d.exists ? "" : "  (does not exist yet)");
  if (!d.dumps.length) {
    $("list").innerHTML = '<div class="empty">No delayed-TM dumps yet. ' +
      'Command an S15 dump (TM_DUMP) from the MCS during a pass, then refresh.</div>';
    return;
  }
  const rows = d.dumps.slice().reverse().map((x) => `
    <tr>
      <td>${x.filename}</td>
      <td class="num">${x.store_id == null ? "--" : x.store_id}</td>
      <td>${x.timestamp || x.mtime || "--"}</td>
      <td class="num">${x.packet_count}</td>
      <td class="num">${fmtBytes(x.size_bytes)}</td>
      <td>
        <a class="btn" href="${API}/file?name=${encodeURIComponent(x.filename)}">download</a>
        <button data-name="${x.filename}" class="sumbtn">summary</button>
      </td>
    </tr>`).join("");
  $("list").innerHTML = `<table>
    <tr><th>FILE</th><th>STORE</th><th>DUMPED (UTC)</th>
        <th>PACKETS</th><th>SIZE</th><th></th></tr>${rows}</table>`;
  for (const b of document.querySelectorAll(".sumbtn")) {
    b.addEventListener("click", () => showSummary(b.dataset.name));
  }
}

async function showSummary(name) {
  $("summary").textContent = "decoding " + name + "…";
  try {
    const r = await fetch(API + "/summary?name=" + encodeURIComponent(name));
    $("summary").textContent = JSON.stringify(await r.json(), null, 2);
  } catch (e) {
    $("summary").textContent = "failed: " + e;
  }
}

$("refresh").addEventListener("click", refresh);
refresh();
setInterval(refresh, 30000);
</script>
</body></html>
"""
