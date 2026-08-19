#!/usr/bin/env python3
"""WCAG 2.2 contrast validator for the SMO design-system palette.

Run:  python tools/check_contrast.py
Used to produce the tables in docs/design_2026-08/CONTRAST_REWORK.md.
Targets: text tokens >= 7:1 (AAA) on their primary dark surfaces,
graphics tokens >= 3:1; light theme text >= 4.5:1 (AA).
"""


def lum(hexstr):
    h = hexstr.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    def f(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = lum(a), lum(b)
    lo, hi = min(la, lb), max(la, lb)
    return (hi + 0.05) / (lo + 0.05)


DARK_SURFACES = {"--bg": "#0B0D10", "--s1": "#14171C", "--s2": "#1C2129", "--s3": "#262C36"}
DARK_TEXT = {   # token: (hex, floor)
    "--val": ("#F5F7FA", 7.0),
    "--tx": ("#E9EDF2", 7.0),
    "--tx2": ("#C0C9D4", 7.0),
    "--tx3": ("#9BA7B4", 7.0),
    "--ok": ("#00C896", 7.0),
    "--warn": ("#F0B429", 7.0),
    "--alarm": ("#FF7B72", 7.0),
    "--info": ("#6BB3FF", 7.0),
    "--shelf": ("#A79BF8", 7.0),
}
DARK_GFX = {"--stale": ("#7E8697", 3.0), "--line-2": ("#3B4450", 1.0)}

LIGHT_SURFACES = {"--bg": "#C2C4C7", "--s1": "#CFD1D3", "--s2": "#D6D7D9", "--s3": "#DEDFE1"}
LIGHT_TEXT = {
    "--val": ("#0B0D10", 4.5),
    "--tx": ("#141619", 4.5),
    "--tx2": ("#383C42", 4.5),
    "--tx3": ("#4A4F56", 4.5),
    "--ok": ("#0B5A42", 4.5),
    "--warn": ("#6E4300", 4.5),
    "--alarm": ("#8F1D12", 4.5),
    "--info": ("#15477C", 4.5),
    "--shelf": ("#453A96", 4.5),
}
LIGHT_GFX = {"--stale": ("#53575D", 3.0)}


def table(name, surfaces, tokens, gfx):
    ok = True
    print(f"\n{name}  (floor shown per token; graphics floor 3:1)")
    hdr = "  {:8} {:9} floor " + " ".join(f"{s:>6}" for s in surfaces)
    print(hdr.format("token", "hex"))
    for tok, (hexv, floor) in {**tokens, **gfx}.items():
        rr = [ratio(hexv, sv) for sv in surfaces.values()]
        # primary surface for text = --bg and --s1 (cards); s2/s3 informational
        primary = rr[:2] if tok in tokens else rr
        status = "PASS" if all(x >= floor for x in primary) else "FAIL"
        if status == "FAIL":
            ok = False
        print("  {:8} {:9} {:>4.1f}  ".format(tok, hexv, floor)
              + " ".join(f"{x:6.2f}" for x in rr) + f"  {status}")
    return ok


if __name__ == "__main__":
    ok = table("DARK", DARK_SURFACES, DARK_TEXT, DARK_GFX)
    ok &= table("LIGHT", LIGHT_SURFACES, LIGHT_TEXT, LIGHT_GFX)
    raise SystemExit(0 if ok else 1)
