"""Tests for WiFi interface binding."""

import socket
from unittest.mock import MagicMock, patch

import pytest
from requests.adapters import HTTPAdapter

from wifi_portal_automation.wifi_binding import (
    InterfaceBindingError,
    InterfaceBoundAdapter,
    create_wifi_session,
    detect_wifi_interface,
    get_interface_ip,
)


class TestGetInterfaceIp:
    """Test interface IP detection."""

    def test_ioctl_success(self):
        """Test getting IP via ioctl."""
        # Mock fcntl.ioctl to return a packed IP address
        import struct

        fake_packed = struct.pack(
            "256s",
            b"wlan0",
        )[:20] + socket.inet_aton("10.0.0.5")

        with (
            patch("wifi_portal_automation.wifi_binding.fcntl.ioctl", return_value=fake_packed),
            patch("wifi_portal_automation.wifi_binding.socket.socket") as mock_sock_class,
        ):
            mock_sock = MagicMock()
            mock_sock.fileno.return_value = 3
            mock_sock_class.return_value = mock_sock

            result = get_interface_ip("wlan0")

        assert result == "10.0.0.5"

    def test_ioctl_failure_falls_back_to_ip_command(self):
        """Test fallback to ``ip addr`` when ioctl fails."""
        ip_output = (
            "3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n"
            "    link/ether aa:bb:cc:dd:ee:ff brd ff:ff:ff:ff:ff:ff\n"
            "    inet 192.168.1.100/24 brd 192.168.1.255 scope global wlan0\n"
        )

        with (
            patch("wifi_portal_automation.wifi_binding.fcntl.ioctl", side_effect=OSError("nope")),
            patch("wifi_portal_automation.wifi_binding.socket.socket"),
            patch("wifi_portal_automation.wifi_binding.subprocess.run") as mock_run,
        ):
            mock_result = mock_run.return_value
            mock_result.returncode = 0
            mock_result.stdout = ip_output

            result = get_interface_ip("wlan0")

        assert result == "192.168.1.100"

    def test_both_methods_fail_raises_error(self):
        """Test that error is raised when both ioctl and ip command fail."""
        with (
            patch("wifi_portal_automation.wifi_binding.fcntl.ioctl", side_effect=OSError("nope")),
            patch("wifi_portal_automation.wifi_binding.socket.socket"),
            patch("wifi_portal_automation.wifi_binding.subprocess.run") as mock_run,
        ):
            mock_result = mock_run.return_value
            mock_result.returncode = 1
            mock_result.stdout = ""

            with pytest.raises(InterfaceBindingError, match="wlan0"):
                get_interface_ip("wlan0")

    def test_ip_command_not_found_raises_error(self):
        """Test that missing ip command raises InterfaceBindingError."""
        with (
            patch("wifi_portal_automation.wifi_binding.fcntl.ioctl", side_effect=OSError("nope")),
            patch("wifi_portal_automation.wifi_binding.socket.socket"),
            patch(
                "wifi_portal_automation.wifi_binding.subprocess.run",
                side_effect=FileNotFoundError(),
            ),
            pytest.raises(InterfaceBindingError, match="wlan0"),
        ):
            get_interface_ip("wlan0")


class TestDetectWifiInterface:
    """Test WiFi interface auto-detection."""

    def test_detects_wlan0(self):
        """Test that wlan0 is detected when it has an IP."""
        with patch(
            "wifi_portal_automation.wifi_binding._get_interface_ip_ioctl",
            side_effect=lambda iface: "10.0.0.5" if iface == "wlan0" else None,
        ):
            result = detect_wifi_interface()

        assert result == "wlan0"

    def test_detects_wlan1_when_wlan0_has_no_ip(self):
        """Test that wlan1 is detected when wlan0 has no IP."""
        with patch(
            "wifi_portal_automation.wifi_binding._get_interface_ip_ioctl",
            side_effect=lambda iface: "10.0.0.6" if iface == "wlan1" else None,
        ):
            result = detect_wifi_interface()

        assert result == "wlan1"

    def test_no_interface_found_raises_error(self):
        """Test that error is raised when no WiFi interface has an IP."""
        with (
            patch("wifi_portal_automation.wifi_binding._get_interface_ip_ioctl", return_value=None),
            pytest.raises(InterfaceBindingError, match="auto-detect"),
        ):
            detect_wifi_interface()


class TestInterfaceBoundAdapter:
    """Test the interface-bound HTTP adapter."""

    def test_adapter_stores_source_ip(self):
        """Test that the adapter stores the source IP."""
        with patch("wifi_portal_automation.wifi_binding.get_interface_ip", return_value="10.0.0.5"):
            adapter = InterfaceBoundAdapter("wlan0")

        assert adapter.source_ip == "10.0.0.5"
        assert adapter.interface == "wlan0"

    def test_adapter_propagates_binding_error(self):
        """Test that InterfaceBindingError propagates from get_interface_ip."""
        with (
            patch(
                "wifi_portal_automation.wifi_binding.get_interface_ip",
                side_effect=InterfaceBindingError("no ip"),
            ),
            pytest.raises(InterfaceBindingError, match="no ip"),
        ):
            InterfaceBoundAdapter("wlan0")

    def test_init_poolmanager_sets_source_address(self):
        """Test that the adapter stores the source IP for pool manager binding."""
        with patch("wifi_portal_automation.wifi_binding.get_interface_ip", return_value="10.0.0.5"):
            adapter = InterfaceBoundAdapter("wlan0")

        assert adapter.source_ip == "10.0.0.5"

    def test_socket_options_include_bindtodevice(self):
        """Test that SO_BINDTODEVICE is included in socket_options."""
        with patch("wifi_portal_automation.wifi_binding.get_interface_ip", return_value="10.0.0.5"):
            adapter = InterfaceBoundAdapter("wlan0")

        opts = adapter._socket_options()
        # Should contain (SOL_SOCKET, SO_BINDTODEVICE, b'wlan0\0')
        bindtodevice_opts = [
            o for o in opts if o[0] == socket.SOL_SOCKET and o[1] == 25
        ]
        assert len(bindtodevice_opts) == 1
        assert bindtodevice_opts[0][2] == b"wlan0\0"

    def test_init_poolmanager_passes_socket_options(self):
        """Test that init_poolmanager passes SO_BINDTODEVICE to the pool manager."""
        with patch("wifi_portal_automation.wifi_binding.get_interface_ip", return_value="10.0.0.5"):
            adapter = InterfaceBoundAdapter("wlan0")

        captured_kwargs: dict = {}

        def fake_init_poolmanager(*args, **kwargs):
            captured_kwargs.update(kwargs)

        with patch.object(HTTPAdapter, "init_poolmanager", fake_init_poolmanager):
            adapter.init_poolmanager(10, 10)

        assert captured_kwargs["source_address"] == ("10.0.0.5", 0)
        opts = captured_kwargs["socket_options"]
        bindtodevice_opts = [
            o for o in opts if o[0] == socket.SOL_SOCKET and o[1] == 25
        ]
        assert len(bindtodevice_opts) == 1
        assert bindtodevice_opts[0][2] == b"wlan0\0"


class TestCreateWifiSession:
    """Test the WiFi session factory."""

    def test_session_has_adapter_mounted(self):
        """Test that the session has the interface-bound adapter for http and https."""
        with patch("wifi_portal_automation.wifi_binding.get_interface_ip", return_value="10.0.0.5"):
            session = create_wifi_session("wlan0")

        http_adapter = session.get_adapter("http://1.1.1.1/")
        https_adapter = session.get_adapter("https://1.1.1.1/")

        assert isinstance(http_adapter, InterfaceBoundAdapter)
        assert isinstance(https_adapter, InterfaceBoundAdapter)
        assert http_adapter.source_ip == "10.0.0.5"
        assert https_adapter.source_ip == "10.0.0.5"

    def test_session_propagates_binding_error(self):
        """Test that InterfaceBindingError propagates from the adapter."""
        with (
            patch(
                "wifi_portal_automation.wifi_binding.get_interface_ip",
                side_effect=InterfaceBindingError("no ip"),
            ),
            pytest.raises(InterfaceBindingError, match="no ip"),
        ):
            create_wifi_session("wlan0")

    def test_custom_interface(self):
        """Test that a custom interface name is used."""
        with patch("wifi_portal_automation.wifi_binding.get_interface_ip", return_value="192.168.1.50"):
            session = create_wifi_session("wlan1")

        adapter = session.get_adapter("http://1.1.1.1/")
        assert adapter.interface == "wlan1"
        assert adapter.source_ip == "192.168.1.50"

    def test_auto_detect_when_interface_none(self):
        """Test that auto-detection is used when interface is None."""
        with (
            patch(
                "wifi_portal_automation.wifi_binding.detect_wifi_interface",
                return_value="wlan1",
            ),
            patch("wifi_portal_automation.wifi_binding.get_interface_ip", return_value="10.0.0.7"),
        ):
            session = create_wifi_session(None)

        adapter = session.get_adapter("http://1.1.1.1/")
        assert adapter.interface == "wlan1"
        assert adapter.source_ip == "10.0.0.7"
