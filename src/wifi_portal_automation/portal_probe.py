"""On-device capture probe for captive portals.

Written for the Costco portal (JS-rendered, launches the Costco app —
most likely via a ``costco://`` deep link or an ``intent://`` URI fired
by the portal page; raw HTML capture was not enough, the decisive
evidence was the Android *intent* the page fires), now generalized for
any portal via ``--name``/``--package``.

This probe collects everything needed to understand the flow, entirely
on the phone (no laptop, no Playwright):

1. Portal probe via a WiFi-bound session — redirect chain, portal
   host, raw HTML (reuses :mod:`wifi_portal_automation.portal_analyzer` and
   :mod:`wifi_portal_automation.wifi_binding`).
2. Deep-link candidates extracted from the portal HTML and its
   linked JS bundles (any non-http(s) scheme: ``costco://``,
   ``intent://``, app-link hosts, etc.).
3. An ``am start`` that opens the portal URL in the system's captive
   portal sign-in browser (bound to WiFi by Android), letting the
   page render and fire its deep link exactly as it does for a user.
4. A logcat slice capturing the resulting ``START ... dat=...``
   intent records and portal-related mentions (requires ``su``;
   gracefully skipped without root).
5. ``dumpsys`` snapshots: which activity ends up foreground, and the
   named app's declared intent filters (``--package``; skipped with
   ``--no-package``).
6. A post-capture connectivity probe so the capture also records
   whether the portal released the device.

CLI::

    wpa-costco-probe --outdir ~/storage/shared/costco_capture
    wpa-portal-probe --name shguestnet --no-package --wait 60 --json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from wifi_portal_automation.captive_portal import verify_internet_connectivity
from wifi_portal_automation.portal_analyzer import analyze_portal
from wifi_portal_automation.root_shell import find_su, run_root
from wifi_portal_automation.wifi_binding import InterfaceBindingError, create_wifi_session

if TYPE_CHECKING:
    import requests

logger = logging.getLogger("wifi_portal_automation.portal_probe")

# Probe URL used for portal detection (plain HTTP so the portal can
# redirect it).
PROBE_URL = "http://detectportal.firefox.com/canonical.html"

# Costco app package — confirmed installed on the test device.
COSTCO_PACKAGE = "com.costco.app.android"

# Deep-link patterns worth extracting from portal HTML/JS: any custom
# URI scheme (costco://, intent://, android-app://, a portal's own
# app scheme, ...) but not plain http(s) links.
_DEEP_LINK_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9+.-])((?!https?://)[a-z][a-z0-9+.-]*://[^\s\"'<>)]+)",
    re.IGNORECASE,
)

# <script src> / <link href> extraction (tolerant of quoting).
_ASSET_PATTERN = re.compile(
    r'(?:src|href)=["\']([^"\']+\.(?:js|json))["\']', re.IGNORECASE
)

# Logcat lines worth keeping: activity launches and portal/deep-link
# mentions.  The probe ``--name`` is added as a keyword at call time.
_LOGCAT_KEEP_PATTERN = re.compile(
    r"START |START u|act=|dat=|cmp=|deep ?link|captiveportal",
    re.IGNORECASE,
)

# Package-qualified VIEW intent for the system captive portal browser.
# Binding the portal URL to CaptivePortalLogin makes Android render it
# over WiFi — the same path a user's "Sign in to network" tap takes.
_CAPTIVE_PORTAL_PACKAGE = "com.android.captiveportallogin"

_MAX_ASSETS = 20
_MAX_ASSET_BYTES = 2_000_000


def extract_deep_links(text: str) -> list[str]:
    """Extract app deep links from HTML or JavaScript text.

    Args:
        text: Page or script source to scan.

    Returns:
        Sorted list of unique deep-link URIs found.
    """
    return sorted(set(_DEEP_LINK_PATTERN.findall(text)))


def fetch_portal_assets(
    session: requests.Session,
    portal_url: str,
    html: str,
    outdir: Path,
    timeout: int = 10,
) -> list[str]:
    """Download JS/JSON assets referenced by the portal page.

    Args:
        session: WiFi-bound HTTP session.
        portal_url: Portal page URL (used to resolve relative paths).
        html: Portal page HTML to scan for asset references.
        outdir: Directory to save assets into.
        timeout: Per-request timeout.

    Returns:
        List of asset URLs successfully fetched.
    """
    base = portal_url.split("?", 1)[0].rsplit("/", 1)[0]
    root = portal_url.split("/", 3)[:3]
    origin = "/".join(root) if len(root) == 3 else base

    fetched: list[str] = []
    assets_dir = outdir / "assets"
    for i, ref in enumerate(_ASSET_PATTERN.findall(html)[:_MAX_ASSETS]):
        if ref.startswith("http://") or ref.startswith("https://"):
            url = ref
        elif ref.startswith("/"):
            url = origin + ref
        else:
            url = f"{base}/{ref}"
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code != 200:
                continue
            assets_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", ref)[-80:]
            (assets_dir / f"{i:02d}_{safe_name}").write_bytes(
                resp.content[:_MAX_ASSET_BYTES]
            )
            fetched.append(url)
        except Exception as e:  # noqa: BLE001 — capture must not die on one asset
            logger.debug("Asset fetch failed for %s: %s", url, e)
    return fetched


def launch_portal_page(su: str | None, portal_url: str) -> list[str]:
    """Open the portal URL so its JS can fire the app deep link.

    Prefers the system captive-portal browser (network-bound to WiFi);
    falls back to a plain VIEW intent.

    Args:
        su: Root shell path, or None to try without root.
        portal_url: Redirected portal URL to open.

    Returns:
        List of human-readable launch attempt outcomes.
    """
    attempts = [
        f'am start -a android.intent.action.VIEW -d "{portal_url}" '
        f"{_CAPTIVE_PORTAL_PACKAGE}",
        f'am start -a android.intent.action.VIEW -d "{portal_url}"',
    ]
    outcomes: list[str] = []
    for cmd in attempts:
        if su is not None:
            rc, out = run_root(su, cmd)
        else:
            try:
                result = subprocess.run(
                    ["am", "start", "-a", "android.intent.action.VIEW", "-d", portal_url],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                rc, out = result.returncode, result.stdout + result.stderr
            except OSError as e:
                rc, out = 127, str(e)
        summary = out.strip().splitlines()[-1] if out.strip() else f"rc={rc}"
        outcomes.append(f"{cmd.split(chr(34))[1][:60]}... -> rc={rc} {summary}")
    return outcomes


def capture_logcat_intents(su: str, outdir: Path, keyword: str = "") -> int:
    """Dump a filtered logcat slice recording activity launches.

    Args:
        su: Root shell path.
        outdir: Directory to write ``intents.txt`` into.
        keyword: Extra substring to keep (e.g. the portal's name).

    Returns:
        Number of matching lines captured (0 if none/failed).
    """
    rc, out = run_root(su, "logcat -b all -d -v threadtime", timeout=60)
    if rc != 0 or not out:
        return 0
    pattern = _LOGCAT_KEEP_PATTERN
    if keyword:
        pattern = re.compile(
            _LOGCAT_KEEP_PATTERN.pattern + "|" + re.escape(keyword),
            re.IGNORECASE,
        )
    lines = [ln for ln in out.splitlines() if pattern.search(ln)]
    (outdir / "intents.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def dump_device_state(su: str, outdir: Path, package: str = "") -> None:
    """Save portal-relevant dumpsys output to the capture directory."""
    rc, out = run_root(su, "dumpsys activity activities | grep -iE 'topResumed'", timeout=30)
    (outdir / "foreground.txt").write_text(out or "(empty)\n", encoding="utf-8")

    if not package:
        return
    rc, out = run_root(su, f"dumpsys package {package}", timeout=30)
    keep = [
        ln
        for ln in out.splitlines()
        if re.search(r"Scheme|Authority|versionName|firstInstall|lastUpdate", ln)
    ]
    (outdir / "app_filters.txt").write_text(
        "\n".join(keep) + "\n", encoding="utf-8"
    )


def run_probe(
    outdir: Path,
    interface: str | None = None,
    wait: int = 25,
    launch: bool = True,
    probe_url: str = PROBE_URL,
    name: str = "costco",
    package: str = COSTCO_PACKAGE,
) -> dict[str, object]:
    """Run the full portal capture and return a summary dict.

    Args:
        outdir: Directory for captured artifacts (created if missing).
        interface: WiFi interface name (None = auto-detect).
        wait: Seconds to wait after launching the portal page before
            dumping logcat (deep links fire asynchronously).
        launch: Whether to am-start the portal URL and capture logcat.
        probe_url: Plain-HTTP URL used for portal detection.
        name: Portal label — used in the report title and as an extra
            logcat keyword.
        package: App package to dumpsys for intent filters (empty =
            skip).

    Returns:
        Summary dict (also written to ``outdir/summary.json``).
    """
    outdir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {
        "name": name,
        "outdir": str(outdir),
        "probe_url": probe_url,
    }

    try:
        session = create_wifi_session(interface)
    except InterfaceBindingError as e:
        summary["error"] = f"WiFi bind failed: {e}"
        _write_outputs(outdir, summary)
        return summary

    report = analyze_portal(
        probe_url=probe_url,
        save_html=True,
        html_path=str(outdir / "portal.html"),
        session=session,
    )
    (outdir / "portal_report.txt").write_text(report.to_text() + "\n", encoding="utf-8")
    summary["redirect_url"] = report.redirect_url
    summary["portal_host"] = report.portal_host
    summary["portal_error"] = report.error

    links: set[str] = set()
    html_path = outdir / "portal.html"
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8", errors="replace")
        links.update(extract_deep_links(html))
        if report.redirect_url:
            fetched = fetch_portal_assets(session, report.redirect_url, html, outdir)
            summary["assets_fetched"] = fetched
            for asset in sorted((outdir / "assets").glob("*")) if fetched else []:
                links.update(
                    extract_deep_links(asset.read_text(encoding="utf-8", errors="replace"))
                )
    summary["deep_links"] = sorted(links)

    su = find_su()
    summary["root"] = su is not None
    if launch and report.redirect_url:
        if su is not None:
            run_root(su, "logcat -b all -c")  # clear for a clean slice
        launch_outcomes = launch_portal_page(su, report.redirect_url)
        summary["launch_attempts"] = launch_outcomes
        logger.info("Waiting %ds for portal JS/deep link to fire", wait)
        time.sleep(wait)
        if su is not None:
            summary["intent_lines"] = capture_logcat_intents(
                su, outdir, keyword=name
            )
            dump_device_state(su, outdir, package=package)
        else:
            summary["intent_lines"] = 0
            summary["note"] = "no su — logcat capture skipped"
    elif launch and not report.redirect_url:
        summary["note"] = "no portal redirect — nothing to launch"

    summary["internet_ok"] = verify_internet_connectivity(session=session)
    _write_outputs(outdir, summary, name=name)
    return summary


def _write_outputs(
    outdir: Path, summary: dict[str, object], name: str = "portal"
) -> None:
    """Write summary.json and a markdown report for the capture."""
    (outdir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    lines = [f"# {name} Portal Capture", ""]
    for key, value in summary.items():
        if isinstance(value, list):
            lines.append(f"**{key}**:")
            lines.extend(f"- {v}" for v in value)
        else:
            lines.append(f"**{key}**: `{value}`")
    (outdir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the Costco portal probe.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description="Capture a captive portal flow on-device",
    )
    parser.add_argument(
        "--name",
        default="costco",
        help="Portal label — report title, logcat keyword, and default "
        "capture dir name (default: costco)",
    )
    parser.add_argument(
        "--package",
        default=COSTCO_PACKAGE,
        help=f"App package to dumpsys for intent filters "
        f"(default: {COSTCO_PACKAGE})",
    )
    parser.add_argument(
        "--no-package",
        action="store_true",
        help="Skip the app-package dumpsys (for portals without a "
        "known companion app)",
    )
    parser.add_argument(
        "--outdir",
        default=None,
        help="Capture directory (default: ~/storage/shared/"
        "<name>_capture/<ts>)",
    )
    parser.add_argument(
        "--interface",
        default=None,
        help="WiFi interface name (default: auto-detect)",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=25,
        help="Seconds to wait for the page's deep link to fire (default: 25)",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Skip opening the portal page and logcat capture",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the JSON summary to stdout as well",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.outdir is None:
        home = Path.home()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        args.outdir = str(
            home / "storage" / "shared" / f"{args.name}_capture" / f"capture_{stamp}"
        )

    summary = run_probe(
        outdir=Path(args.outdir),
        interface=args.interface,
        wait=args.wait,
        launch=not args.no_launch,
        name=args.name,
        package="" if args.no_package else args.package,
    )

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print((Path(args.outdir) / "report.md").read_text(encoding="utf-8"))

    return 1 if summary.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
