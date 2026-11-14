"""Captive portal handler for Costco WiFi (Juniper Mist).

Protocol captured on-device 2026-10-01 — see
``docs/costco-portal-capture.md`` and devlog Session 26 for the full
record.  Summary:

- The captive portal is hosted by **Juniper Mist**
  (``https://portal.gc1.mist.com/logon?...``) — a real internet host,
  reachable pre-auth via the walled garden.
- The visible ``singleAuthForm`` is a plain TOS-accept:
  checkbox ``tos=true``, submit ``auth_method=passphrase``, plus
  per-session hidden fields (``ap_mac``, ``client_mac``, ``wlan_id``,
  ``url``, ``direct``) echoed from the redirect URL — they must be
  parsed from the page, never hardcoded.
- A successful POST returns a redirect to ``https://www.costco.com/`` —
  which Android app-links into the Costco app.  **The app is not part
  of authentication**; it is merely the configured landing page.  The
  hidden ``submitSms``/``submitEmail`` forms in the same page are
  alternate auth methods (access codes) — if a future capture shows
  only those (no TOS form), the warehouse requires a code and this
  handler will refuse rather than guess.
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests import RequestException

from wifi_portal_automation.captive_portal import (
    DEFAULT_USER_AGENT,
    detect_captive_portal,
    verify_internet_connectivity,
)
from wifi_portal_automation.portal_analyzer import PortalForm, parse_forms

logger = logging.getLogger("wifi_portal_automation.costco_portal")

# Costco WiFi SSID — confirmed by on-site scan 2026-09-17
COSTCO_SSID = "Costco Member Wifi"

# Name of the submit button on the Mist TOS-accept form.  Other
# auth_method values seen in the page: smsCodeSubmit/emailCodeSubmit
# belong to the hidden access-code forms and require a real code.
TOS_SUBMIT_NAME = "auth_method"
TOS_SUBMIT_VALUE = "passphrase"


class CostcoPortalError(Exception):
    """Costco portal operation error."""

    pass


def _safe_url(url: str) -> str:
    """Strip the query string from a URL for logging.

    Captive-portal redirect URLs (Mist in particular) carry the
    client MAC and session tokens in the query — a location-linked
    identifier that shouldn't land in logs on shared storage.
    """
    return url.split("?", 1)[0]


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return response headers with credential-carrying ones removed."""
    sensitive = {"set-cookie", "cookie", "authorization", "www-authenticate"}
    return {
        k: v for k, v in headers.items() if k.lower() not in sensitive
    }


def find_tos_form(page_html: str) -> PortalForm | None:
    """Find the replayable TOS-accept form in the Mist portal page.

    Prefers the form whose submit button is ``auth_method`` (the
    ``singleAuthForm`` observed on-site); falls back to any form
    containing a ``tos`` checkbox.  Returns None when only access-code
    forms exist — that variant cannot be automated without a code.

    Args:
        page_html: Raw portal page HTML.

    Returns:
        The matching :class:`PortalForm`, or None.
    """
    forms = parse_forms(page_html)
    for form in forms:
        if any(s.name == TOS_SUBMIT_NAME for s in form.submits):
            return form
    for form in forms:
        if any(cb.name == "tos" for cb in form.checkboxes):
            return form
    return None


def build_post_data(form: PortalForm) -> dict[str, str]:
    """Build the POST body from a parsed TOS form.

    Includes every hidden field (HTML entities unescaped — Mist encodes
    ``&`` in the ``url`` field as ``&amp;``), the TOS checkbox, and the
    submit button name/value.

    Args:
        form: The form returned by :func:`find_tos_form`.

    Returns:
        POST data dict ready for ``requests``.
    """
    data: dict[str, str] = {}
    for hf in form.hiddens:
        if hf.name:
            data[hf.name] = html.unescape(hf.value)
    for cb in form.checkboxes:
        if cb.name:
            data[cb.name] = cb.value or "true"
    for sb in form.submits:
        if sb.name:
            data[sb.name] = sb.value
    return data


def _save_debug_page(debug_dir: Path | None, name: str, content: str) -> None:
    """Save a debug artifact (portal page or POST response info).

    Args:
        debug_dir: Directory for artifacts; None disables saving.
        name: File name to write.
        content: Text content.
    """
    if debug_dir is None:
        return
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / name).write_text(content, encoding="utf-8")
        logger.info("Saved debug artifact %s", debug_dir / name)
    except OSError as e:
        logger.warning("Could not save debug artifact: %s", e)


def accept_costco_terms(
    redirect_url: str,
    timeout: int = 10,
    session: requests.Session | None = None,
    debug_dir: Path | None = None,
) -> bool:
    """Accept Costco WiFi terms via the Mist portal form.

    Fetches the portal page at *redirect_url*, parses the TOS form,
    and POSTs it back — the same requests a browser makes when the
    user checks the box and taps the button.

    Args:
        redirect_url: Portal page URL from the captive-portal redirect.
        timeout: Request timeout in seconds.
        session: Optional requests session for interface binding.
        debug_dir: Directory for failure artifacts (portal HTML,
            POST response summary). None disables saving.

    Returns:
        True if internet access was verified after the POST.

    Raises:
        CostcoPortalError: If the page has no replayable TOS form or
            the request fails.
    """
    get_func = session.get if session is not None else requests.get
    post_func = session.post if session is not None else requests.post
    headers = {"User-Agent": DEFAULT_USER_AGENT}

    try:
        logger.info("Fetching portal page: %s", _safe_url(redirect_url))
        page = get_func(redirect_url, headers=headers, timeout=timeout)
        logger.info(
            "Portal page: status=%d, %d bytes, %d form(s)",
            page.status_code,
            len(page.text),
            len(parse_forms(page.text)),
        )

        form = find_tos_form(page.text)
        if form is None or not form.action:
            _save_debug_page(debug_dir, "portal_no_tos_form.html", page.text)
            raise CostcoPortalError(
                "No replayable TOS form found — portal may require an "
                "access code or member login at this warehouse "
                f"(page saved under {debug_dir})"
            )

        # The action attribute HTML-encodes & as &amp; — decode it or
        # the query string gains a bogus "amp;..." parameter.
        post_url = urljoin(redirect_url, html.unescape(form.action))
        post_data = build_post_data(form)
        logger.info("Posting TOS acceptance to %s", post_url)
        logger.info("POST fields: %s", sorted(post_data.keys()))

        post_headers = dict(headers)
        post_headers["Referer"] = redirect_url
        response = post_func(
            post_url,
            data=post_data,
            headers=post_headers,
            timeout=timeout,
            allow_redirects=True,
        )
        logger.info(
            "POST response: status=%d, landed on %s",
            response.status_code,
            response.url,
        )

        if response.status_code not in (200, 302, 303):
            _save_debug_page(
                debug_dir,
                "portal_post_rejected.txt",
                f"status={response.status_code}\n"
                f"url={_safe_url(response.url)}\n"
                f"headers={_safe_headers(response.headers)}\n",
            )
            logger.warning("Costco portal POST returned %d", response.status_code)
            return False

        time.sleep(1)
        ok = verify_internet_connectivity(session=session)
        logger.info("Internet verification after POST: %s", "OK" if ok else "FAILED")
        if not ok:
            _save_debug_page(
                debug_dir,
                "portal_verify_failed.txt",
                f"POST status={response.status_code}\nfinal_url={response.url}\n"
                f"internet verification failed\n",
            )
        return ok

    except RequestException as e:
        raise CostcoPortalError(f"Failed to accept Costco terms: {e}") from e


def handle_costco_connection(
    max_attempts: int = 3,
    attempt_delay: float = 2.0,
    session: requests.Session | None = None,
    debug_dir: Path | None = None,
) -> bool:
    """Handle full Costco WiFi connection including captive portal.

    1. Detects the captive portal and its redirect URL
    2. POSTs the TOS-accept form
    3. Verifies internet connectivity

    Args:
        max_attempts: Maximum number of captive portal attempts.
        attempt_delay: Delay between attempts in seconds.
        session: Optional requests session for interface binding.
        debug_dir: Directory for failure artifacts; None disables.

    Returns:
        True if successfully connected with internet access.
    """
    time.sleep(2)

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info("Portal detection attempt %d/%d", attempt, max_attempts)
            is_captive, redirect_url = detect_captive_portal(session=session)
            logger.info(
                "Detection: captive=%s, redirect=%s",
                is_captive,
                _safe_url(redirect_url) if redirect_url else "(none)",
            )
            if not is_captive or not redirect_url:
                # No portal detected — might already have internet
                if verify_internet_connectivity(session=session):
                    logger.info("No portal and internet OK — nothing to do")
                    return True
                if attempt < max_attempts:
                    time.sleep(attempt_delay)
                    continue
                return False

            if accept_costco_terms(
                redirect_url=redirect_url, session=session, debug_dir=debug_dir
            ):
                return True

            if attempt < max_attempts:
                time.sleep(attempt_delay)

        except CostcoPortalError:
            if attempt >= max_attempts:
                raise
            time.sleep(attempt_delay)

    return False


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the Costco portal handler.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description="Handle the Costco (Juniper Mist) captive portal",
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
        help="Write logs to a file",
    )
    parser.add_argument(
        "--debug-dir",
        default=None,
        help="Save failure artifacts (portal HTML, POST rejections) here",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.log_file:
        try:
            handler = logging.FileHandler(args.log_file, mode="w")
            handler.setFormatter(
                logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            )
            logging.getLogger().addHandler(handler)
        except OSError as e:
            print(f"Warning: cannot write to {args.log_file}: {e}")

    from wifi_portal_automation.wifi_binding import InterfaceBindingError, create_wifi_session

    try:
        session = create_wifi_session(args.interface)
    except InterfaceBindingError as e:
        logger.error("Cannot bind to WiFi interface: %s", e)
        return 1

    try:
        success = handle_costco_connection(
            max_attempts=args.max_attempts,
            session=session,
            debug_dir=Path(args.debug_dir) if args.debug_dir else None,
        )
    except CostcoPortalError as e:
        logger.error("%s", e)
        return 1

    logging.info("Run %s", "completed successfully" if success else "FAILED")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
