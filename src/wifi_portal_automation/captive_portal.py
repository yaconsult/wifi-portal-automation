"""Captive portal handler for Mountain View public WiFi (cmvwifi).

The portal sign-in host is *not* necessarily the default gateway.  The
captive portal redirects HTTP requests to a dynamic host (e.g.
``10.64.2.21:9997``) which may differ from the routing gateway
(``10.65.8.1``).  This module therefore extracts the portal host from the
redirect ``Location`` header rather than relying on ``ip route``.

All HTTP functions accept an optional ``session`` parameter (a
``requests.Session`` or the ``requests`` module itself) so that callers
can bind traffic to a specific network interface — essential on Android
where policy routing otherwise sends HTTP over cellular.
"""

import re
import subprocess
import time

import requests
from requests import ConnectionError as _RequestsConnectionError
from requests import RequestException as _RequestsRequestException
from requests import Timeout as _RequestsTimeout


class CaptivePortalError(Exception):
    """Captive portal operation error."""

    pass


# User-Agent required for cmvwifi captive portal
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

# Mountain View WiFi captive portal patterns
CMVWIFI_GATEWAY_PATTERN = re.compile(r"http://(\d+\.\d+\.\d+\.\d+)/")
CMVWIFI_LOGIN_URL = "/forms/guest_toued"

# Regex to extract host (IP or IP:port) from a redirect URL.
# Matches "http://10.64.2.21:9997/user/guest_tou.asp" -> "10.64.2.21:9997"
_PORTAL_HOST_PATTERN = re.compile(r"https?://([^/]+)")


def _get(
    session: requests.Session | None,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    allow_redirects: bool,
) -> requests.Response:
    """Issue a GET via *session* or the default requests module."""
    if session is not None:
        return session.get(
            url, headers=headers, timeout=timeout,
            allow_redirects=allow_redirects,
        )
    return requests.get(
        url, headers=headers, timeout=timeout,
        allow_redirects=allow_redirects,
    )


def _post(
    session: requests.Session | None,
    url: str,
    *,
    data: dict[str, str],
    headers: dict[str, str],
    timeout: float,
    allow_redirects: bool,
) -> requests.Response:
    """Issue a POST via *session* or the default requests module."""
    if session is not None:
        return session.post(
            url, data=data, headers=headers, timeout=timeout,
            allow_redirects=allow_redirects,
        )
    return requests.post(
        url, data=data, headers=headers, timeout=timeout,
        allow_redirects=allow_redirects,
    )


def get_default_gateway() -> str | None:
    """Get the default gateway IP address.

    Kept for diagnostics and backward compatibility.  The portal host
    used by :func:`accept_cmvwifi_terms` is now extracted from the
    redirect URL, not from the routing gateway.

    Returns:
        Gateway IP string or None if cannot determine.
    """
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Parse "default via 192.168.1.1 dev ..."
            match = re.search(r"default\s+via\s+(\d+\.\d+\.\d+\.\d+)", result.stdout)
            if match:
                return match.group(1)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def extract_portal_host(redirect_url: str) -> str | None:
    """Extract the host (IP or IP:port) from a portal redirect URL.

    Args:
        redirect_url: Full URL such as ``http://10.64.2.21:9997/user/guest_tou.asp``.

    Returns:
        Host string (e.g. ``10.64.2.21:9997``) or None if the URL is
        not a valid HTTP(S) URL.
    """
    match = _PORTAL_HOST_PATTERN.match(redirect_url)
    if match:
        return match.group(1)
    return None


def detect_portal_host(
    probe_url: str = "http://1.1.1.1/",
    timeout: int = 10,
    session: requests.Session | None = None,
) -> str | None:
    """Detect the captive portal host by following a probe redirect.

    Sends a GET to *probe_url* with redirects enabled.  When a captive
    portal is active it intercepts the request and redirects to the
    portal page; the host portion of the final URL is returned.

    Args:
        probe_url: URL to probe (any HTTP URL works — the portal
            intercepts all HTTP traffic).
        timeout: Request timeout in seconds.
        session: Optional requests session for interface binding.

    Returns:
        Portal host string (e.g. ``10.64.2.21:9997``) or None.
    """
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    try:
        response = _get(
            session,
            probe_url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        # If the final URL is the same as the probe URL, no redirect happened
        # (i.e. no captive portal).  Only return a host when we were redirected.
        if response.url == probe_url:
            return None
        return extract_portal_host(response.url)
    except _RequestsRequestException:
        return None


def detect_captive_portal(
    test_url: str = "http://detectportal.firefox.com/canonical.html",
    timeout: int = 10,
    session: requests.Session | None = None,
) -> tuple[bool, str | None]:
    """Detect if we're behind a captive portal.

    Uses the Firefox captive portal detection URL.

    Args:
        test_url: URL to test for captive portal detection.
        timeout: Request timeout in seconds.
        session: Optional requests session for interface binding.

    Returns:
        Tuple of (is_captive, redirect_url). redirect_url is the captive portal
        page if detected.
    """
    headers = {"User-Agent": DEFAULT_USER_AGENT}

    try:
        # Disable redirects to catch the portal redirect
        response = _get(
            session,
            test_url,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )

        # If we get a redirect (302, 307), we're likely behind a captive portal
        if response.status_code in (302, 303, 307):
            redirect_url = response.headers.get("Location", "")
            return True, redirect_url

        # Check if response content is the expected success response
        if response.status_code == 200:
            content = response.text
            # Firefox success.txt is exactly "success\n" — a substring
            # match also fires on portal HTML with jQuery `success:`
            # handlers and reports a captive network as connected.
            if content.strip().lower() == "success" or "<" not in content:
                return False, None
            # If we got HTML, might be a captive portal
            if "<html" in content.lower():
                return True, test_url

        return False, None

    except _RequestsTimeout:
        # Timeout might indicate captive portal blocking us
        return True, None
    except _RequestsConnectionError:
        # Connection error might indicate no connectivity or portal
        return True, None
    except _RequestsRequestException:
        return True, None


def accept_cmvwifi_terms(
    portal_host: str | None = None,
    timeout: int = 10,
    session: requests.Session | None = None,
) -> bool:
    """Accept Mountain View WiFi terms of service.

    Posts to the captive portal form to accept terms and gain internet
    access.

    Args:
        portal_host: Portal host (IP or IP:port). If None, the host is
            auto-detected by following a probe redirect.
        timeout: Request timeout in seconds.
        session: Optional requests session for interface binding.

    Returns:
        True if terms acceptance was successful.
    """
    if portal_host is None:
        portal_host = detect_portal_host(timeout=timeout, session=session)
        if portal_host is None:
            raise CaptivePortalError("Could not determine portal host")

    login_url = f"http://{portal_host}{CMVWIFI_LOGIN_URL}"

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": f"http://{portal_host}/",
    }

    # Form data based on the micropython captive_portal.py reference
    post_data = {
        "origurl": "http://www.google.com",
        "ok": "Accept and Continue",
    }

    try:
        response = _post(
            session,
            login_url,
            data=post_data,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )

        # Check for success - usually returns 200 or redirects to success page
        if response.status_code in (200, 302, 303):
            # Verify we now have internet
            time.sleep(1)  # Brief wait for connection to settle
            return verify_internet_connectivity(session=session)

        return False

    except _RequestsRequestException as e:
        raise CaptivePortalError(f"Failed to accept terms: {e}") from e


def verify_internet_connectivity(
    test_url: str = "http://detectportal.firefox.com/success.txt",
    timeout: int = 5,
    session: requests.Session | None = None,
) -> bool:
    """Verify we have actual internet connectivity.

    Args:
        test_url: URL to test connectivity.
        timeout: Request timeout.
        session: Optional requests session for interface binding.

    Returns:
        True if internet is accessible.
    """
    headers = {"User-Agent": DEFAULT_USER_AGENT}

    try:
        response = _get(
            session,
            test_url,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
        # Should get 200 with "success" content if truly connected
        return (
            response.status_code == 200
            and response.text.strip().lower() == "success"
        )
    except _RequestsRequestException:
        return False


def handle_cmvwifi_connection(
    max_attempts: int = 3,
    attempt_delay: float = 2.0,
    session: requests.Session | None = None,
) -> bool:
    """Handle full cmvwifi connection including captive portal.

    This function:
    1. Waits for connection to cmvwifi to be established
    2. Detects captive portal
    3. Accepts terms of service
    4. Verifies internet connectivity

    Args:
        max_attempts: Maximum number of captive portal attempts.
        attempt_delay: Delay between attempts in seconds.
        session: Optional requests session for interface binding.

    Returns:
        True if successfully connected with internet access.
    """
    # Give NetworkManager a moment to fully connect
    time.sleep(2)

    for attempt in range(1, max_attempts + 1):
        try:
            # Check if we need to handle captive portal
            is_captive, redirect_url = detect_captive_portal(session=session)

            if not is_captive:
                # Already have internet or no portal needed
                if verify_internet_connectivity(session=session):
                    return True
                # No portal but no internet - might need more time
                if attempt < max_attempts:
                    time.sleep(attempt_delay)
                    continue
                return False

            # We have a captive portal - extract host from redirect URL
            portal_host = None
            if redirect_url:
                portal_host = extract_portal_host(redirect_url)

            # If we couldn't extract host from redirect, probe for it
            if portal_host is None:
                portal_host = detect_portal_host(session=session)

            if portal_host and accept_cmvwifi_terms(portal_host=portal_host, session=session):
                return True

            # Failed this attempt, wait and retry
            if attempt < max_attempts:
                time.sleep(attempt_delay)

        except CaptivePortalError:
            if attempt >= max_attempts:
                raise
            time.sleep(attempt_delay)

    return False
