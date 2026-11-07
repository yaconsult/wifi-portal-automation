"""Tests for the generic portal analyzer."""

from unittest.mock import MagicMock, patch

from wifi_portal_automation.portal_analyzer import (
    FormField,
    PortalForm,
    PortalReport,
    analyze_portal,
    extract_host_from_url,
    main,
    parse_forms,
)


class TestExtractHostFromUrl:
    """Test host extraction from URLs."""

    def test_extract_ip_with_port(self):
        assert extract_host_from_url("http://10.64.2.21:9997/login") == "10.64.2.21:9997"

    def test_extract_ip_without_port(self):
        assert extract_host_from_url("http://192.168.1.1/portal") == "192.168.1.1"

    def test_extract_hostname(self):
        assert extract_host_from_url("http://portal.example.com/form") == "portal.example.com"

    def test_extract_https(self):
        assert extract_host_from_url("https://10.64.2.21:9997/portal") == "10.64.2.21:9997"

    def test_extract_invalid_url(self):
        assert extract_host_from_url("not a url") == ""
        assert extract_host_from_url("") == ""


class TestParseForms:
    """Test HTML form parsing."""

    def test_parse_simple_form(self):
        """Test parsing a basic form with action and method."""
        html = """
        <form action="/login" method="POST">
            <input type="submit" name="ok" value="Accept">
        </form>
        """
        forms = parse_forms(html)

        assert len(forms) == 1
        assert forms[0].action == "/login"
        assert forms[0].method == "POST"

    def test_parse_form_with_hidden_fields(self):
        """Test parsing a form with hidden fields."""
        html = """
        <form action="/submit" method="post">
            <input type="hidden" name="token" value="abc123">
            <input type="hidden" name="origurl" value="http://google.com">
        </form>
        """
        forms = parse_forms(html)

        assert len(forms) == 1
        assert len(forms[0].hiddens) == 2
        assert forms[0].hiddens[0].name == "token"
        assert forms[0].hiddens[0].value == "abc123"
        assert forms[0].hiddens[1].name == "origurl"
        assert forms[0].hiddens[1].value == "http://google.com"

    def test_parse_form_with_checkbox(self):
        """Test parsing a form with a checkbox."""
        html = """
        <form action="/agree" method="POST">
            <input type="checkbox" name="accept_terms" value="yes">
            <input type="submit" name="submit" value="Continue">
        </form>
        """
        forms = parse_forms(html)

        assert len(forms) == 1
        assert len(forms[0].checkboxes) == 1
        assert forms[0].checkboxes[0].name == "accept_terms"
        assert forms[0].checkboxes[0].value == "yes"
        assert len(forms[0].submits) == 1
        assert forms[0].submits[0].name == "submit"
        assert forms[0].submits[0].value == "Continue"

    def test_parse_multiple_forms(self):
        """Test parsing multiple forms in one page."""
        html = """
        <form action="/form1" method="POST">
            <input type="submit" name="btn1" value="One">
        </form>
        <form action="/form2" method="GET">
            <input type="submit" name="btn2" value="Two">
        </form>
        """
        forms = parse_forms(html)

        assert len(forms) == 2
        assert forms[0].action == "/form1"
        assert forms[1].action == "/form2"

    def test_parse_no_forms(self):
        """Test parsing HTML with no forms."""
        forms = parse_forms("<html><body>No forms here</body></html>")
        assert forms == []

    def test_parse_form_no_action(self):
        """Test parsing a form without an action attribute."""
        html = """
        <form method="POST">
            <input type="submit" value="Submit">
        </form>
        """
        forms = parse_forms(html)

        assert len(forms) == 1
        assert forms[0].action == ""
        assert forms[0].method == "POST"

    def test_parse_form_case_insensitive(self):
        """Test that form parsing is case-insensitive."""
        html = """
        <FORM ACTION="/Login" METHOD="Post">
            <INPUT TYPE="Submit" NAME="ok" VALUE="Accept">
        </FORM>
        """
        forms = parse_forms(html)

        assert len(forms) == 1
        assert forms[0].action == "/Login"
        assert forms[0].method == "Post"
        assert len(forms[0].submits) == 1
        assert forms[0].submits[0].name == "ok"


class TestAnalyzePortal:
    """Test the analyze_portal function."""

    def test_no_redirect_returns_empty_report(self):
        """Test that a 200 response (no portal) returns an empty report."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_get = MagicMock(return_value=mock_response)

        report = analyze_portal(http_get=mock_get)

        assert report.response_code == 200
        assert report.redirect_url == ""
        assert report.forms == []
        assert report.error == ""

    def test_redirect_detected_and_portal_parsed(self):
        """Test that a redirect is followed and the portal page is parsed."""
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {"Location": "http://10.64.2.21:9997/login"}

        portal_html = """
        <form action="/accept" method="POST">
            <input type="hidden" name="token" value="xyz">
            <input type="submit" name="ok" value="Accept">
        </form>
        """
        portal_response = MagicMock()
        portal_response.status_code = 200
        portal_response.text = portal_html

        mock_get = MagicMock(side_effect=[redirect_response, portal_response])

        report = analyze_portal(http_get=mock_get)

        assert report.response_code == 302
        assert report.redirect_url == "http://10.64.2.21:9997/login"
        assert report.portal_host == "10.64.2.21:9997"
        assert report.portal_page_status == 200
        assert len(report.forms) == 1
        assert report.forms[0].action == "/accept"
        assert len(report.forms[0].hiddens) == 1
        assert len(report.forms[0].submits) == 1

    def test_request_exception_sets_error(self):
        """Test that a request exception is captured in the report."""
        from requests import RequestException

        mock_get = MagicMock(side_effect=RequestException("connection failed"))

        report = analyze_portal(http_get=mock_get)

        assert report.error == "connection failed"

    def test_custom_probe_url(self):
        """Test that a custom probe URL is used."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get = MagicMock(return_value=mock_response)

        analyze_portal(probe_url="http://1.1.1.1/", http_get=mock_get)

        call_args = mock_get.call_args
        assert call_args.args[0] == "http://1.1.1.1/"

    def test_save_html(self, tmp_path):
        """Test that HTML is saved to a file when requested."""
        redirect_response = MagicMock()
        redirect_response.status_code = 302
        redirect_response.headers = {"Location": "http://10.64.2.21/login"}

        portal_response = MagicMock()
        portal_response.status_code = 200
        portal_response.text = "<html>Portal</html>"

        mock_get = MagicMock(side_effect=[redirect_response, portal_response])

        html_file = tmp_path / "portal.html"
        report = analyze_portal(
            save_html=True,
            html_path=str(html_file),
            http_get=mock_get,
        )

        assert report.html_saved is True
        assert html_file.exists()
        assert html_file.read_text() == "<html>Portal</html>"

    def test_uses_session_when_provided(self):
        """Test that a provided session's get method is used."""
        mock_session = MagicMock()
        mock_response = mock_session.get.return_value
        mock_response.status_code = 200

        analyze_portal(session=mock_session)

        mock_session.get.assert_called_once()


class TestPortalReport:
    """Test the PortalReport dataclass."""

    def test_to_text_with_error(self):
        """Test that error reports are formatted correctly."""
        report = PortalReport(error="something went wrong")
        text = report.to_text()
        assert "Error: something went wrong" in text

    def test_to_text_with_redirect(self):
        """Test that successful reports include portal details."""
        report = PortalReport(
            probe_url="http://1.1.1.1/",
            response_code=302,
            redirect_url="http://10.64.2.21:9997/login",
            portal_host="10.64.2.21:9997",
            portal_page_status=200,
            forms=[
                PortalForm(
                    action="/accept",
                    method="POST",
                    fields=[FormField(field_type="submit", name="ok", value="Accept")],
                )
            ],
        )
        text = report.to_text()
        assert "Response Code: 302" in text
        assert "Redirect Location: http://10.64.2.21:9997/login" in text
        assert "Portal Host: 10.64.2.21:9997" in text
        assert "Action: /accept" in text
        assert "Button: name=ok, value=Accept" in text

    def test_to_text_no_redirect(self):
        """Test that no-redirect reports say so."""
        report = PortalReport(
            probe_url="http://1.1.1.1/",
            response_code=200,
        )
        text = report.to_text()
        assert "No redirect detected" in text


class TestMain:
    """Test the CLI entry point."""

    def test_main_success(self):
        """Test that main returns 0 on success."""
        with patch("wifi_portal_automation.portal_analyzer.analyze_portal") as mock_analyze:
            mock_analyze.return_value = PortalReport(response_code=200)
            exit_code = main(["--probe-url", "http://1.1.1.1/"])

        assert exit_code == 0

    def test_main_error(self):
        """Test that main returns 1 on error."""
        with patch("wifi_portal_automation.portal_analyzer.analyze_portal") as mock_analyze:
            mock_analyze.return_value = PortalReport(error="failed")
            exit_code = main([])

        assert exit_code == 1

    def test_main_custom_timeout(self):
        """Test that --timeout is passed through."""
        with patch("wifi_portal_automation.portal_analyzer.analyze_portal") as mock_analyze:
            mock_analyze.return_value = PortalReport()
            main(["--timeout", "10"])

        call_kwargs = mock_analyze.call_args.kwargs
        assert call_kwargs["timeout"] == 10

    def test_main_save_html_flag(self):
        """Test that --save-html is passed through."""
        with patch("wifi_portal_automation.portal_analyzer.analyze_portal") as mock_analyze:
            mock_analyze.return_value = PortalReport()
            main(["--save-html"])

        call_kwargs = mock_analyze.call_args.kwargs
        assert call_kwargs["save_html"] is True

    def test_main_html_path_passthrough(self):
        """Test that --html-path is passed through to analyze_portal."""
        with patch("wifi_portal_automation.portal_analyzer.analyze_portal") as mock_analyze:
            mock_analyze.return_value = PortalReport()
            main(["--save-html", "--html-path", "/data/local/tmp/p.html"])

        call_kwargs = mock_analyze.call_args.kwargs
        assert call_kwargs["html_path"] == "/data/local/tmp/p.html"

    def test_main_output_to_file(self, tmp_path):
        """Test that --output writes to a file."""
        with patch("wifi_portal_automation.portal_analyzer.analyze_portal") as mock_analyze:
            mock_analyze.return_value = PortalReport(response_code=200)
            output_file = tmp_path / "report.txt"
            main(["--output", str(output_file)])

        assert output_file.exists()
        content = output_file.read_text()
        assert "Captive Portal Analysis" in content

    def test_main_interface_creates_session(self):
        """Test that --interface creates a WiFi-bound session."""
        mock_session = MagicMock()

        with (
            patch(
                "wifi_portal_automation.wifi_binding.create_wifi_session",
                return_value=mock_session,
            ),
            patch("wifi_portal_automation.portal_analyzer.analyze_portal") as mock_analyze,
        ):
            mock_analyze.return_value = PortalReport()
            main(["--interface", "wlan0"])

        call_kwargs = mock_analyze.call_args.kwargs
        assert call_kwargs["session"] is mock_session
