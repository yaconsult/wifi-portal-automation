"""Android/Termux entry point for MVwifiAuto.

Reuses the same portal-handling logic as the Linux/NetworkManager
version, but routes HTTP traffic over WiFi to bypass Android's
cellular-preferred policy routing.

Designed to be called from Tasker via ``Run Shell`` (non-root) or run
directly from the Termux command line::

    wpa-android --once
    wpa-android --once --verbose
    wpa-android --once --verbose --log-file /sdcard/mvwifi.log

Requires:
    - Termux with Python 3.11+
    - ``requests`` installed (``pip install requests``)
    - cmvwifi already connected (via Tasker's Net → Connect to WiFi)
"""

from __future__ import annotations

import argparse
import logging
import sys

from wifi_portal_automation.captive_portal import (
    CaptivePortalError,
    handle_cmvwifi_connection,
)
from wifi_portal_automation.wifi_binding import (
    InterfaceBindingError,
    InterfaceBoundAdapter,
    create_wifi_session,
)

logger = logging.getLogger("wifi_portal_automation.android")


def run_once(
    interface: str | None = None,
    verbose: bool = False,
    max_portal_attempts: int = 3,
) -> bool:
    """Run a single portal-handling cycle on Android.

    Assumes the phone is already associated with ``cmvwifi`` (handled
    by Tasker's WiFi Near profile + Connect to WiFi action).  This
    function creates a WiFi-bound HTTP session, detects the captive
    portal, accepts the terms, and verifies internet connectivity.

    Args:
        interface: WiFi interface name. If None, auto-detects (tries
            wlan0, wlan1, etc.). Default: None.
        verbose: Enable debug logging.
        max_portal_attempts: Maximum captive portal retry attempts.

    Returns:
        True if internet access was established.
    """
    try:
        session = create_wifi_session(interface)
    except InterfaceBindingError as e:
        logger.error("Cannot bind to WiFi interface: %s", e)
        return False

    adapter = session.get_adapter("http://")
    interface_name = (
        adapter.interface if isinstance(adapter, InterfaceBoundAdapter) else "?"
    )
    logger.info("Handling cmvwifi captive portal via %s", interface_name)
    return handle_cmvwifi_connection(
        max_attempts=max_portal_attempts,
        session=session,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the Android/Termux version.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description="Handle cmvwifi captive portal on Android (via Termux)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run portal handling once and exit (default)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging",
    )
    parser.add_argument(
        "--interface",
        default=None,
        help="WiFi interface name (default: auto-detect wlan0/wlan1)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum portal retry attempts (default: 3)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Write logs to a file (e.g. /sdcard/mvwifi.log)",
    )

    args = parser.parse_args(argv)

    # Configure logging (console + optional file)
    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_format = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    if args.log_file:
        try:
            file_handler = logging.FileHandler(args.log_file, mode="w")
            file_handler.setLevel(log_level)
            file_handler.setFormatter(log_format)
            root_logger.addHandler(file_handler)
        except OSError as e:
            print(f"Warning: cannot write to {args.log_file}: {e}")

    # CaptivePortalError propagates after the final retry — catch it
    # so the run still records its outcome line.  An uncaught
    # traceback leaves no "Run FAILED" marker, which troubleshooting
    # docs treat as "the process was killed" — a false diagnosis.
    try:
        success = run_once(
            interface=args.interface,
            verbose=args.verbose,
            max_portal_attempts=args.max_attempts,
        )
    except CaptivePortalError as e:
        logging.error("%s", e)
        logging.info("Run FAILED")
        return 1

    # The log file is overwritten each run (mode="w") and kept on
    # success, so its last line always records the outcome of the
    # most recent run — useful when debugging Tasker/plugin issues.
    logging.info(
        "Run %s", "completed successfully" if success else "FAILED"
    )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
