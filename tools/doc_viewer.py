#!/usr/bin/env python3
"""doc_viewer.py — Lightweight Markdown documentation browser.

Serves the mission manual and procedure markdown files as rendered HTML
with a sidebar table of contents, navigation, and search. Fully offline —
no CDN dependencies. Uses a simple regex-based Markdown-to-HTML converter.

Usage::

    python tools/doc_viewer.py                          # default port 8095
    python tools/doc_viewer.py --port 9200
    python tools/doc_viewer.py --docs configs/eosat1/manual --port 8095
"""
from __future__ import annotations

import argparse
import html
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[0].parent
DEFAULT_DOCS = REPO / "configs" / "eosat1"

try:
    from aiohttp import web
except ImportError:
    print("ERROR: aiohttp required. Install: pip install aiohttp")
    raise SystemExit(1)


# ── Minimal Markdown → HTML converter (no dependencies) ──────────────

def md_to_html(text: str) -> str:
    """Convert Markdown to HTML using regex. Handles headings, bold, italic,
    code blocks, inline code, links, tables, lists, horizontal rules, and
    block quotes. Good enough for documentation browsing."""
    lines = text.split('\n')
    out: list[str] = []
    in_code_block = False
    in_table = False
    in_list = False
    list_type = 'ul'

    for line in lines:
        # Fenced code blocks
        if line.strip().startswith('```'):
            if in_code_block:
                out.append('</code></pre>')
                in_code_block = False
            else:
                lang = line.strip()[3:].strip()
                cls = f' class="lang-{html.escape(lang)}"' if lang else ''
                out.append(f'<pre><code{cls}>')
                in_code_block = True
            continue
        if in_code_block:
            out.append(html.escape(line))
            continue

        # Close open list if non-list line
        stripped = line.strip()
        is_list_item = bool(re.match(r'^[-*+] ', stripped) or re.match(r'^\d+\. ', stripped))
        if in_list and not is_list_item and stripped:
            out.append(f'</{list_type}>')
            in_list = False

        # Close table
        if in_table and not stripped.startswith('|'):
            out.append('</tbody></table>')
            in_table = False

        # Empty line
        if not stripped:
            if not in_list and not in_table:
                out.append('<br>')
            continue

        # Headings
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            level = len(m.group(1))
            content = _inline(m.group(2))
            slug = re.sub(r'[^a-z0-9]+', '-', m.group(2).lower()).strip('-')
            out.append(f'<h{level} id="{slug}">{content}</h{level}>')
            continue

        # Horizontal rule
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            out.append('<hr>')
            continue

        # Block quote
        if stripped.startswith('> '):
            out.append(f'<blockquote>{_inline(stripped[2:])}</blockquote>')
            continue

        # Table
        if stripped.startswith('|'):
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if all(re.match(r'^[-:]+$', c) for c in cells):
                # Separator row — skip (already started table)
                continue
            if not in_table:
                out.append('<table><thead><tr>')
                for c in cells:
                    out.append(f'<th>{_inline(c)}</th>')
                out.append('</tr></thead><tbody>')
                in_table = True
            else:
                out.append('<tr>')
                for c in cells:
                    out.append(f'<td>{_inline(c)}</td>')
                out.append('</tr>')
            continue

        # Unordered list
        m = re.match(r'^[-*+] (.*)', stripped)
        if m:
            if not in_list:
                list_type = 'ul'
                out.append('<ul>')
                in_list = True
            out.append(f'<li>{_inline(m.group(1))}</li>')
            continue

        # Ordered list
        m = re.match(r'^\d+\. (.*)', stripped)
        if m:
            if not in_list:
                list_type = 'ol'
                out.append('<ol>')
                in_list = True
            out.append(f'<li>{_inline(m.group(1))}</li>')
            continue

        # Paragraph
        out.append(f'<p>{_inline(line)}</p>')

    # Close open blocks
    if in_code_block:
        out.append('</code></pre>')
    if in_table:
        out.append('</tbody></table>')
    if in_list:
        out.append(f'</{list_type}>')

    return '\n'.join(out)


def _inline(text: str) -> str:
    """Process inline Markdown: bold, italic, code, links, images."""
    # Inline code
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    # Images
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1">', text)
    # Links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    return text


# ── File discovery ────────────────────────────────────────────────────

def discover_docs(base: Path) -> dict[str, list[dict]]:
    """Find all .md files organized by category."""
    categories: dict[str, list[dict]] = {}
    for subdir in sorted(base.iterdir()):
        if subdir.is_dir():
            md_files = sorted(subdir.rglob('*.md'))
            if md_files:
                cat_name = subdir.name
                categories[cat_name] = [
                    {"name": f.stem, "path": str(f.relative_to(base)),
                     "title": _extract_title(f)}
                    for f in md_files
                ]
    # Also include top-level .md files
    top_level = sorted(base.glob('*.md'))
    if top_level:
        categories['_root'] = [
            {"name": f.stem, "path": str(f.relative_to(base)),
             "title": _extract_title(f)}
            for f in top_level
        ]
    return categories


def _extract_title(path: Path) -> str:
    """Extract the first heading from a markdown file."""
    try:
        with open(path) as f:
            for line in f:
                m = re.match(r'^#\s+(.*)', line.strip())
                if m:
                    return m.group(1)
        return path.stem.replace('_', ' ').title()
    except Exception:
        return path.stem


# ── HTML templates ────────────────────────────────────────────────────

PAGE_CSS = """
:root { --bg:#0d1117; --sidebar:#161b22; --border:#21262d; --text:#c9d1d9;
  --dim:#8b949e; --accent:#58a6ff; --code-bg:#1c2128; --link:#58a6ff; }
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; font-size:15px; line-height:1.6; display:flex; height:100vh; }
#sidebar { width:280px; min-width:280px; background:var(--sidebar); border-right:1px solid var(--border); overflow-y:auto; padding:12px; }
#sidebar h2 { color:var(--accent); font-size:13px; text-transform:uppercase; letter-spacing:1px; margin:12px 0 6px; }
#sidebar a { display:block; padding:3px 8px; color:var(--dim); text-decoration:none; font-size:13px; border-radius:4px; }
#sidebar a:hover, #sidebar a.active { background:var(--border); color:var(--text); }
#sidebar input { width:100%; padding:6px 8px; background:var(--code-bg); border:1px solid var(--border); border-radius:4px; color:var(--text); font-size:13px; margin-bottom:8px; }
#content { flex:1; overflow-y:auto; padding:24px 40px; max-width:900px; }
#content h1 { font-size:28px; border-bottom:1px solid var(--border); padding-bottom:8px; margin-bottom:16px; }
#content h2 { font-size:22px; margin-top:24px; border-bottom:1px solid var(--border); padding-bottom:4px; }
#content h3 { font-size:18px; margin-top:16px; }
#content p { margin:8px 0; }
#content a { color:var(--link); }
#content code { background:var(--code-bg); padding:2px 6px; border-radius:3px; font-size:13px; }
#content pre { background:var(--code-bg); padding:12px; border-radius:6px; overflow-x:auto; margin:12px 0; }
#content pre code { padding:0; background:none; }
#content table { border-collapse:collapse; margin:12px 0; width:100%; }
#content th,#content td { border:1px solid var(--border); padding:6px 10px; text-align:left; }
#content th { background:var(--sidebar); }
#content blockquote { border-left:3px solid var(--accent); padding-left:12px; color:var(--dim); margin:8px 0; }
#content hr { border:none; border-top:1px solid var(--border); margin:16px 0; }
#content img { max-width:100%; }
#content ul,#content ol { padding-left:24px; margin:8px 0; }
#content li { margin:2px 0; }
.breadcrumb { font-size:12px; color:var(--dim); margin-bottom:12px; }
.breadcrumb a { color:var(--accent); text-decoration:none; }
"""


def build_index_html(categories: dict[str, list[dict]]) -> str:
    sidebar_html = '<input type="text" id="search" placeholder="Search docs..." oninput="filterDocs()">\n'
    cat_order = sorted(categories.keys(), key=lambda c: (c == '_root', c))
    for cat in cat_order:
        label = 'Overview' if cat == '_root' else cat.replace('_', ' ').title()
        sidebar_html += f'<h2>{html.escape(label)}</h2>\n'
        for doc in categories[cat]:
            sidebar_html += (
                f'<a href="/doc/{html.escape(doc["path"])}" class="doc-link" '
                f'data-title="{html.escape(doc["title"].lower())}">'
                f'{html.escape(doc["title"])}</a>\n'
            )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>EOSAT-1 Documentation</title>
<style>{PAGE_CSS}</style>
</head><body>
<div id="sidebar">
<h2 style="margin-top:0">EOSAT-1 Docs</h2>
{sidebar_html}
</div>
<div id="content">
<h1>EOSAT-1 Mission Documentation</h1>
<p>Select a document from the sidebar to begin reading.</p>
<p>This viewer renders all Markdown documentation from the mission configuration.</p>
<h3>Categories</h3>
<ul>
{"".join(f'<li><strong>{cat.replace("_"," ").title()}</strong>: {len(docs)} documents</li>' for cat, docs in categories.items())}
</ul>
</div>
<script>
function filterDocs() {{
  const q = document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('.doc-link').forEach(a => {{
    a.style.display = a.dataset.title.includes(q) ? '' : 'none';
  }});
}}
</script>
</body></html>"""


def build_doc_html(title: str, body_html: str, breadcrumb: str,
                   categories: dict[str, list[dict]]) -> str:
    sidebar_html = '<input type="text" id="search" placeholder="Search docs..." oninput="filterDocs()">\n'
    cat_order = sorted(categories.keys(), key=lambda c: (c == '_root', c))
    for cat in cat_order:
        label = 'Overview' if cat == '_root' else cat.replace('_', ' ').title()
        sidebar_html += f'<h2>{html.escape(label)}</h2>\n'
        for doc in categories[cat]:
            sidebar_html += (
                f'<a href="/doc/{html.escape(doc["path"])}" class="doc-link" '
                f'data-title="{html.escape(doc["title"].lower())}">'
                f'{html.escape(doc["title"])}</a>\n'
            )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{html.escape(title)} — EOSAT-1 Docs</title>
<style>{PAGE_CSS}</style>
</head><body>
<div id="sidebar">
<h2 style="margin-top:0"><a href="/" style="color:var(--accent);text-decoration:none">EOSAT-1 Docs</a></h2>
{sidebar_html}
</div>
<div id="content">
<div class="breadcrumb">{breadcrumb}</div>
{body_html}
</div>
<script>
function filterDocs() {{
  const q = document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('.doc-link').forEach(a => {{
    a.style.display = a.dataset.title.includes(q) ? '' : 'none';
  }});
}}
</script>
</body></html>"""


# ── aiohttp server ────────────────────────────────────────────────────

def create_app(docs_root: Path) -> web.Application:
    categories = discover_docs(docs_root)
    app = web.Application()

    async def index(request):
        return web.Response(text=build_index_html(categories),
                            content_type='text/html')

    async def doc_handler(request):
        rel_path = request.match_info['path']
        full_path = docs_root / rel_path
        if not full_path.exists() or not full_path.suffix == '.md':
            raise web.HTTPNotFound(text=f"Document not found: {rel_path}")
        md_text = full_path.read_text(encoding='utf-8')
        title = _extract_title(full_path)
        body = md_to_html(md_text)
        parts = rel_path.split('/')
        breadcrumb = '<a href="/">Home</a>'
        if len(parts) > 1:
            breadcrumb += f' / <a href="/">{html.escape(parts[0])}</a>'
        breadcrumb += f' / {html.escape(title)}'
        return web.Response(
            text=build_doc_html(title, body, breadcrumb, categories),
            content_type='text/html')

    app.router.add_get('/', index)
    app.router.add_get('/doc/{path:.+}', doc_handler)
    return app


def main():
    parser = argparse.ArgumentParser(description='EOSAT-1 Documentation Viewer')
    parser.add_argument('--port', type=int, default=8095,
                        help='HTTP port (default: 8095)')
    parser.add_argument('--docs', type=str, default=None,
                        help='Documentation root directory')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [doc_viewer] %(message)s')

    docs_root = Path(args.docs) if args.docs else DEFAULT_DOCS
    if not docs_root.exists():
        print(f"ERROR: docs directory not found: {docs_root}")
        raise SystemExit(1)

    app = create_app(docs_root)
    logger.info("Documentation viewer: http://localhost:%d", args.port)
    web.run_app(app, host='0.0.0.0', port=args.port, print=None)


if __name__ == '__main__':
    main()
