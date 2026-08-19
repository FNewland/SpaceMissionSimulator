#!/usr/bin/env python3
"""Screenshot harness for the contrast rework (before/after sets).

Usage: PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python tools/shoot_contrast.py <outdir>
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots")
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})

    # MCS L1 overview
    pg.goto("http://localhost:9090/modern", wait_until="networkidle")
    pg.wait_for_timeout(4000)
    pg.screenshot(path=OUT / "mcs_l1_overview.png")

    # EPS L2
    pg.keyboard.press("2")  # rail hotkeys 1..n; fall back to click
    pg.wait_for_timeout(500)
    if not pg.locator("#pg-eps").is_visible():
        pg.locator('[data-page="eps"]').first.click()
    pg.wait_for_timeout(2500)
    pg.screenshot(path=OUT / "mcs_l2_eps.png")

    # Alarm drawer open (keyboard "a" or the ALARMS button)
    for sel in ("#alarm-drawer-btn", "#bd-tab-alarms", 'button:has-text("ALARMS")'):
        loc = pg.locator(sel)
        if loc.count():
            loc.first.click()
            break
    pg.wait_for_timeout(1500)
    pg.screenshot(path=OUT / "mcs_alarm_drawer.png")

    # Planner
    pg.goto("http://localhost:9091/modern", wait_until="networkidle")
    pg.wait_for_timeout(3000)
    pg.screenshot(path=OUT / "planner.png")

    # Instructor (simulator console)
    pg.goto("http://localhost:8080/modern", wait_until="networkidle")
    pg.wait_for_timeout(3000)
    pg.screenshot(path=OUT / "instructor.png")

    b.close()
print("wrote", OUT)
