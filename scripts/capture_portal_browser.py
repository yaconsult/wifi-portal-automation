#!/usr/bin/env python3
"""Drive a real browser through a captive portal and record everything.

Use at a hotspot while connected to the portal network, BEFORE
accepting the terms.  Opens a visible browser, navigates to a
plain-HTTP probe URL, and records:

- ``flow.har`` — every request/response with bodies (the gold standard)
- ``page_NN.html`` — the *rendered* DOM at each navigation (JS executed,
  unlike curl which sees only the raw shell)
- ``page_NN.png`` — screenshot at each navigation
- ``nav_log.txt`` — ordered list of every top-frame URL visited

Complete the portal login manually inside the browser window — the
recording keeps running.  Close the browser window (or press Ctrl+C)
to finalize the HAR.

One-time setup (needs internet, do this at home)::

    uv run --with playwright playwright install chromium

Usage::

    uv run --with playwright python scripts/capture_portal_browser.py [OUTDIR]

OUTDIR defaults to ``portal_capture_browser_<timestamp>/`` in the repo.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
PROBE_URL = "http://neverssl.com/"  # plain HTTP — triggers the portal redirect


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright not installed. Run:\n"
            "  uv run --with playwright playwright install chromium",
            file=sys.stderr,
        )
        return 1

    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        REPO_DIR / f"portal_capture_browser_{datetime.now():%Y%m%d_%H%M%S}"
    )
    outdir.mkdir(parents=True, exist_ok=True)
    nav_log = outdir / "nav_log.txt"
    step = 0

    print(f"Recording to {outdir}")
    print("A browser window will open and load the probe URL.")
    print("Complete the portal login manually — recording continues.")
    print("Close the browser window (or Ctrl+C) to finish.\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            ignore_https_errors=True,  # portal MITMs certs
            record_har_path=str(outdir / "flow.har"),
            record_har_content="embed",
        )
        page = context.new_page()

        def on_frame_navigated(frame) -> None:
            nonlocal step
            if frame != page.main_frame:
                return
            step += 1
            url = frame.url
            with nav_log.open("a", encoding="utf-8") as f:
                f.write(f"{step:03d} {url}\n")
            print(f"  [{step:03d}] {url}")
            # Let JS render before capturing DOM/screenshot
            try:
                page.wait_for_timeout(1500)
                (outdir / f"page_{step:02d}.html").write_text(
                    page.content(), encoding="utf-8"
                )
                page.screenshot(path=str(outdir / f"page_{step:02d}.png"))
            except Exception as e:  # page may be mid-navigation/closed
                print(f"      (capture skipped: {e})")

        page.on("framenavigated", on_frame_navigated)
        page.goto(PROBE_URL, wait_until="commit")

        try:
            while context.pages:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

        context.close()
        browser.close()

    print(f"\nDone. Files in {outdir}:")
    print("  flow.har    — full request/response log")
    print("  nav_log.txt — ordered URL chain")
    print("  page_NN.*   — rendered DOM + screenshot per page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
