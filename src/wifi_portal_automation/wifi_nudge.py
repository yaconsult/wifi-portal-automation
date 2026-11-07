"""Periodic auto-join nudge for WiFi networks Android deprioritizes.

cmvwifi's captive portal makes Android's network validator report
"no internet" on every join.  That, plus historical DHCP/association
failures, poisons the network's selection score — observed on-device
as ``CMD_UNWANTED_NETWORK``, ``numConsecutiveConnectionFailure``, and
a BSSID blocklist entry.  Android then backs off auto-join, and
screen-off scan throttling means the next retry can be 15-30 minutes
out even though "Auto-connect" is enabled.

This module is the nudge that bypasses that backoff: run periodically
(from a Tasker Time profile via the ``cmvwifi_nudge`` Termux wrapper),
it checks whether the target SSID is visible but unassociated and, if
so, explicitly requests the connection through the rooted ``cmd wifi``
interface.  The existing WiFi Connected profile then handles the
captive portal as usual.

Behaviour:

- Already on a target SSID        -> no-op
- Connected to a fallback SSID    -> scan; if a preferred target is
                                     visible, promote onto it
                                     (``connect-network``); otherwise
                                     no-op and stay
- Connected to a different SSID   -> no-op (never disrupts a working
                                     connection)
- WiFi disabled                   -> no-op (the user may have turned
                                     it off deliberately)
- Target absent from scan         -> no-op
- Target visible + a ``--defer-to``
  SSID also visible               -> no-op (``deferred`` — the
                                     preferred network should win;
                                     avoids two nudge tasks racing
                                     connect requests)
- Target visible, not connected   -> ``cmd wifi connect-network``

Fallback SSIDs are never joined by this run — they only mark
connections the nudge may promote away from.  Joining a fallback is
a separate invocation's job (``--ssid <fallback> --autojoin-disabled``),
so an entire fallback class can be toggled in Tasker by disabling its
profile without affecting preferred-network handling.

CLI::

    mvwifi-nudge
    mvwifi-nudge --preferred --fallback xfinitywifi
    mvwifi-nudge --ssid xfinitywifi --autojoin-disabled \
        --defer-to-preferred
    mvwifi-nudge --dry-run --json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shlex
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from wifi_portal_automation.config import DEFAULT_CONFIG, resolve_config
from wifi_portal_automation.root_shell import find_su, run_root

logger = logging.getLogger("wifi_portal_automation.wifi_nudge")

# Fallback default when neither --ssid nor --preferred is given.
DEFAULT_SSIDS = list(DEFAULT_CONFIG.preferred_open[:1])

# Every network that should beat a fallback join when visible.  These
# are the built-in example defaults — override with --config to point
# at your own networks.toml.  Kept as module constants (not Tasker
# args) because Termux's Arguments field is a single space-separated
# string — multi-word SSIDs like "Costco Member Wifi" would tokenize
# incorrectly.  Referenced via --defer-to-preferred / --preferred.
PREFERRED_SSIDS = list(DEFAULT_CONFIG.preferred)
PREFERRED_OPEN_SSIDS = list(DEFAULT_CONFIG.preferred_open)

# `cmd wifi status` emits lines like:
#   Wifi is enabled
#   Wifi is connected to "cmvwifi"
#   Wifi is not connected
_STATUS_ENABLED = re.compile(r"Wifi is enabled", re.IGNORECASE)
_STATUS_CONNECTED = re.compile(r'Wifi is connected to "([^"]+)"')

# `cmd wifi list-scan-results` emits a fixed-width table:
#     BSSID              Frequency      RSSI  Age(sec)  SSID   Flags
#   aa:bb:cc:dd:ee:ff       5540        -63    148.587  cmvwifi [ESS]
# The SSID column may be empty (hidden networks) or contain spaces;
# the Flags column always starts with '['.
_SCAN_LINE = re.compile(
    r"^\s*(?P<bssid>(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\s+"
    r"(?P<freq>\d+)\s+"
    r"(?P<rssi>-?\d+)\s+"
    r"(?P<age>[\d.]+)\s+"
    r"(?P<tail>.*?)\s*$"
)


@dataclass
class WifiStatus:
    """Parsed ``cmd wifi status`` output."""

    enabled: bool
    connected_ssid: str | None


@dataclass
class ScanResult:
    """One row of ``cmd wifi list-scan-results``."""

    bssid: str
    frequency: int
    rssi: int
    ssid: str  # empty for hidden networks


@dataclass
class NudgeReport:
    """Outcome of a nudge run (also the --json/--markdown payload)."""

    action: str
    """Terminal state: already_connected, connected_elsewhere,
    fallback_stay, wifi_disabled, target_absent, deferred,
    connect_requested, promoted, dry_run, no_root, status_failed,
    scan_failed, connect_failed, or throttled."""

    ok: bool
    wifi_enabled: bool = False
    connected_ssid: str | None = None
    targets: list[str] = field(default_factory=list)
    visible_targets: list[str] = field(default_factory=list)
    connect_rc: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable view of the report."""
        return asdict(self)


def parse_wifi_status(text: str) -> WifiStatus:
    """Parse ``cmd wifi status`` output.

    Args:
        text: Raw stdout of ``cmd wifi status``.

    Returns:
        A :class:`WifiStatus` with the enabled flag and the
        currently-connected SSID (None when disconnected).
    """
    enabled = False
    connected: str | None = None
    for line in text.splitlines():
        if _STATUS_ENABLED.search(line):
            enabled = True
        match = _STATUS_CONNECTED.search(line)
        if match:
            connected = match.group(1)
    return WifiStatus(enabled=enabled, connected_ssid=connected)


def parse_scan_results(text: str) -> list[ScanResult]:
    """Parse ``cmd wifi list-scan-results`` output.

    Args:
        text: Raw stdout of ``cmd wifi list-scan-results``.

    Returns:
        A list of :class:`ScanResult`, one per visible BSSID.
    """
    results: list[ScanResult] = []
    for line in text.splitlines():
        match = _SCAN_LINE.match(line)
        if not match:
            continue
        # The SSID column ends where the Flags column's '[' begins.
        ssid = match.group("tail").split("[", 1)[0].strip()
        results.append(
            ScanResult(
                bssid=match.group("bssid"),
                frequency=int(match.group("freq")),
                rssi=int(match.group("rssi")),
                ssid=ssid,
            )
        )
    return results


def _report(
    action: str,
    ok: bool,
    status: WifiStatus | None,
    targets: list[str],
    visible_targets: list[str] | None = None,
    connect_rc: int | None = None,
) -> NudgeReport:
    """Build a NudgeReport, carrying status fields when available."""
    return NudgeReport(
        action=action,
        ok=ok,
        wifi_enabled=status.enabled if status else False,
        connected_ssid=status.connected_ssid if status else None,
        targets=list(targets),
        visible_targets=list(visible_targets or []),
        connect_rc=connect_rc,
    )


def _read_last_scan(state_file: Path) -> float | None:
    """Return the epoch seconds of the last recorded scan, or None."""
    try:
        return float(state_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _write_last_scan(state_file: Path) -> None:
    """Record the current time as the last scan timestamp."""
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(str(time.time()))
    except OSError as exc:
        logger.warning("cannot write state file %s: %s", state_file, exc)


def _scan(su: str, settle: float) -> tuple[int, str]:
    """Trigger a scan and return ``list-scan-results`` output."""
    rc, out = run_root(su, "cmd wifi start-scan")
    if rc != 0:
        logger.warning("start-scan failed (rc=%s): %s", rc, out.strip())
    time.sleep(settle)
    return run_root(su, "cmd wifi list-scan-results")


def _connect(su: str, ssid: str, autojoin_disabled: bool) -> int:
    """Issue ``cmd wifi connect-network`` for *ssid*; returns the rc.

    ``autojoin_disabled`` adds ``-d``: the network joins now but the
    saved config is marked so Android never self-joins it later —
    the nudge stays the only path onto the network.
    """
    flag = " -d" if autojoin_disabled else ""
    rc, out = run_root(
        su, f"cmd wifi connect-network {shlex.quote(ssid)} open{flag}"
    )
    logger.info("connect-network %s -> rc=%s %s", ssid, rc, out.strip())
    return rc


def run_nudge(
    su: str,
    targets: list[str],
    fallbacks: list[str] | None = None,
    defer_to: list[str] | None = None,
    settle: float = 3.0,
    dry_run: bool = False,
    autojoin_disabled: bool = False,
    min_interval: float = 0.0,
    state_file: Path | None = None,
) -> NudgeReport:
    """Check WiFi state and request a join if a target SSID is visible.

    Args:
        su: Root shell path from :func:`find_su`.
        targets: Preferred SSIDs worth nudging (first visible one wins).
        fallbacks: SSIDs that count as "promotable" connections — when
            connected to one, the nudge scans and moves to a visible
            preferred target.  Never joined by this run.
        defer_to: SSIDs that outrank this run's targets — while
            disconnected, if any defer_to SSID is visible alongside a
            target, no connect is issued (``deferred``) so the
            preferred nudge / Android selector can take it.  Prevents
            two sibling nudge tasks from racing connect requests.
        settle: Seconds to wait between ``start-scan`` and reading
            ``list-scan-results``.
        dry_run: Report what would happen without issuing
            ``connect-network``.
        autojoin_disabled: Pass ``-d`` to ``connect-network`` so the
            joined network's saved config keeps auto-join off —
            Android cannot spontaneously hop onto it later.
        min_interval: Minimum seconds between real scans.  When a
            *state_file* records a scan newer than this, the run
            exits ``throttled`` without scanning — lets frequent
            triggers (e.g. a Display Unlocked profile) share the
            task without thrashing the radio.  0 disables.
        state_file: Timestamp file for *min_interval*.  Written on
            every real scan — but only read and written by runs
            that pass *min_interval*, so throttled callers share a
            window with each other while unthrottled callers (the
            periodic nudges) always scan.

    Returns:
        A :class:`NudgeReport` describing what was decided and done.
    """
    fallbacks = fallbacks or []
    defer_to = defer_to or []
    rc, out = run_root(su, "cmd wifi status")
    if rc != 0:
        logger.error("cmd wifi status failed (rc=%s): %s", rc, out.strip())
        return _report("status_failed", False, None, targets)
    status = parse_wifi_status(out)
    logger.info(
        "WiFi status: enabled=%s connected=%s", status.enabled, status.connected_ssid
    )

    if not status.enabled:
        return _report("wifi_disabled", True, status, targets)
    if status.connected_ssid in targets:
        return _report("already_connected", True, status, targets)
    on_fallback = status.connected_ssid in fallbacks
    if status.connected_ssid is not None and not on_fallback:
        # Never pull the device off a working connection.
        return _report("connected_elsewhere", True, status, targets)

    if min_interval > 0 and state_file is not None:
        last = _read_last_scan(state_file)
        if last is not None and time.time() - last < min_interval:
            logger.info(
                "throttled: last scan %.1fs ago (< %ss)",
                time.time() - last, min_interval,
            )
            return _report("throttled", True, status, targets)
        _write_last_scan(state_file)

    rc, out = _scan(su, settle)
    if rc != 0:
        logger.error("list-scan-results failed (rc=%s): %s", rc, out.strip())
        return _report("scan_failed", False, status, targets)

    visible = {r.ssid for r in parse_scan_results(out)}
    hits = [t for t in targets if t in visible]
    logger.info("scan: %d targets visible: %s", len(hits), hits or "none")
    if on_fallback:
        if not hits:
            return _report("fallback_stay", True, status, targets)
        action = "dry_run" if dry_run else "promoted"
    else:
        if not hits:
            return _report("target_absent", True, status, targets)
        defer_hits = [
            d for d in defer_to if d in visible and d not in targets
        ]
        if defer_hits:
            logger.info(
                "preferred network visible, deferring: %s", defer_hits
            )
            return _report(
                "deferred", True, status, targets,
                visible_targets=defer_hits,
            )
        action = "dry_run" if dry_run else "connect_requested"

    if dry_run:
        return _report("dry_run", True, status, targets, visible_targets=hits)

    rc = _connect(su, hits[0], autojoin_disabled and not on_fallback)
    if rc != 0:
        return _report(
            "connect_failed", False, status, targets,
            visible_targets=hits, connect_rc=rc,
        )
    return _report(
        action, True, status, targets,
        visible_targets=hits, connect_rc=rc,
    )


def format_markdown(report: NudgeReport) -> str:
    """Render a :class:`NudgeReport` as a small Markdown section."""
    lines = [
        "## WiFi Nudge",
        "",
        f"- **action:** {report.action}",
        f"- **ok:** {report.ok}",
        f"- **wifi enabled:** {report.wifi_enabled}",
        f"- **connected ssid:** {report.connected_ssid or '-'}",
        f"- **targets:** {', '.join(report.targets)}",
        f"- **visible targets:** {', '.join(report.visible_targets) or '-'}",
    ]
    if report.connect_rc is not None:
        lines.append(f"- **connect rc:** {report.connect_rc}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        0 for any clean outcome (including no-ops), 2 when root is
        unavailable, 1 when a wifi command itself failed.
    """
    parser = argparse.ArgumentParser(
        description="Nudge Android to join a visible target WiFi network",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="networks.toml overriding the built-in example SSID lists "
        "(preferred / preferred_open / fallback)",
    )
    parser.add_argument(
        "--ssid",
        action="append",
        dest="ssids",
        help="Preferred target SSID (repeatable; default: cmvwifi)",
    )
    parser.add_argument(
        "--preferred",
        action="store_true",
        help="Also target every SSID in PREFERRED_OPEN_SSIDS — shorthand "
        "covering multi-word SSIDs which can't be passed as arguments",
    )
    parser.add_argument(
        "--fallback",
        action="append",
        dest="fallbacks",
        help="Fallback SSID this run may promote away from "
        "(repeatable; never joined by this run)",
    )
    parser.add_argument(
        "--defer-to",
        action="append",
        dest="defer_to",
        help="Preferred SSID to yield to: if visible while disconnected, "
        "this run does nothing (repeatable)",
    )
    parser.add_argument(
        "--defer-to-preferred",
        action="store_true",
        help="Defer to every SSID in PREFERRED_SSIDS — shorthand that "
        "covers multi-word SSIDs which can't be passed as arguments",
    )
    parser.add_argument(
        "--autojoin-disabled",
        action="store_true",
        help="Join targets with -d so Android never self-joins them",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=3.0,
        metavar="SECS",
        help="Seconds to wait after start-scan (default: 3)",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=0.0,
        metavar="SECS",
        help="Skip the scan if a previous run scanned within this many "
        "seconds (default: 0 = no throttle).  For frequent triggers "
        "like a Display Unlocked profile",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Timestamp file for --min-interval (default: "
        "~/.cache/wifi_portal_automation/last_scan)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the decision without issuing connect-network",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument(
        "--markdown", action="store_true", help="Print Markdown report"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose logging"
    )
    parser.add_argument(
        "--log-file", help="Also append log output to this file"
    )
    args = parser.parse_args(argv)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    config = resolve_config(args.config)

    targets = list(
        args.ssids or config.preferred_open[:1] or DEFAULT_SSIDS
    )
    if args.preferred:
        targets += [s for s in config.preferred_open if s not in targets]
    defer_to = list(args.defer_to or [])
    if args.defer_to_preferred:
        defer_to += [s for s in config.preferred if s not in defer_to]
    su = find_su()
    if su is None:
        report = _report("no_root", False, None, targets)
        logger.error("no usable root shell found")
    else:
        report = run_nudge(
            su,
            targets,
            fallbacks=args.fallbacks,
            defer_to=defer_to,
            settle=args.settle,
            dry_run=args.dry_run,
            autojoin_disabled=args.autojoin_disabled,
            min_interval=args.min_interval,
            state_file=(
                args.state_file
                or Path.home() / ".cache" / "wifi_portal_automation" / "last_scan"
            ),
        )

    # The resolved action lands in the log file too — promoted /
    # fallback_stay would otherwise be invisible without --json.
    logger.info("nudge result: action=%s ok=%s", report.action, report.ok)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    elif args.markdown:
        print(format_markdown(report))

    if report.action == "no_root":
        return 2
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
