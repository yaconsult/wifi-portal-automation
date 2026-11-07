"""Shared root-shell helpers for on-device modules.

``cmd wifi``, ``settings``, ``dumpsys`` slices and similar commands
used by the Android/Termux flows require root.  The ``su`` binary
lives in different places depending on the Magisk install, so every
caller should go through :func:`find_su` rather than hardcoding a
path, and through :func:`run_root` for execution so timeouts and
missing binaries degrade gracefully.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("wifi_portal_automation.root_shell")

# Candidate locations for a root shell (Magisk hides su in different
# places depending on the install).
_SU_CANDIDATES = ["su", "/system/bin/su", "/system/xbin/su", "/sbin/su", "/su/bin/su"]


def find_su() -> str | None:
    """Locate a usable root shell binary.

    Returns:
        Path/name of a working ``su``, or None if unavailable.
    """
    for candidate in _SU_CANDIDATES:
        try:
            result = subprocess.run(
                [candidate, "-c", "id -u"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip() == "0":
            logger.info("Using root shell: %s", candidate)
            return candidate
    return None


def run_root(su: str, command: str, timeout: int = 30) -> tuple[int, str]:
    """Run *command* under ``su`` and return (exit code, stdout).

    Args:
        su: Root shell path from :func:`find_su`.
        command: Shell command to execute as root.
        timeout: Subprocess timeout in seconds.

    Returns:
        Tuple of (return code, combined stdout+stderr text).
    """
    try:
        result = subprocess.run(
            [su, "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
