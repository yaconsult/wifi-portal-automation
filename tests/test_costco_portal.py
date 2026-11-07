"""Tests for the Costco (Juniper Mist) captive portal handler."""

from unittest.mock import MagicMock, patch

import pytest
from requests import RequestException

from wifi_portal_automation.costco_portal import (
    COSTCO_SSID,
    CostcoPortalError,
    accept_costco_terms,
    build_post_data,
    find_tos_form,
    handle_costco_connection,
    main,
)

# Minimal replica of the captured Mist portal page (2026-10-01):
# the visible singleAuthForm plus the hidden SMS/email variants.
PORTAL_HTML = """
<html><body>
<form name='submitSms' class='hide' action='logon?ap_mac=aa&x=1'>
  <input type="hidden" name="ap_mac" value="aa" />
  <input type='text' name='accessCode'>
  <button name='smsCodeSubmit' value='submitSmsCode' type='submit'>
</form>
<form name=singleAuthForm action='logon?ap_mac=aa&amp;client_mac=bb' method='post'>
  <input type="hidden" name="ap_mac" value="aa" />
  <input type="hidden" name="client_mac" value="bb" />
  <input type="hidden" name="wlan_id" value="w123" />
  <input type="hidden" name="url" value="http://a/?x=1&amp;y=2" />
  <input type='hidden' name='direct' value='True'/>
  <input type='checkbox' name='tos' value='true'>
  <button name='auth_method' value='passphrase' type='submit'>
</form>
</body></html>
"""

# A page offering only access-code forms (membership/code variant —
# not automatable without a code).
CODE_ONLY_HTML = """
<html><body>
<form name='submitSms' action='logon?x=1'>
  <input type="hidden" name="ap_mac" value="aa" />
  <input type='text' name='accessCode'>
  <button name='smsCodeSubmit' value='submitSmsCode' type='submit'>
</form>
</body></html>
"""

REDIRECT_URL = "https://portal.gc1.mist.com/logon?ap_mac=aa&client_mac=bb"


class TestFindTosForm:
    """Test TOS-form selection from portal HTML."""

    def test_picks_auth_method_form(self):
        """The form with the auth_method submit is preferred."""
        form = find_tos_form(PORTAL_HTML)
        assert form is not None
        assert form.action.startswith("logon?")
        assert any(s.name == "auth_method" for s in form.submits)

    def test_code_only_page_returns_none(self):
        """Access-code-only pages are not replayable."""
        assert find_tos_form(CODE_ONLY_HTML) is None

    def test_empty_page_returns_none(self):
        assert find_tos_form("<html></html>") is None


class TestBuildPostData:
    """Test POST body construction."""

    def test_includes_all_fields(self):
        form = find_tos_form(PORTAL_HTML)
        data = build_post_data(form)
        assert data["ap_mac"] == "aa"
        assert data["client_mac"] == "bb"
        assert data["wlan_id"] == "w123"
        assert data["direct"] == "True"
        assert data["tos"] == "true"
        assert data["auth_method"] == "passphrase"

    def test_unescapes_html_entities(self):
        """&amp; in the hidden url field must be decoded to &."""
        form = find_tos_form(PORTAL_HTML)
        data = build_post_data(form)
        assert data["url"] == "http://a/?x=1&y=2"


class TestAcceptCostcoTerms:
    """Test the GET-form/POST-accept flow."""

    def _session(self, post_status: int = 200) -> MagicMock:
        session = MagicMock()
        page = MagicMock()
        page.status_code = 200
        page.text = PORTAL_HTML
        session.get.return_value = page
        session.post.return_value.status_code = post_status
        return session

    def test_successful_acceptance(self):
        session = self._session()
        with patch(
            "wifi_portal_automation.costco_portal.verify_internet_connectivity",
            return_value=True,
        ):
            assert accept_costco_terms(
                redirect_url=REDIRECT_URL, session=session
            ) is True

        post_call = session.post.call_args
        # Relative form action resolved against the redirect URL
        assert post_call.args[0] == (
            "https://portal.gc1.mist.com/logon?ap_mac=aa&client_mac=bb"
        )
        assert post_call.kwargs["data"]["auth_method"] == "passphrase"
        assert post_call.kwargs["data"]["tos"] == "true"
        # Referer header carries the portal page URL
        assert post_call.kwargs["headers"]["Referer"] == REDIRECT_URL

    def test_no_tos_form_raises(self):
        session = MagicMock()
        page = MagicMock()
        page.status_code = 200
        page.text = CODE_ONLY_HTML
        session.get.return_value = page

        with pytest.raises(CostcoPortalError, match="No replayable TOS form"):
            accept_costco_terms(redirect_url=REDIRECT_URL, session=session)

    def test_post_non_success_status(self):
        session = self._session(post_status=500)
        assert (
            accept_costco_terms(redirect_url=REDIRECT_URL, session=session)
            is False
        )

    def test_verify_fails_returns_false(self):
        session = self._session()
        with patch(
            "wifi_portal_automation.costco_portal.verify_internet_connectivity",
            return_value=False,
        ):
            assert (
                accept_costco_terms(redirect_url=REDIRECT_URL, session=session)
                is False
            )

    def test_network_error_raises(self):
        session = MagicMock()
        session.get.side_effect = RequestException("down")
        with pytest.raises(CostcoPortalError, match="Failed to accept"):
            accept_costco_terms(redirect_url=REDIRECT_URL, session=session)


class TestHandleCostcoConnection:
    """Test the retrying connection handler."""

    def test_successful_connection(self):
        session = MagicMock()
        with (
            patch(
                "wifi_portal_automation.costco_portal.detect_captive_portal",
                return_value=(True, REDIRECT_URL),
            ),
            patch(
                "wifi_portal_automation.costco_portal.accept_costco_terms",
                return_value=True,
            ),
            patch("wifi_portal_automation.costco_portal.time.sleep"),
        ):
            assert handle_costco_connection(session=session) is True

    def test_no_portal_but_has_internet(self):
        session = MagicMock()
        with (
            patch(
                "wifi_portal_automation.costco_portal.detect_captive_portal",
                return_value=(False, None),
            ),
            patch(
                "wifi_portal_automation.costco_portal.verify_internet_connectivity",
                return_value=True,
            ),
            patch("wifi_portal_automation.costco_portal.time.sleep"),
        ):
            assert handle_costco_connection(session=session) is True

    def test_no_portal_no_internet_retries(self):
        session = MagicMock()
        with (
            patch(
                "wifi_portal_automation.costco_portal.detect_captive_portal",
                return_value=(False, None),
            ),
            patch(
                "wifi_portal_automation.costco_portal.verify_internet_connectivity",
                return_value=False,
            ),
            patch("wifi_portal_automation.costco_portal.time.sleep"),
        ):
            result = handle_costco_connection(
                max_attempts=2, attempt_delay=0.1, session=session
            )
        assert result is False

    def test_portal_without_redirect_url_retries(self):
        """Captive detected but no redirect URL (timeout) -> retry."""
        session = MagicMock()
        with (
            patch(
                "wifi_portal_automation.costco_portal.detect_captive_portal",
                return_value=(True, None),
            ),
            patch(
                "wifi_portal_automation.costco_portal.verify_internet_connectivity",
                return_value=False,
            ),
            patch("wifi_portal_automation.costco_portal.time.sleep"),
        ):
            result = handle_costco_connection(
                max_attempts=2, attempt_delay=0.1, session=session
            )
        assert result is False

    def test_costco_error_propagates_after_max_attempts(self):
        session = MagicMock()
        with (
            patch(
                "wifi_portal_automation.costco_portal.detect_captive_portal",
                return_value=(True, REDIRECT_URL),
            ),
            patch(
                "wifi_portal_automation.costco_portal.accept_costco_terms",
                side_effect=CostcoPortalError("fail"),
            ),
            patch("wifi_portal_automation.costco_portal.time.sleep"),
            pytest.raises(CostcoPortalError, match="fail"),
        ):
            handle_costco_connection(
                max_attempts=2, attempt_delay=0.1, session=session
            )


class TestMain:
    """Test the CLI entry point."""

    def test_bind_failure_returns_1(self):
        from wifi_portal_automation.wifi_binding import InterfaceBindingError

        with patch(
            "wifi_portal_automation.wifi_binding.create_wifi_session",
            side_effect=InterfaceBindingError("no wlan"),
        ):
            assert main([]) == 1

    def test_success_returns_0(self):
        with (
            patch(
                "wifi_portal_automation.wifi_binding.create_wifi_session",
                return_value=MagicMock(),
            ),
            patch(
                "wifi_portal_automation.costco_portal.handle_costco_connection",
                return_value=True,
            ),
        ):
            assert main([]) == 0

    def test_portal_error_returns_1(self):
        with (
            patch(
                "wifi_portal_automation.wifi_binding.create_wifi_session",
                return_value=MagicMock(),
            ),
            patch(
                "wifi_portal_automation.costco_portal.handle_costco_connection",
                side_effect=CostcoPortalError("no TOS form"),
            ),
        ):
            assert main([]) == 1


class TestConstants:
    """Pin the SSID — 'CostcoWiFi' was an early wrong guess."""

    def test_costco_ssid(self):
        assert COSTCO_SSID == "Costco Member Wifi"
