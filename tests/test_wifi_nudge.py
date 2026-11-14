"""Tests for the cmvwifi auto-join nudge (wifi_nudge.py).

Sample outputs are verbatim captures from `cmd wifi status` and
`cmd wifi list-scan-results` on Android 17.
"""

import json
import time
from unittest.mock import patch

from wifi_portal_automation.wifi_nudge import (
    NudgeReport,
    format_markdown,
    main,
    parse_scan_results,
    parse_wifi_status,
    run_nudge,
    wifi_validated,
)

# `cmd wifi status` while associated with cmvwifi (truncated tail).
STATUS_CONNECTED = """\
Wifi is enabled
Wifi scanning is always available
==== ClientModeManager instance: ConcreteClientModeManager{id=598128215 iface=wlan1} ====
Wifi is connected to "cmvwifi"
WifiInfo: SSID: "cmvwifi", BSSID: 94:b3:4f:a3:9e:e0, Supplicant state: COMPLETED
"""

# `cmd wifi status` while connected to a different network.
STATUS_OTHER = """\
Wifi is enabled
Wifi is connected to "dd-wrt_5G"
WifiInfo: SSID: "dd-wrt_5G", Supplicant state: COMPLETED
"""

# `cmd wifi status` while disconnected.
STATUS_DISCONNECTED = """\
Wifi is enabled
Wifi scanning is always available
==== ClientModeManager instance: ConcreteClientModeManager{id=598128215 iface=wlan1} ====
Wifi is not connected
"""

# `cmd wifi status` with the radio off.
STATUS_DISABLED = """\
Wifi is disabled
"""

# `cmd wifi list-scan-results` (real header + rows; hidden-SSID rows
# carry an empty SSID column, "CSA_Staff_Secure" exercises multi-word
# names are absent here but covered by SSID flags splitting).
SCAN_RESULTS = """\
    BSSID              Frequency      RSSI           Age(sec)     SSID                                 Flags
  22:61:b4:19:20:a6       2452        -90            148.587                                      [WPA2-PSK-CCMP-128][ESS]
  94:b3:4f:63:9e:e0       2412        -57            148.587    cmvwifi                           [ESS]
  94:b3:4f:a3:9e:e0       5540        -63            148.587    cmvwifi                           [ESS]
  62:22:32:fd:b3:26       6135        -67            148.587    CSA_Staff_Secure                  [WPA2-EAP/SHA256-CCMP-128][ESS]
  94:b3:4f:62:f8:20       2462        -82            148.587    cmvwifi                           [ESS]
"""


def _fake_run_root(responses: dict[str, tuple[int, str]]):
    """Return a run_root replacement dispatching on the command."""
    def fake(su: str, command: str, timeout: int = 30):
        for key, response in responses.items():
            if key in command:
                return response
        return (1, f"unexpected command: {command}")

    return fake


# `dumpsys connectivity` — the WIFI NetworkAgentInfo block (truncated;
# the real block is one long line).  Validated WiFi carries VALIDATED
# in nc{...Capabilities:}; a captive network shows CAPTIVE_PORTAL
# instead.  Modeled on a Pixel 6a capture.
DC_WIFI_VALIDATED = (
    'NetworkAgentInfo{network{153} ni{WIFI CONNECTED extra: } '
    'Score(Policies : IS_VALIDATED) lp{{InterfaceName: wlan1}} '
    'nc{[ Transports: WIFI Capabilities: INTERNET&TRUSTED&NOT_VPN&'
    'VALIDATED&NOT_SUSPENDED LinkUpBandwidth>=19183Kbps '
    'Specifier: <WifiNetworkAgentSpecifier [SSID="cmvwifi"]>]}}'
)
DC_WIFI_CAPTIVE = DC_WIFI_VALIDATED.replace(
    "VALIDATED&NOT_SUSPENDED", "CAPTIVE_PORTAL&NOT_SUSPENDED"
)
DC_NO_WIFI_AGENT = (
    'NetworkAgentInfo{network{143} ni{MOBILE[LTE] CONNECTED} '
    'nc{[ Transports: CELLULAR Capabilities: VALIDATED ]}}'
)


class TestParseWifiStatus:
    """Test `cmd wifi status` parsing."""

    def test_connected(self):
        status = parse_wifi_status(STATUS_CONNECTED)
        assert status.enabled is True
        assert status.connected_ssid == "cmvwifi"

    def test_connected_other(self):
        status = parse_wifi_status(STATUS_OTHER)
        assert status.connected_ssid == "dd-wrt_5G"

    def test_disconnected(self):
        status = parse_wifi_status(STATUS_DISCONNECTED)
        assert status.enabled is True
        assert status.connected_ssid is None

    def test_disabled(self):
        status = parse_wifi_status(STATUS_DISABLED)
        assert status.enabled is False
        assert status.connected_ssid is None

    def test_garbage(self):
        status = parse_wifi_status("")
        assert status.enabled is False
        assert status.connected_ssid is None


class TestParseScanResults:
    """Test `cmd wifi list-scan-results` parsing."""

    def test_rows_parsed(self):
        results = parse_scan_results(SCAN_RESULTS)
        assert len(results) == 5
        assert results[1].ssid == "cmvwifi"
        assert results[1].bssid == "94:b3:4f:63:9e:e0"
        assert results[1].frequency == 2412
        assert results[1].rssi == -57

    def test_hidden_ssid_empty(self):
        results = parse_scan_results(SCAN_RESULTS)
        assert results[0].ssid == ""

    def test_ssids_set(self):
        ssids = {r.ssid for r in parse_scan_results(SCAN_RESULTS)}
        assert "cmvwifi" in ssids
        assert "CSA_Staff_Secure" in ssids

    def test_header_and_garbage_skipped(self):
        assert parse_scan_results("BSSID Frequency RSSI\nhello world\n") == []
        assert parse_scan_results("") == []


class TestWifiValidated:
    """Test `dumpsys connectivity` validation parsing."""

    def _run(self, output: str, rc: int = 0) -> bool | None:
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            return_value=(rc, output),
        ):
            return wifi_validated("su")

    def test_validated(self):
        assert self._run(DC_WIFI_VALIDATED) is True

    def test_captive(self):
        assert self._run(DC_WIFI_CAPTIVE) is False

    def test_no_wifi_agent(self):
        assert self._run(DC_NO_WIFI_AGENT) is None

    def test_rc_failure(self):
        assert self._run("", rc=1) is None


class TestRunNudge:
    """Test the nudge decision flow (run_root mocked)."""

    def test_noop_when_already_on_target(self):
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            side_effect=_fake_run_root({"status": (0, STATUS_CONNECTED)}),
        ):
            report = run_nudge("su", ["cmvwifi"], settle=0)
        assert report.action == "already_connected"

    def test_portal_recheck_runs_handler_when_captive(self):
        """Connected to a portal SSID + CAPTIVE_PORTAL -> re-handle."""
        calls = []
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            side_effect=_fake_run_root({
                "status": (0, STATUS_CONNECTED),
                "dumpsys": (0, DC_WIFI_CAPTIVE),
            }),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], settle=0,
                portal_ssids=("cmvwifi",),
                on_unvalidated=lambda: calls.append(1) or True,
            )
        assert calls == [1]
        assert report.action == "portal_rehandled"
        assert report.ok is True

    def test_portal_recheck_failure_reported(self):
        """A failed re-handle is a distinct, non-ok action."""
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            side_effect=_fake_run_root({
                "status": (0, STATUS_CONNECTED),
                "dumpsys": (0, DC_WIFI_CAPTIVE),
            }),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], settle=0,
                portal_ssids=("cmvwifi",),
                on_unvalidated=lambda: False,
            )
        assert report.action == "portal_recheck_failed"
        assert report.ok is False

    def test_portal_recheck_skipped_when_validated(self):
        """Validated portal SSID -> already_connected, no handler call."""
        calls = []
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            side_effect=_fake_run_root({
                "status": (0, STATUS_CONNECTED),
                "dumpsys": (0, DC_WIFI_VALIDATED),
            }),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], settle=0,
                portal_ssids=("cmvwifi",),
                on_unvalidated=lambda: calls.append(1) or True,
            )
        assert calls == []
        assert report.action == "already_connected"

    def test_portal_recheck_skipped_for_non_portal_ssid(self):
        """Connected to a non-portal target -> no dumpsys, no handler."""
        calls = []
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            side_effect=_fake_run_root({"status": (0, STATUS_CONNECTED)}),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], settle=0,
                portal_ssids=("MVwifi",),  # connected SSID not in list
                on_unvalidated=lambda: calls.append(1) or True,
            )
        assert calls == []
        assert report.action == "already_connected"

    def test_portal_recheck_skipped_on_parse_failure(self):
        """dumpsys failure -> None -> treat as 'don't recheck'."""
        calls = []
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            side_effect=_fake_run_root({
                "status": (0, STATUS_CONNECTED),
                "dumpsys": (1, ""),
            }),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], settle=0,
                portal_ssids=("cmvwifi",),
                on_unvalidated=lambda: calls.append(1) or True,
            )
        assert calls == []
        assert report.action == "already_connected"
        assert report.ok is True

    def test_noop_when_connected_elsewhere(self):
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_OTHER)
            return (1, "must not be called")

        with patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake):
            report = run_nudge("su", ["cmvwifi"], settle=0)
        assert report.action == "connected_elsewhere"
        assert report.ok is True
        # No scan, and definitely no connect attempt.
        assert all("scan" not in c for c in commands)
        assert all("connect-network" not in c for c in commands)

    def test_noop_when_wifi_disabled(self):
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            side_effect=_fake_run_root({"status": (0, STATUS_DISABLED)}),
        ):
            report = run_nudge("su", ["cmvwifi"], settle=0)
        assert report.action == "wifi_disabled"
        assert report.ok is True

    def test_noop_when_target_absent(self):
        scan_no_target = SCAN_RESULTS.replace("cmvwifi", "other_ssid")
        with (
            patch(
                "wifi_portal_automation.wifi_nudge.run_root",
                side_effect=_fake_run_root(
                    {
                        "status": (0, STATUS_DISCONNECTED),
                        "start-scan": (0, ""),
                        "list-scan-results": (0, scan_no_target),
                    }
                ),
            ),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge("su", ["cmvwifi"], settle=0)
        assert report.action == "target_absent"
        assert report.ok is True
        assert report.visible_targets == []

    def test_connects_when_target_visible(self):
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_DISCONNECTED)
            if "start-scan" in command:
                return (0, "")
            if "list-scan-results" in command:
                return (0, SCAN_RESULTS)
            if "connect-network" in command:
                return (0, "")
            return (1, "unexpected")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge("su", ["cmvwifi"], settle=0)
        assert report.action == "connect_requested"
        assert report.ok is True
        assert report.connect_rc == 0
        assert "cmd wifi connect-network cmvwifi open" in commands

    def test_dry_run_skips_connect(self):
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_DISCONNECTED)
            if "list-scan-results" in command:
                return (0, SCAN_RESULTS)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge("su", ["cmvwifi"], settle=0, dry_run=True)
        assert report.action == "dry_run"
        assert report.visible_targets == ["cmvwifi"]
        assert all("connect-network" not in c for c in commands)

    def test_multi_word_ssid_quoted(self):
        commands: list[str] = []
        scan = SCAN_RESULTS.replace("cmvwifi", "Costco Member Wifi")

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_DISCONNECTED)
            if "list-scan-results" in command:
                return (0, scan)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge("su", ["Costco Member Wifi"], settle=0)
        assert report.action == "connect_requested"
        assert "cmd wifi connect-network 'Costco Member Wifi' open" in commands

    def test_status_command_failure(self):
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root", return_value=(1, "boom")
        ):
            report = run_nudge("su", ["cmvwifi"], settle=0)
        assert report.action == "status_failed"
        assert report.ok is False

    def test_scan_command_failure(self):
        with (
            patch(
                "wifi_portal_automation.wifi_nudge.run_root",
                side_effect=_fake_run_root(
                    {
                        "status": (0, STATUS_DISCONNECTED),
                        "list-scan-results": (1, "denied"),
                    }
                ),
            ),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge("su", ["cmvwifi"], settle=0)
        assert report.action == "scan_failed"
        assert report.ok is False

    def test_connect_command_failure(self):
        with (
            patch(
                "wifi_portal_automation.wifi_nudge.run_root",
                side_effect=_fake_run_root(
                    {
                        "status": (0, STATUS_DISCONNECTED),
                        "list-scan-results": (0, SCAN_RESULTS),
                        "connect-network": (1, "rejected"),
                    }
                ),
            ),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge("su", ["cmvwifi"], settle=0)
        assert report.action == "connect_failed"
        assert report.ok is False
        assert report.connect_rc == 1


# `cmd wifi status` while connected to the xfinitywifi fallback.
STATUS_FALLBACK = STATUS_CONNECTED.replace("cmvwifi", "xfinitywifi")

# Scan variants: both SSIDs visible / fallback only.
SCAN_BOTH = SCAN_RESULTS.replace("CSA_Staff_Secure", "xfinitywifi")
SCAN_FALLBACK_ONLY = SCAN_RESULTS.replace("cmvwifi", "xfinitywifi")


class TestFallbacks:
    """Fallback networks: promote away when a preferred target is
    visible, stay put when not; fallback SSIDs are never joined by a
    run that only knows them as fallbacks."""

    def test_promotes_to_visible_preferred(self):
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_FALLBACK)
            if "list-scan-results" in command:
                return (0, SCAN_BOTH)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], fallbacks=["xfinitywifi"], settle=0
            )
        assert report.action == "promoted"
        assert report.ok is True
        # Preferred targets keep normal auto-join — no -d flag.
        assert "cmd wifi connect-network cmvwifi open" in commands

    def test_stays_on_fallback_when_no_preferred_visible(self):
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_FALLBACK)
            if "list-scan-results" in command:
                return (0, SCAN_FALLBACK_ONLY)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], fallbacks=["xfinitywifi"], settle=0
            )
        assert report.action == "fallback_stay"
        assert report.ok is True
        assert all("connect-network" not in c for c in commands)

    def test_fallback_never_joined_by_this_run(self):
        """Disconnected + only the fallback visible -> target_absent.

        Joining a fallback is a different invocation's job
        (--ssid xfinitywifi), so disabling the fallback's Tasker
        profile removes it from circulation entirely."""
        with (
            patch(
                "wifi_portal_automation.wifi_nudge.run_root",
                side_effect=_fake_run_root(
                    {
                        "status": (0, STATUS_DISCONNECTED),
                        "list-scan-results": (0, SCAN_FALLBACK_ONLY),
                    }
                ),
            ),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], fallbacks=["xfinitywifi"], settle=0
            )
        assert report.action == "target_absent"

    def test_autojoin_disabled_connect(self):
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_DISCONNECTED)
            if "list-scan-results" in command:
                return (0, SCAN_FALLBACK_ONLY)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su",
                ["xfinitywifi"],
                settle=0,
                autojoin_disabled=True,
            )
        assert report.action == "connect_requested"
        assert "cmd wifi connect-network xfinitywifi open -d" in commands

    def test_promotion_never_uses_autojoin_disabled(self):
        """Promoting onto a preferred network must not carry -d —
        preferred SSIDs keep normal auto-join even if the run was
        configured --autojoin-disabled."""
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_FALLBACK)
            if "list-scan-results" in command:
                return (0, SCAN_BOTH)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su",
                ["cmvwifi"],
                fallbacks=["xfinitywifi"],
                settle=0,
                autojoin_disabled=True,
            )
        assert report.action == "promoted"
        connect = [c for c in commands if "connect-network" in c]
        assert connect == ["cmd wifi connect-network cmvwifi open"]

    def test_fallback_dry_run_reports_without_connecting(self):
        commands: list[str] = []

        def tracked(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_FALLBACK)
            if "list-scan-results" in command:
                return (0, SCAN_BOTH)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=tracked),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su",
                ["cmvwifi"],
                fallbacks=["xfinitywifi"],
                settle=0,
                dry_run=True,
            )
        assert report.action == "dry_run"
        assert report.visible_targets == ["cmvwifi"]
        assert all("connect-network" not in c for c in commands)

    def test_cli_wires_fallback_and_autojoin_flags(self):
        """--fallback/--autojoin-disabled reach run_nudge."""
        fake_report = NudgeReport(action="connect_requested", ok=True)
        with patch(
            "wifi_portal_automation.wifi_nudge.run_nudge",
            return_value=fake_report,
        ) as mock_run, patch(
            "wifi_portal_automation.wifi_nudge.find_su", return_value="su"
        ):
            rc = main(
                [
                    "--ssid", "xfinitywifi",
                    "--fallback", "cmvwifi",
                    "--autojoin-disabled",
                ]
            )
        assert rc == 0
        _, kwargs = mock_run.call_args
        assert kwargs["fallbacks"] == ["cmvwifi"]
        assert kwargs["autojoin_disabled"] is True


class TestDefer:
    """--defer-to: a fallback nudge yields when a preferred SSID is
    visible, so sibling nudge tasks don't race connect requests."""

    def test_defers_when_preferred_visible(self):
        """Disconnected + target visible + preferred visible ->
        deferred, no connect issued."""
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_DISCONNECTED)
            if "list-scan-results" in command:
                return (0, SCAN_BOTH)  # xfinitywifi + cmvwifi
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su", ["xfinitywifi"], defer_to=["cmvwifi"], settle=0
            )
        assert report.action == "deferred"
        assert report.ok is True
        assert report.visible_targets == ["cmvwifi"]
        assert all("connect-network" not in c for c in commands)

    def test_connects_when_no_defer_target_visible(self):
        """Disconnected + only the fallback visible -> connect."""
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_DISCONNECTED)
            if "list-scan-results" in command:
                return (0, SCAN_FALLBACK_ONLY)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su",
                ["xfinitywifi"],
                defer_to=["cmvwifi", "dd-wrt"],
                autojoin_disabled=True,
                settle=0,
            )
        assert report.action == "connect_requested"
        assert "cmd wifi connect-network xfinitywifi open -d" in commands

    def test_defer_does_not_block_promotion(self):
        """On a fallback connection, promotion ignores defer_to — the
        two flags solve different problems."""
        with (
            patch(
                "wifi_portal_automation.wifi_nudge.run_root",
                side_effect=_fake_run_root(
                    {
                        "status": (0, STATUS_FALLBACK),
                        "list-scan-results": (0, SCAN_BOTH),
                        "connect-network": (0, ""),
                    }
                ),
            ),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su",
                ["cmvwifi"],
                fallbacks=["xfinitywifi"],
                defer_to=["dd-wrt"],
                settle=0,
            )
        assert report.action == "promoted"

    def test_cli_wires_defer_to(self):
        """--defer-to reaches run_nudge."""
        fake_report = NudgeReport(action="deferred", ok=True)
        with patch(
            "wifi_portal_automation.wifi_nudge.run_nudge",
            return_value=fake_report,
        ) as mock_run, patch(
            "wifi_portal_automation.wifi_nudge.find_su", return_value="su"
        ):
            rc = main(
                [
                    "--ssid", "xfinitywifi",
                    "--defer-to", "cmvwifi",
                    "--defer-to", "MVwifi",
                ]
            )
        assert rc == 0
        _, kwargs = mock_run.call_args
        assert kwargs["defer_to"] == ["cmvwifi", "MVwifi"]

    def test_cli_defer_to_preferred_expands_constant(self):
        """--defer-to-preferred passes PREFERRED_SSIDS to run_nudge —
        the only way multi-word SSIDs ('Costco Member Wifi') can reach
        the defer list, since Tasker args are space-separated."""
        from wifi_portal_automation.wifi_nudge import PREFERRED_SSIDS

        fake_report = NudgeReport(action="deferred", ok=True)
        with patch(
            "wifi_portal_automation.wifi_nudge.run_nudge",
            return_value=fake_report,
        ) as mock_run, patch(
            "wifi_portal_automation.wifi_nudge.find_su", return_value="su"
        ):
            rc = main(["--ssid", "xfinitywifi", "--defer-to-preferred"])
        assert rc == 0
        _, kwargs = mock_run.call_args
        assert kwargs["defer_to"] == PREFERRED_SSIDS
        assert "Costco Member Wifi" in kwargs["defer_to"]

    def test_defer_to_multiword_ssid(self):
        """A visible multi-word preferred SSID defers the fallback
        join — the Costco-arrival case."""
        scan_with_costco = SCAN_FALLBACK_ONLY + (
            "  aa:bb:cc:dd:ee:ff       5180        -60   1.0   "
            "Costco Member Wifi                   [ESS]\n"
        )
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_DISCONNECTED)
            if "list-scan-results" in command:
                return (0, scan_with_costco)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su",
                ["xfinitywifi"],
                defer_to=["Costco Member Wifi"],
                settle=0,
            )
        assert report.action == "deferred"
        assert report.visible_targets == ["Costco Member Wifi"]
        assert all("connect-network" not in c for c in commands)

    def test_cli_preferred_expands_open_targets(self):
        """--preferred passes PREFERRED_OPEN_SSIDS as targets — the
        only way multi-word SSIDs ('Costco Member Wifi') can be nudge
        targets, since Tasker args are space-separated."""
        from wifi_portal_automation.wifi_nudge import PREFERRED_OPEN_SSIDS

        fake_report = NudgeReport(action="already_connected", ok=True)
        with patch(
            "wifi_portal_automation.wifi_nudge.run_nudge",
            return_value=fake_report,
        ) as mock_run, patch(
            "wifi_portal_automation.wifi_nudge.find_su", return_value="su"
        ):
            rc = main(["--preferred", "--fallback", "xfinitywifi"])
        assert rc == 0
        args_, kwargs = mock_run.call_args
        assert args_[1] == PREFERRED_OPEN_SSIDS
        assert "Costco Member Wifi" in args_[1]

    def test_promotes_to_multiword_preferred(self):
        """On a fallback with a multi-word preferred SSID visible,
        promotion connects to it — quoted correctly for the shell."""
        scan_with_costco = SCAN_FALLBACK_ONLY + (
            "  aa:bb:cc:dd:ee:ff       5180        -60   1.0   "
            "Costco Member Wifi                   [ESS]\n"
        )
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_FALLBACK)
            if "list-scan-results" in command:
                return (0, scan_with_costco)
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su",
                ["cmvwifi", "MVwifi", "Costco Member Wifi"],
                fallbacks=["xfinitywifi"],
                settle=0,
            )
        assert report.action == "promoted"
        assert (
            "cmd wifi connect-network 'Costco Member Wifi' open"
            in commands
        )

    def test_defer_never_blocks_own_target(self):
        """A defer_to SSID that is also this run's target must not
        self-defer — guard for overlapping configurations."""
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            if "status" in command:
                return (0, STATUS_DISCONNECTED)
            if "list-scan-results" in command:
                return (0, SCAN_RESULTS)  # cmvwifi visible
            return (0, "")

        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su",
                ["cmvwifi"],
                defer_to=["cmvwifi"],  # overlap: target in defer list
                settle=0,
            )
        assert report.action == "connect_requested"
        assert "cmd wifi connect-network cmvwifi open" in commands


class TestCli:
    """Test exit codes and report output modes."""

    def _patch_run(self, responses):
        return patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            side_effect=_fake_run_root(responses),
        )

    def test_no_root_exit_2(self):
        with patch("wifi_portal_automation.wifi_nudge.find_su", return_value=None):
            assert main([]) == 2

    def test_success_exit_0(self):
        with (
            patch("wifi_portal_automation.wifi_nudge.find_su", return_value="su"),
            self._patch_run({"status": (0, STATUS_CONNECTED)}),
        ):
            assert main([]) == 0

    def test_failure_exit_1(self):
        with (
            patch("wifi_portal_automation.wifi_nudge.find_su", return_value="su"),
            self._patch_run({"status": (1, "boom")}),
        ):
            assert main([]) == 1

    def test_json_output(self, capsys):
        with (
            patch("wifi_portal_automation.wifi_nudge.find_su", return_value="su"),
            self._patch_run({"status": (0, STATUS_CONNECTED)}),
        ):
            assert main(["--json"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["action"] == "already_connected"
        assert report["ok"] is True
        assert report["connected_ssid"] == "cmvwifi"

    def test_markdown_output(self, capsys):
        with (
            patch("wifi_portal_automation.wifi_nudge.find_su", return_value="su"),
            self._patch_run({"status": (0, STATUS_CONNECTED)}),
        ):
            assert main(["--markdown"]) == 0
        out = capsys.readouterr().out
        assert "## WiFi Nudge" in out
        assert "already_connected" in out

    def test_custom_ssid(self, capsys):
        with (
            patch("wifi_portal_automation.wifi_nudge.find_su", return_value="su"),
            self._patch_run(
                {
                    "status": (0, STATUS_DISCONNECTED),
                    "list-scan-results": (0, SCAN_RESULTS),
                }
            ),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            # Not in scan results -> target_absent, still exit 0.
            assert main(["--ssid", "NoSuchSSID", "--dry-run"]) == 0
        with (
            patch("wifi_portal_automation.wifi_nudge.find_su", return_value="su"),
            self._patch_run(
                {
                    "status": (0, STATUS_DISCONNECTED),
                    "list-scan-results": (0, SCAN_RESULTS),
                }
            ),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            assert main(["--ssid", "cmvwifi", "--dry-run", "--json"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["action"] == "dry_run"
        assert report["visible_targets"] == ["cmvwifi"]


class TestReport:
    """Test the report renderers."""

    def test_markdown_includes_fields(self):
        report = NudgeReport(
            action="connect_requested",
            ok=True,
            wifi_enabled=True,
            connected_ssid=None,
            targets=["cmvwifi"],
            visible_targets=["cmvwifi"],
            connect_rc=0,
        )
        md = format_markdown(report)
        assert "connect_requested" in md
        assert "cmvwifi" in md
        assert "connect rc" in md

    def test_to_dict_roundtrip(self):
        report = NudgeReport(action="target_absent", ok=True)
        data = report.to_dict()
        assert data["action"] == "target_absent"
        json.dumps(data)  # must be serializable


class TestMinIntervalThrottle:
    """Test the --min-interval scan throttle (state-file backed)."""

    _RESPONSES = {
        "status": (0, STATUS_DISCONNECTED),
        "start-scan": (0, ""),
        "list-scan-results": (0, SCAN_RESULTS),
        "connect-network": (0, ""),
    }

    def _run(self, tmp_path, min_interval=300):
        commands: list[str] = []

        def fake(su, command, timeout=30):
            commands.append(command)
            for key, response in self._RESPONSES.items():
                if key in command:
                    return response
            return (1, f"unexpected: {command}")

        state = tmp_path / "last_scan"
        with (
            patch("wifi_portal_automation.wifi_nudge.run_root", side_effect=fake),
            patch("wifi_portal_automation.wifi_nudge.time.sleep"),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], settle=0,
                min_interval=min_interval, state_file=state,
            )
        return report, commands, state

    def test_recent_scan_throttles(self, tmp_path):
        (tmp_path / "last_scan").write_text(str(time.time()))
        report, commands, _ = self._run(tmp_path)
        assert report.action == "throttled"
        assert report.ok is True
        # status check still ran; no scan or connect issued
        assert any("status" in c for c in commands)
        assert all("scan" not in c for c in commands)
        assert all("connect-network" not in c for c in commands)

    def test_missing_state_file_scans_and_stamps(self, tmp_path):
        report, commands, state = self._run(tmp_path)
        assert report.action == "connect_requested"
        assert any("list-scan-results" in c for c in commands)
        assert state.exists()
        assert abs(float(state.read_text()) - time.time()) < 10

    def test_stale_timestamp_scans_again(self, tmp_path):
        (tmp_path / "last_scan").write_text(str(time.time() - 10000))
        report, commands, _ = self._run(tmp_path)
        assert report.action == "connect_requested"
        assert any("list-scan-results" in c for c in commands)

    def test_zero_interval_never_throttles(self, tmp_path):
        (tmp_path / "last_scan").write_text(str(time.time()))
        report, commands, _ = self._run(tmp_path, min_interval=0)
        assert report.action == "connect_requested"
        assert any("list-scan-results" in c for c in commands)

    def test_connected_status_exits_before_throttle(self, tmp_path):
        (tmp_path / "last_scan").write_text(str(time.time() - 10000))
        with patch(
            "wifi_portal_automation.wifi_nudge.run_root",
            side_effect=_fake_run_root({"status": (0, STATUS_CONNECTED)}),
        ):
            report = run_nudge(
                "su", ["cmvwifi"], settle=0,
                min_interval=300, state_file=tmp_path / "last_scan",
            )
        assert report.action == "already_connected"
