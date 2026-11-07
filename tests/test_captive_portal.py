"""Tests for captive portal handling."""

import pytest

from wifi_portal_automation.captive_portal import (
    CMVWIFI_LOGIN_URL,
    DEFAULT_USER_AGENT,
    CaptivePortalError,
    accept_cmvwifi_terms,
    detect_captive_portal,
    detect_portal_host,
    extract_portal_host,
    get_default_gateway,
    verify_internet_connectivity,
)


class TestGetDefaultGateway:
    """Test gateway detection (kept for diagnostics)."""

    def test_successful_gateway_detection(self, mock_captive_subprocess):
        """Test detecting gateway from ip route output."""
        mock_result = mock_captive_subprocess.run.return_value
        mock_result.returncode = 0
        mock_result.stdout = "default via 192.168.1.1 dev wlp3s0 proto dhcp metric 600"

        gateway = get_default_gateway()

        assert gateway == "192.168.1.1"
        mock_captive_subprocess.run.assert_called_once()

    def test_no_gateway_found(self, mock_captive_subprocess):
        """Test when no default route exists."""
        mock_result = mock_captive_subprocess.run.return_value
        mock_result.returncode = 0
        mock_result.stdout = ""

        gateway = get_default_gateway()

        assert gateway is None

    def test_command_failure(self, mock_captive_subprocess):
        """Test when ip command fails."""
        mock_captive_subprocess.run.side_effect = FileNotFoundError()

        gateway = get_default_gateway()

        assert gateway is None


class TestExtractPortalHost:
    """Test extracting the portal host from a redirect URL."""

    def test_extract_ip_with_port(self):
        """Extract host:port from a portal URL with a port."""
        url = "http://10.64.2.21:9997/user/guest_tou.asp"
        assert extract_portal_host(url) == "10.64.2.21:9997"

    def test_extract_ip_without_port(self):
        """Extract host from a portal URL without a port."""
        url = "http://192.168.1.1/login"
        assert extract_portal_host(url) == "192.168.1.1"

    def test_extract_https(self):
        """Extract host from an HTTPS URL."""
        url = "https://10.64.2.21:9997/portal"
        assert extract_portal_host(url) == "10.64.2.21:9997"

    def test_extract_invalid_url(self):
        """Return None for a non-HTTP URL."""
        assert extract_portal_host("not a url") is None
        assert extract_portal_host("") is None


class TestDetectPortalHost:
    """Test portal host detection via probe redirect."""

    def test_detect_from_redirect(self, mock_requests):
        """Test extracting host from the final URL after redirect."""
        mock_response = mock_requests.get.return_value
        mock_response.url = "http://10.64.2.21:9997/user/guest_tou.asp"

        host = detect_portal_host()

        assert host == "10.64.2.21:9997"

    def test_detect_no_redirect(self, mock_requests):
        """Test that a non-redirected probe returns None."""
        mock_response = mock_requests.get.return_value
        mock_response.url = "http://1.1.1.1/"

        host = detect_portal_host()

        assert host is None

    def test_detect_request_exception(self, mock_requests):
        """Test that a request exception returns None."""
        from requests import RequestException

        mock_requests.get.side_effect = RequestException("fail")

        host = detect_portal_host()

        assert host is None

    def test_detect_uses_custom_probe_url(self, mock_requests):
        """Test that a custom probe URL is used."""
        mock_response = mock_requests.get.return_value
        mock_response.url = "http://10.64.2.23:9997/user/guest_tou.asp"

        host = detect_portal_host(probe_url="http://example.com/")

        call_args = mock_requests.get.call_args
        assert call_args.args[0] == "http://example.com/"
        assert host == "10.64.2.23:9997"


class TestDetectCaptivePortal:
    """Test captive portal detection."""

    def test_no_portal_success_response(self, mock_requests):
        """Test when no portal (200 response with success)."""
        mock_response = mock_requests.get.return_value
        mock_response.status_code = 200
        mock_response.text = "success"

        is_captive, redirect = detect_captive_portal()

        assert is_captive is False
        assert redirect is None

    def test_portal_detected_via_redirect(self, mock_requests):
        """Test portal detection via redirect."""
        mock_response = mock_requests.get.return_value
        mock_response.status_code = 302
        mock_response.headers = {"Location": "http://192.168.1.1/login"}

        is_captive, redirect = detect_captive_portal()

        assert is_captive is True
        assert redirect == "http://192.168.1.1/login"

    def test_portal_detected_via_html_response(self, mock_requests):
        """Test portal detection via HTML response."""
        mock_response = mock_requests.get.return_value
        mock_response.status_code = 200
        mock_response.text = "<html><body>Login Required</body></html>"

        is_captive, redirect = detect_captive_portal()

        assert is_captive is True

    def test_uses_correct_user_agent(self, mock_requests):
        """Test that correct User-Agent is sent."""
        detect_captive_portal()

        call_args = mock_requests.get.call_args
        headers = call_args.kwargs.get("headers", {})
        assert headers.get("User-Agent") == DEFAULT_USER_AGENT

    def test_connection_error_indicates_captive(self, mock_requests):
        """Test that connection error suggests captive portal."""
        from requests import ConnectionError

        mock_requests.get.side_effect = ConnectionError()

        is_captive, redirect = detect_captive_portal()

        assert is_captive is True

    def test_uses_session_when_provided(self, mock_requests):
        """Test that a provided session is used instead of the requests module."""
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_response = mock_session.get.return_value
        mock_response.status_code = 200
        mock_response.text = "success"

        is_captive, redirect = detect_captive_portal(session=mock_session)

        assert is_captive is False
        mock_session.get.assert_called_once()
        mock_requests.get.assert_not_called()


class TestAcceptCmvwifiTerms:
    """Test accepting Mountain View WiFi terms."""

    def test_successful_terms_acceptance(self, mock_requests):
        """Test successful terms acceptance with explicit portal host."""
        # First response for POST
        mock_post_response = mock_requests.post.return_value
        mock_post_response.status_code = 200

        # Second response for verification
        mock_get_response = mock_requests.get.return_value
        mock_get_response.status_code = 200
        mock_get_response.text = "success"

        result = accept_cmvwifi_terms(portal_host="10.64.2.21:9997")

        assert result is True
        mock_requests.post.assert_called_once()

        # Check POST URL
        call_args = mock_requests.post.call_args
        assert call_args.args[0] == f"http://10.64.2.21:9997{CMVWIFI_LOGIN_URL}"

    def test_successful_terms_acceptance_plain_ip(self, mock_requests):
        """Test successful terms acceptance with a plain IP (no port)."""
        mock_post_response = mock_requests.post.return_value
        mock_post_response.status_code = 200

        mock_get_response = mock_requests.get.return_value
        mock_get_response.status_code = 200
        mock_get_response.text = "success"

        result = accept_cmvwifi_terms(portal_host="192.168.1.1")

        assert result is True
        call_args = mock_requests.post.call_args
        assert call_args.args[0] == f"http://192.168.1.1{CMVWIFI_LOGIN_URL}"

    def test_post_failure(self, mock_requests):
        """Test when POST to portal fails."""
        from requests import RequestException

        mock_requests.post.side_effect = RequestException("Connection failed")

        with pytest.raises(CaptivePortalError):
            accept_cmvwifi_terms(portal_host="192.168.1.1")

    def test_internet_verification_fails(self, mock_requests):
        """Test when terms accepted but internet verification fails."""
        mock_post_response = mock_requests.post.return_value
        mock_post_response.status_code = 200

        mock_get_response = mock_requests.get.return_value
        mock_get_response.status_code = 200
        mock_get_response.text = "Please login"  # Wrong content

        result = accept_cmvwifi_terms(portal_host="192.168.1.1")

        assert result is False

    def test_auto_detect_portal_host(self, mock_requests, mock_captive_subprocess):
        """Test auto-detecting portal host when not provided.

        When portal_host is None, the function probes http://1.1.1.1/
        and extracts the host from the redirect URL.
        """
        # detect_portal_host calls requests.get (follows redirects)
        # The first get call is from detect_portal_host
        mock_get_response = mock_requests.get.return_value
        mock_get_response.url = "http://10.64.2.21:9997/user/guest_tou.asp"

        # POST response
        mock_post_response = mock_requests.post.return_value
        mock_post_response.status_code = 200

        # verify_internet_connectivity also calls requests.get
        # (same mock return value, configure for success)
        mock_get_response.status_code = 200
        mock_get_response.text = "success"

        accept_cmvwifi_terms()

        # Should use auto-detected portal host
        call_args = mock_requests.post.call_args
        assert "10.64.2.21:9997" in call_args.args[0]

    def test_no_portal_host_raises_error(self, mock_requests):
        """Test that error is raised when portal host cannot be detected."""
        from requests import RequestException

        # detect_portal_host returns None when request fails
        mock_requests.get.side_effect = RequestException("no network")

        with pytest.raises(CaptivePortalError, match="portal host"):
            accept_cmvwifi_terms()

    def test_uses_session_when_provided(self, mock_requests):
        """Test that a provided session is used for POST and verification."""
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_post_response = mock_session.post.return_value
        mock_post_response.status_code = 200

        mock_get_response = mock_session.get.return_value
        mock_get_response.status_code = 200
        mock_get_response.text = "success"

        result = accept_cmvwifi_terms(portal_host="10.64.2.21", session=mock_session)

        assert result is True
        mock_session.post.assert_called_once()
        mock_requests.post.assert_not_called()


class TestVerifyInternetConnectivity:
    """Test internet connectivity verification."""

    def test_successful_connectivity(self, mock_requests):
        """Test successful connectivity check."""
        mock_response = mock_requests.get.return_value
        mock_response.status_code = 200
        mock_response.text = "success"

        result = verify_internet_connectivity()

        assert result is True

    def test_wrong_status_code(self, mock_requests):
        """Test when status code is not 200."""
        mock_response = mock_requests.get.return_value
        mock_response.status_code = 302
        mock_response.text = "success"

        result = verify_internet_connectivity()

        assert result is False

    def test_wrong_content(self, mock_requests):
        """Test when content doesn't indicate success."""
        mock_response = mock_requests.get.return_value
        mock_response.status_code = 200
        mock_response.text = "Please login"

        result = verify_internet_connectivity()

        assert result is False

    def test_request_exception(self, mock_requests):
        """Test when request fails entirely."""
        from requests import RequestException

        mock_requests.get.side_effect = RequestException("Timeout")

        result = verify_internet_connectivity()

        assert result is False

    def test_uses_firefox_portal_url(self, mock_requests):
        """Test that Firefox portal URL is used by default."""
        verify_internet_connectivity()

        call_args = mock_requests.get.call_args
        assert "firefox.com" in call_args.args[0]

    def test_uses_session_when_provided(self, mock_requests):
        """Test that a provided session is used instead of the requests module."""
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_response = mock_session.get.return_value
        mock_response.status_code = 200
        mock_response.text = "success"

        result = verify_internet_connectivity(session=mock_session)

        assert result is True
        mock_session.get.assert_called_once()
        mock_requests.get.assert_not_called()
