"""Tests for the main controller logic."""

from unittest.mock import MagicMock, patch

from wifi_portal_automation.controller import ConnectionDecision, WiFiController


class TestConnectionDecision:
    """Test ConnectionDecision dataclass."""

    def test_basic_decision(self):
        """Test creating a basic decision."""
        decision = ConnectionDecision(
            action="none",
            reason="Already connected",
            preferred_available="dd-wrt",
            public_available=True,
        )
        assert decision.action == "none"
        assert decision.reason == "Already connected"
        assert decision.preferred_available == "dd-wrt"
        assert decision.public_available is True


class TestWiFiControllerInitialization:
    """Test controller initialization."""

    def test_default_initialization(self):
        """Test controller with default settings."""
        controller = WiFiController()
        assert controller.preferred_networks == ["dd-wrt", "dd-wrt_5G"]
        assert controller.public_network == "cmvwifi"

    def test_custom_networks(self):
        """Test controller with custom network lists."""
        controller = WiFiController(
            preferred_networks=["home-wifi", "office-wifi"],
            public_network="public-guest",
        )
        assert controller.preferred_networks == ["home-wifi", "office-wifi"]
        assert controller.public_network == "public-guest"


class TestDecideAction:
    """Test the decision logic for WiFi connections."""

    def test_connected_to_preferred_with_internet(self, controller, sample_networks):
        """When connected to preferred network - do nothing."""
        decision = controller.decide_action("dd-wrt", sample_networks)
        assert decision.action == "none"
        assert "dd-wrt" in decision.reason

    def test_connected_to_public_network(self, controller, sample_networks):
        """When connected to public network - do nothing."""
        decision = controller.decide_action("cmvwifi", sample_networks)
        assert decision.action == "none"
        assert "public network" in decision.reason

    def test_connected_to_other_network(self, controller, sample_networks):
        """When connected to unknown network - do nothing."""
        decision = controller.decide_action("Starbucks_WiFi", sample_networks)
        assert decision.action == "none"
        assert "other network" in decision.reason

    def test_not_connected_preferred_available(self, controller):
        """When not connected but preferred available - wait for autoconnect."""
        networks = [{"ssid": "dd-wrt", "signal": 80, "security": "WPA2"}]
        decision = controller.decide_action(None, networks)
        assert decision.action == "wait"
        assert "dd-wrt" in decision.reason

    def test_not_connected_only_public_available(self, controller):
        """When not connected and only public available - connect to it."""
        networks = [{"ssid": "cmvwifi", "signal": 70, "security": "Open"}]
        decision = controller.decide_action(None, networks)
        assert decision.action == "connect_cmvwifi"
        assert "cmvwifi" in decision.reason

    def test_not_connected_both_available(self, controller, sample_networks):
        """When not connected but both available - prefer preferred."""
        # Remove dd-wrt from sample to simulate only cmvwifi available
        networks = [n for n in sample_networks if n["ssid"] == "cmvwifi"]
        decision = controller.decide_action(None, networks)
        assert decision.action == "connect_cmvwifi"

    def test_not_connected_nothing_available(self, controller):
        """When not connected and no useful networks - do nothing."""
        networks = [{"ssid": "RandomNetwork", "signal": 60}]
        decision = controller.decide_action(None, networks)
        assert decision.action == "none"
        assert "No preferred or public" in decision.reason


class TestCheckCurrentState:
    """Test checking current connection state."""

    @patch("wifi_portal_automation.controller.get_connection_info")
    def test_returns_connection_info(self, mock_get_info, controller):
        """Should return connection info dict."""
        mock_get_info.return_value = {
            "connected": True,
            "ssid": "dd-wrt",
            "has_internet": True,
        }
        result = controller.check_current_state()
        assert result["ssid"] == "dd-wrt"
        assert result["has_internet"] is True


class TestConnectToPublicWifi:
    """Test connecting to public WiFi."""

    @patch("wifi_portal_automation.controller.handle_cmvwifi_connection")
    @patch.object(WiFiController, "_get_nm")
    def test_successful_connection(self, mock_get_nm, mock_handle_portal, controller):
        """Test successful connection to public WiFi."""
        mock_nm = MagicMock()
        mock_nm.connect_to_open_network.return_value = True
        mock_get_nm.return_value = mock_nm
        mock_handle_portal.return_value = True

        result = controller.connect_to_public_wifi()

        assert result is True
        mock_nm.connect_to_open_network.assert_called_once_with("cmvwifi")
        mock_handle_portal.assert_called_once()

    @patch.object(WiFiController, "_get_nm")
    def test_failed_connection(self, mock_get_nm, controller):
        """Test when connection to network fails."""
        mock_nm = MagicMock()
        mock_nm.connect_to_open_network.return_value = False
        mock_get_nm.return_value = mock_nm

        result = controller.connect_to_public_wifi()

        assert result is False

    @patch("wifi_portal_automation.controller.handle_cmvwifi_connection")
    @patch.object(WiFiController, "_get_nm")
    def test_portal_failure(self, mock_get_nm, mock_handle_portal, controller):
        """Test when connection succeeds but portal handling fails."""
        mock_nm = MagicMock()
        mock_nm.connect_to_open_network.return_value = True
        mock_get_nm.return_value = mock_nm
        mock_handle_portal.return_value = False

        result = controller.connect_to_public_wifi()

        assert result is False


class TestRunOnce:
    """Test the main run_once cycle."""

    @patch("wifi_portal_automation.controller.get_connection_info")
    @patch.object(WiFiController, "_get_nm")
    def test_already_on_preferred_with_internet(self, mock_get_nm, mock_get_info, controller):
        """When already on preferred with internet - no action needed."""
        mock_get_info.return_value = {
            "connected": True,
            "ssid": "dd-wrt",
            "has_internet": True,
        }

        result = controller.run_once()

        assert result is True
        # Should not scan or connect
        mock_get_nm.return_value.scan_wifi_networks.assert_not_called()

    @patch("wifi_portal_automation.controller.get_connection_info")
    @patch.object(WiFiController, "connect_to_public_wifi")
    @patch.object(WiFiController, "_get_nm")
    def test_connects_when_decision_is_connect_cmvwifi(
        self, mock_get_nm, mock_connect, mock_get_info, controller
    ):
        """Should connect when decision is to connect to cmvwifi."""
        mock_get_info.return_value = {
            "connected": False,
            "ssid": None,
            "has_internet": False,
        }
        mock_nm = MagicMock()
        mock_nm.scan_wifi_networks.return_value = [{"ssid": "cmvwifi", "signal": 70}]
        mock_get_nm.return_value = mock_nm
        mock_connect.return_value = True

        result = controller.run_once()

        assert result is True
        mock_connect.assert_called_once()

    @patch("wifi_portal_automation.controller.get_connection_info")
    @patch.object(WiFiController, "_get_nm")
    def test_handles_network_manager_error(self, mock_get_nm, mock_get_info, controller):
        """Should handle NetworkManager errors gracefully."""
        from wifi_portal_automation.network_manager import NetworkManagerError

        mock_get_info.return_value = {
            "connected": False,
            "ssid": None,
            "has_internet": False,
        }
        mock_get_nm.side_effect = NetworkManagerError("Test error")

        result = controller.run_once()

        assert result is False

    @patch("wifi_portal_automation.controller.handle_cmvwifi_connection")
    @patch("wifi_portal_automation.controller.get_connection_info")
    @patch.object(WiFiController, "_get_nm")
    def test_handles_portal_when_on_public_no_internet(
        self, mock_get_nm, mock_get_info, mock_handle_portal, controller
    ):
        """When on cmvwifi but no internet, handle captive portal."""
        mock_get_info.return_value = {
            "connected": True,
            "ssid": "cmvwifi",
            "has_internet": False,
        }
        mock_handle_portal.return_value = True

        result = controller.run_once()

        assert result is True
        mock_handle_portal.assert_called_once()
