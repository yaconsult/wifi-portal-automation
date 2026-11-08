"""Generic captive portal analyzer.

A recon tool for capturing the structure of any captive portal — form
actions, hidden fields, submit buttons, redirect URLs.  Originally
written as ``analyze_costco_portal.py`` for the Costco WiFi portal, now
refactored into a reusable, testable module that works for any portal.

Usage::

    from wifi_portal_automation.portal_analyzer import analyze_portal

    report = analyze_portal()
    print(report.to_text())

Or via CLI::

    wpa-analyze-portal
    wpa-analyze-portal --probe-url http://1.1.1.1/ --output /tmp/report.txt

The analyzer does NOT attempt to accept the portal — it only captures
and reports the portal structure so you can implement a handler.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import requests
from requests import RequestException

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("wifi_portal_automation.portal_analyzer")

# Regexes for HTML form parsing (tolerant of attribute ordering and quoting)
# Captures the opening <form ...> tag and inner content separately
_FORM_PATTERN = re.compile(r"(<form[^>]*>)(.*?)</form>", re.DOTALL | re.IGNORECASE)
_ACTION_PATTERN = re.compile(r'action=["\']([^"\']+)["\']', re.IGNORECASE)
_METHOD_PATTERN = re.compile(r'method=["\']([^"\']+)["\']', re.IGNORECASE)
_CHECKBOX_PATTERN = re.compile(r'<input[^>]*type=["\']checkbox["\'][^>]*>', re.IGNORECASE)
_SUBMIT_PATTERN = re.compile(r'<input[^>]*type=["\']submit["\'][^>]*>', re.IGNORECASE)
# <button> elements act as submit buttons unless type="button"/"reset"
# (the Mist portal uses <button type='submit'> — an input-only pattern
# would miss it entirely)
_BUTTON_PATTERN = re.compile(r"<button[^>]*>", re.IGNORECASE)
_BUTTON_NONSUBMIT_PATTERN = re.compile(r'type=["\'](?:button|reset)["\']', re.IGNORECASE)
_HIDDEN_PATTERN = re.compile(r'<input[^>]*type=["\']hidden["\'][^>]*>', re.IGNORECASE)
_NAME_PATTERN = re.compile(r'name=["\']([^"\']+)["\']', re.IGNORECASE)
_VALUE_PATTERN = re.compile(r'value=["\']([^"\']*)["\']', re.IGNORECASE)


@dataclass
class FormField:
    """A single form field (checkbox, submit, or hidden)."""

    field_type: str  # "checkbox", "submit", "hidden"
    name: str = ""
    value: str = ""


@dataclass
class PortalForm:
    """A parsed HTML form from the portal page."""

    action: str = ""
    method: str = ""
    fields: list[FormField] = field(default_factory=list)

    @property
    def checkboxes(self) -> list[FormField]:
        """Checkbox fields in this form."""
        return [f for f in self.fields if f.field_type == "checkbox"]

    @property
    def submits(self) -> list[FormField]:
        """Submit buttons in this form."""
        return [f for f in self.fields if f.field_type == "submit"]

    @property
    def hiddens(self) -> list[FormField]:
        """Hidden fields in this form."""
        return [f for f in self.fields if f.field_type == "hidden"]


@dataclass
class PortalReport:
    """Complete analysis report for a captive portal."""

    probe_url: str = ""
    response_code: int = 0
    redirect_url: str = ""
    portal_host: str = ""
    portal_page_status: int = 0
    forms: list[PortalForm] = field(default_factory=list)
    html_saved: bool = False
    html_path: str = ""
    error: str = ""

    def to_text(self) -> str:
        """Format the report as human-readable text."""
        lines: list[str] = ["=== Captive Portal Analysis ===", ""]

        if self.error:
            lines.append(f"Error: {self.error}")
            return "\n".join(lines)

        lines.append(f"Probe URL: {self.probe_url}")
        lines.append(f"Response Code: {self.response_code}")

        if self.redirect_url:
            lines.append(f"Redirect Location: {self.redirect_url}")
            lines.append(f"Portal Host: {self.portal_host}")
            lines.append(f"Portal Page Status: {self.portal_page_status}")
            lines.append(f"Found {len(self.forms)} form(s)")

            for i, form in enumerate(self.forms):
                lines.append("")
                lines.append(f"--- Form {i + 1} ---")
                lines.append(f"Action: {form.action}")
                lines.append(f"Method: {form.method}")

                if form.checkboxes:
                    lines.append(f"Checkboxes found: {len(form.checkboxes)}")
                    for cb in form.checkboxes:
                        lines.append(f"  Checkbox name: {cb.name}")

                if form.submits:
                    lines.append(f"Submit buttons: {len(form.submits)}")
                    for sb in form.submits:
                        lines.append(f"  Button: name={sb.name}, value={sb.value}")

                if form.hiddens:
                    lines.append(f"Hidden fields: {len(form.hiddens)}")
                    for hf in form.hiddens:
                        lines.append(f"  Hidden: {hf.name}={hf.value}")

            if self.html_saved:
                lines.append("")
                lines.append(f"Full HTML saved to: {self.html_path}")
        else:
            lines.append("No redirect detected - may already be connected to internet")

        return "\n".join(lines)


def _extract_name(tag: str) -> str:
    """Extract the name attribute from an HTML tag."""
    match = _NAME_PATTERN.search(tag)
    return match.group(1) if match else ""


def _extract_value(tag: str) -> str:
    """Extract the value attribute from an HTML tag."""
    match = _VALUE_PATTERN.search(tag)
    return match.group(1) if match else ""


def parse_forms(html: str) -> list[PortalForm]:
    """Parse all <form> elements from HTML.

    Args:
        html: HTML page content.

    Returns:
        List of parsed PortalForm objects.
    """
    forms: list[PortalForm] = []
    for opening_tag, inner_html in _FORM_PATTERN.findall(html):
        form = PortalForm()

        action_match = _ACTION_PATTERN.search(opening_tag)
        if action_match:
            form.action = action_match.group(1)

        method_match = _METHOD_PATTERN.search(opening_tag)
        if method_match:
            form.method = method_match.group(1)

        for cb_tag in _CHECKBOX_PATTERN.findall(inner_html):
            form.fields.append(
                FormField(
                    field_type="checkbox",
                    name=_extract_name(cb_tag),
                    value=_extract_value(cb_tag),
                )
            )

        submit_tags = _SUBMIT_PATTERN.findall(inner_html)
        submit_tags += [
            tag
            for tag in _BUTTON_PATTERN.findall(inner_html)
            if not _BUTTON_NONSUBMIT_PATTERN.search(tag)
        ]
        for sb_tag in submit_tags:
            form.fields.append(
                FormField(
                    field_type="submit",
                    name=_extract_name(sb_tag),
                    value=_extract_value(sb_tag),
                )
            )

        for hf_tag in _HIDDEN_PATTERN.findall(inner_html):
            form.fields.append(
                FormField(
                    field_type="hidden",
                    name=_extract_name(hf_tag),
                    value=_extract_value(hf_tag),
                )
            )

        forms.append(form)

    return forms


def extract_host_from_url(url: str) -> str:
    """Extract the host (IP or IP:port) from a URL.

    Args:
        url: Full URL like ``http://10.64.2.21:9997/login``.

    Returns:
        Host string, or empty string if URL is invalid.
    """
    if "://" in url:
        after_scheme = url.split("://", 1)[1]
        return after_scheme.split("/", 1)[0]
    return ""


def analyze_portal(
    probe_url: str = "http://detectportal.firefox.com/canonical.html",
    timeout: int = 5,
    save_html: bool = False,
    html_path: str = "/tmp/portal.html",
    session: requests.Session | None = None,
    http_get: Callable[..., requests.Response] | None = None,
) -> PortalReport:
    """Analyze a captive portal's structure.

    Probes *probe_url* with redirects disabled.  If a redirect is
    detected, fetches the portal page and parses its forms.

    Args:
        probe_url: URL to probe for portal detection.
        timeout: Request timeout in seconds.
        save_html: If True, save the portal page HTML to *html_path*.
        html_path: Path to save HTML if save_html is True.
        session: Optional requests session for interface binding.
        http_get: Optional callable to override requests.get (for testing).

    Returns:
        PortalReport with the portal structure.
    """
    report = PortalReport(probe_url=probe_url)

    get_func = http_get
    if get_func is None:
        get_func = session.get if session is not None else requests.get

    try:
        response = get_func(
            probe_url,
            allow_redirects=False,
            timeout=timeout,
        )
        report.response_code = response.status_code

        if response.status_code not in (302, 303, 307):
            return report

        location = response.headers.get("Location", "")
        report.redirect_url = location
        report.portal_host = extract_host_from_url(location)

        # Fetch the portal page
        portal_response = get_func(location, timeout=timeout)
        report.portal_page_status = portal_response.status_code
        report.forms = parse_forms(portal_response.text)

        if save_html:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(portal_response.text)
            report.html_saved = True
            report.html_path = html_path

    except RequestException as e:
        report.error = str(e)
    except OSError as e:
        report.error = f"Failed to save HTML: {e}"

    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the portal analyzer.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        0 on success, 1 on error.
    """
    parser = argparse.ArgumentParser(
        description="Analyze a captive portal's structure",
    )
    parser.add_argument(
        "--probe-url",
        default="http://detectportal.firefox.com/canonical.html",
        help="URL to probe for portal detection",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Request timeout in seconds (default: 5)",
    )
    parser.add_argument(
        "--save-html",
        action="store_true",
        help="Save the portal page HTML to a file",
    )
    parser.add_argument(
        "--html-path",
        default="/tmp/portal.html",
        help="Where to save HTML with --save-html (default: /tmp/portal.html). "
        "On Termux use e.g. ~/storage/shared/portal.html since /tmp is not writable",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="-",
        help="Output file (default: stdout)",
    )
    parser.add_argument(
        "--interface",
        help="Network interface to bind to (e.g. wlan0)",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    session = None
    if args.interface:
        from wifi_portal_automation.wifi_binding import create_wifi_session

        session = create_wifi_session(args.interface)

    report = analyze_portal(
        probe_url=args.probe_url,
        timeout=args.timeout,
        save_html=args.save_html,
        html_path=args.html_path,
        session=session,
    )

    text = report.to_text()

    if args.output == "-":
        print(text)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"Report written to {args.output}")

    return 1 if report.error else 0


if __name__ == "__main__":
    sys.exit(main())
