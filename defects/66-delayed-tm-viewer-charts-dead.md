# Defect 66 — Every plot in the delayed-TM viewer died with a ReferenceError, and every CUC timestamp was 0

## Summary

`tools/delayed_tm_viewer.py` (the offline S15 dump viewer on :8092) could not
draw a single chart. Three independent bugs, any one of which was fatal.

### 1. `ChartDataLabels` is an undeclared identifier

Both chart constructors — `addChart()` and `plotAllForSid()` — passed:

```js
plugins: [ChartDataLabels || {}]
```

`chartjs-plugin-datalabels` is never loaded by the page (it loaded only
`chart.js` and `chartjs-plugin-zoom`), and the identifier appears nowhere else
in the file. In JavaScript, `undeclaredIdentifier || {}` does **not**
short-circuit — evaluating the identifier throws:

```
$ node -e "try{ console.log(ChartDataLabels || {}) }catch(e){ console.log(e.constructor.name) }"
ReferenceError
```

The throw happened while building the options object for `new Chart(...)`, i.e.
*after* `chartsBody.appendChild(chartItem)` but *before*
`chartInstances[chartId] = chart`. The observable symptom was exactly what was
reported: clicking **Plot** or **Plot All** produced an empty bordered box with
a title and a ✕, containing a blank canvas, plus an uncaught `ReferenceError`
in the console — and `chartInstances` left permanently inconsistent, so
`removeChart()` could not clean up either.

### 2. Every CUC timestamp decoded as 0

`tools/tm_report.py` — the shared decoder the viewer imports:

```python
cuc = int(getattr(pkt.secondary, "time", 0) or 0)
```

`SecondaryHeader` (`smo_common/protocol/ecss_packet.py`) has **no** `time`
attribute; the field is `cuc_time`. `getattr(..., "time", 0)` therefore returned
the default `0` for every packet ever decoded. Downstream:

- `analyse_dumps()` built `series: [[cuc, value], …]` with every x at `0`;
- `first_cuc`/`last_cuc` stayed `None`, so the "CUC range" card read `— → —`;
- the sparkline drew a vertical scribble at the left edge (`dx = (x1-x0) || 1`);
- S5 events all rendered as `CUC 0`;
- `tools/tm_report.py`'s own HTML output had the same symptom.

### 3. `formatCucTime()` used the wrong CUC encoding

```js
const s = Math.floor(cuc / 256);
const date = new Date((s - 657720000) * 1000);
```

That assumes a packed coarse/fine CUC. The sim's CUC is **plain seconds since
2000-01-01T12:00:00Z**, packed as a bare `>I` (`ecss_packet.TIME_EPOCH`,
Unix 946 728 000). Dividing by 256 under-reported elapsed time 256×, and the
magic `657720000` corresponds to nothing. Once bug 2 was fixed this would have
produced confidently wrong axis labels.

### 4. The "fully offline" promise was false

The module docstring claims "It is fully offline — no CDN, no MCS / simulator
dependency at runtime", but the page loaded Chart.js and the zoom plugin from
`cdn.jsdelivr.net`. On an air-gapped classroom machine `Chart` is `undefined`
and every plot path fails with a *second* `ReferenceError`, masking the real
bug.

## Severity

**Major.** The tool's headline feature — plotting a parameter out of an archived
pass dump — did not work at all, in any environment.

## Status

**Fixed.**

1. `tools/tm_report.py` — `getattr(pkt.secondary, "time", 0)` →
   `getattr(pkt.secondary, "cuc_time", 0)`. This also repairs `tm_report.py`'s
   own HTML output.
2. `tools/delayed_tm_viewer.py` — both constructors now use
   `plugins: (typeof ChartDataLabels !== 'undefined') ? [ChartDataLabels] : []`,
   the only form that is safe against an undeclared binding. (No `datalabels`
   options are configured anywhere, so the plugin is genuinely unused; the guard
   keeps it working if it is ever added.)
3. `formatCucTime()` rewritten against the real epoch:
   ```js
   const CUC_EPOCH_UNIX_MS = Date.UTC(2000, 0, 1, 12, 0, 0);   // 946728000000
   ```
   and the dead `minCuc` locals and unused `getRelativeTime()` removed.
4. Chart.js is now served from the repo's vendor tree
   (`app.router.add_static("/vendor", …)`, preferring the MCS static vendor dir
   and falling back to the top-level `vendor/`), and the CDN `<script>` tags are
   gone — the docstring is now true. The zoom plugin is not vendored in this
   repo; its options are simply ignored by Chart.js when the plugin is absent.

## Verification

Run in-container against the 44 archived dumps in `workspace/dumps`:

- `GET /vendor/js/chart.umd.min.js` → `200`, 205 889 bytes (no CDN reference
  remains in the served HTML).
- `GET /api/decode?all=1` → 13 800 packets, `first_cuc 829032833`,
  `last_cuc 1783553163`; SIDs 1–5 decode with 51/71/17/26/23 parameters and
  series such as `eps.bat_voltage [[829032834, 27.2], [829032835, 27.21], …]`
  — i.e. **monotonically increasing, non-zero CUCs**, where every x used to be 0.
- The page's inline script was extracted and executed under Node with a stubbed
  DOM and a `Chart` shim: `addChart()` and `plotAllForSid()` both construct
  successfully (2 chart configs, 1 dataset × 3 points each, first label
  `18:53:54`), where previously they threw `ReferenceError` before reaching the
  constructor.

## Affected areas

- `tools/tm_report.py` (the `cuc_time` field name)
- `tools/delayed_tm_viewer.py` (plugin guard ×2, `formatCucTime`, dead locals,
  vendored Chart.js + `/vendor` static route)

## Forward work (not in this change)

`delayed_tm_viewer.py` still serialises whole series into inline `onclick=`
attributes via `JSON.stringify(p.series)`. SID 1 carries 51 parameters at 1 Hz,
so a multi-pass "Load all" can put megabytes of JSON into a single attribute,
and only `"` is escaped — a parameter name containing `'` or `\` would break the
handler. The fix is `data-sid` / `data-pid` attributes plus one delegated
listener reading from the already-populated `chartsData` (effort M).

## Related

- Defect #62 (ADCS failure-scenario viewer not integrated) — the other offline
  ground tool.
