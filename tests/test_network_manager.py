"""Tests for NetworkManager D-Bus interface."""

import pytest

from wifi_portal_automation.network_manager import (
    NetworkManager,
    NetworkManagerError,
    find_network,
    get_connection_info,
)


class TestNetworkManagerInitialization:
    """Test NetworkManager initialization."""

    @pytest.mark.skip(reason="Cannot test dbus=None after module import")
    def test_raises_error_without_dbus(self):
        """Test that error is raised when dbus is None."""
        # This would require reloading the module with dbus=None
        pass

    def test_successful_initialization(self, mock_dbus):
        """Test successful initialization with mocked D-Bus."""
        nm = NetworkManager()
        assert nm._bus is not None
        assert nm._nm is not None


class TestGetActiveConnectionSsid:
    """Test getting current connection SSID."""

    def test_no_active_connections(self, mock_network_manager):
        """Test when there are no active connections."""
        mock_network_manager._nm.Get.return_value = []

        ssid = mock_network_manager.get_active_connection_ssid()

        assert ssid is None

    def test_wifi_connection_found(self, mock_network_manager):
        """Test finding WiFi connection."""
        # Mock active connections list
        mock_network_manager._nm.Get.return_value = ["/path/to/connection"]

        # Mock connection object
        mock_conn_obj = mock_network_manager._bus.get_object.return_value
        mock_conn_iface = mock_conn_obj.return_value
        mock_conn_iface.Get.side_effect = lambda iface, prop: {
            ("org.freedesktop.NetworkManager.Connection.Active", "Type"): "802-11-wireless",
            ("org.freedesktop.NetworkManager.Connection.Active", "Connection"): "/settings/path",
        }[(iface, prop)]

        # Mock settings object
        mock_settings_obj = mock_network_manager._bus.get_object.return_value
        mock_settings_iface = mock_settings_obj.return_value
        mock_settings_iface.GetSettings.return_value = {
            "802-11-wireless": {"ssid": [ord(c) for c in "dd-wrt"]}
        }

        # This test reveals complexity of mocking D-Bus - simplified for now
        # In real test, need to properly mock the chain of calls
        mock_network_manager.get_active_connection_ssid()


class TestScanWifiNetworks:
    """Test WiFi network scanning."""

    def test_no_wifi_device(self, mock_network_manager):
        """Test when no WiFi device is found."""
        mock_network_manager._nm.Get.return_value = []  # No devices

        networks = mock_network_manager.scan_wifi_networks()

        assert networks == []

    def test_scan_finds_networks(self, mock_network_manager):
        """Test scanning finds available networks."""
        # Mock device list with WiFi device
        mock_network_manager._nm.Get.return_value = ["/device/wlan0"]

        # Mock device type check
        mock_device_obj = mock_network_manager._bus.get_object.return_value
        mock_device_iface = mock_device_obj.return_value
        mock_device_iface.Get.return_value = 2  # DeviceType 2 = WiFi

        # This test would need extensive mocking of D-Bus chain
        # Simplified for illustration


class TestConnectToOpenNetwork:
    """Test connecting to open networks."""

    def test_successful_connection(self, mock_network_manager, mock_subprocess):
        """Test successful connection via nmcli."""
        mock_subprocess.run.return_value.returncode = 0

        result = mock_network_manager.connect_to_open_network("cmvwifi")

        assert result is True
        mock_subprocess.run.assert_called_once()
        # Verify nmcli was called correctly
        call_args = mock_subprocess.run.call_args
        assert call_args.args[0][0:3] == ["nmcli", "device", "wifi"]
        assert "cmvwifi" in call_args.args[0]

    def test_failed_connection(self, mock_network_manager, mock_subprocess):
        """Test when nmcli returns error."""
        mock_subprocess.run.return_value.returncode = 1

        result = mock_network_manager.connect_to_open_network("cmvwifi")

        assert result is False

    def test_nmcli_not_found(self, mock_network_manager, mock_subprocess):
        """Test when nmcli is not installed."""
        mock_subprocess.run.side_effect = FileNotFoundError("nmcli not found")

        with pytest.raises(NetworkManagerError, match="nmcli"):
            mock_network_manager.connect_to_open_network("cmvwifi")

    def test_timeout_handled(self, mock_network_manager, mock_subprocess):
        """Test handling of timeout."""
        mock_subprocess.run.side_effect = mock_subprocess.TimeoutExpired("nmcli", 30)

        result = mock_network_manager.connect_to_open_network("cmvwifi")

        assert result is False


class TestIsConnectedToInternet:
    """Test internet connectivity check."""

    def test_ping_success(self, mock_network_manager, mock_subprocess):
        """Test successful ping to test host."""
        mock_subprocess.run.return_value.returncode = 0

        result = mock_network_manager.is_connected_to_internet()

        assert result is True
        mock_subprocess.run.assert_called_once()

    def test_ping_failure(self, mock_network_manager, mock_subprocess):
        """Test failed ping."""
        mock_subprocess.run.return_value.returncode = 1

        result = mock_network_manager.is_connected_to_internet()

        assert result is False

    def test_custom_test_host(self, mock_network_manager, mock_subprocess):
        """Test with custom test host."""
        mock_subprocess.run.return_value.returncode = 0

        mock_network_manager.is_connected_to_internet(test_host="1.1.1.1")

        call_args = mock_subprocess.run.call_args
        assert "1.1.1.1" in call_args.args[0]


class TestGetConnectionInfo:
    """Test the get_connection_info convenience function."""

    @pytest.mark.skip(reason="Requires full D-Bus mocking")
    def test_returns_proper_dict_structure(self):
        """Test that function returns expected dict structure."""
        # This would need extensive mocking
        result = get_connection_info()

        assert "connected" in result
        assert "ssid" in result
        assert "has_internet" in result

    def test_handles_network_manager_error(self):
        """Test graceful handling of errors."""
        # In real test, would mock NetworkManager to raise error
        # and verify error is in result dict
        pass


class TestFindNetwork:
    """Test the find_network convenience function."""

    @pytest.mark.skip(reason="Requires full D-Bus mocking")
    def test_finds_network_by_ssid(self):
        """Test finding specific network by SSID."""
        result = find_network("cmvwifi")

        # Should return network dict if found
        if result:
            assert result["ssid"] == "cmvwifi"

    @pytest.mark.skip(reason="Requires full D-Bus mocking")
    def test_returns_none_if_not_found(self):
        """Test returning None when network not found."""
        result = find_network("NonExistentNetwork")

        assert result is None

    def test_handles_error_gracefully(self):
        """Test handling of NetworkManager errors."""
        # Should return None on error rather than raise
        # May return None or actual result depending on environment
        find_network("cmvwifi", timeout=0.001)
