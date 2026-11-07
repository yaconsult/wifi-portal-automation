"""NetworkManager D-Bus interface for WiFi operations."""

import subprocess
import time

try:
    import dbus
except ImportError:
    dbus = None


class NetworkManagerError(Exception):
    """NetworkManager operation error."""

    pass


class NetworkManager:
    """Interface to NetworkManager via D-Bus."""

    # Well-known D-Bus names
    NM_SERVICE = "org.freedesktop.NetworkManager"
    NM_PATH = "/org/freedesktop/NetworkManager"
    NM_SETTINGS_PATH = "/org/freedesktop/NetworkManager/Settings"
    NM_DEVICE_INTERFACE = "org.freedesktop.NetworkManager.Device"

    # NM Device types (from NetworkManager.h)
    NM_DEVICE_TYPE_ETHERNET = 1
    NM_DEVICE_TYPE_WIFI = 2
    NM_DEVICE_WIFI_INTERFACE = "org.freedesktop.NetworkManager.Device.Wireless"
    NM_AP_INTERFACE = "org.freedesktop.NetworkManager.AccessPoint"
    NM_ACTIVE_CONNECTION_INTERFACE = "org.freedesktop.NetworkManager.Connection.Active"

    def __init__(self) -> None:
        """Initialize D-Bus connection to NetworkManager."""
        if dbus is None:
            raise NetworkManagerError("dbus-python not installed")

        try:
            self._bus = dbus.SystemBus()
            self._nm = self._bus.get_object(self.NM_SERVICE, self.NM_PATH)
            self._nm_interface = dbus.Interface(self._nm, self.NM_SERVICE)
        except dbus.DBusException as e:
            raise NetworkManagerError(f"Failed to connect to NetworkManager: {e}") from e

    def get_active_connection_ssid(self) -> str | None:
        """Get SSID of currently active WiFi connection.

        Returns:
            SSID string or None if not connected to WiFi.
        """
        try:
            # Use Properties interface to get ActiveConnections
            nm_props = dbus.Interface(self._nm, "org.freedesktop.DBus.Properties")
            active_path = nm_props.Get("org.freedesktop.NetworkManager", "ActiveConnections")
            if not active_path:
                return None

            for conn_path in active_path:
                conn_obj = self._bus.get_object(self.NM_SERVICE, conn_path)
                conn_iface = dbus.Interface(conn_obj, "org.freedesktop.DBus.Properties")

                # Get connection type
                try:
                    conn_type = conn_iface.Get(self.NM_ACTIVE_CONNECTION_INTERFACE, "Type")
                except dbus.DBusException:
                    continue

                if conn_type != "802-11-wireless":
                    continue

                # Get the connection object
                try:
                    conn_path_obj = conn_iface.Get(
                        self.NM_ACTIVE_CONNECTION_INTERFACE, "Connection"
                    )
                except dbus.DBusException:
                    continue

                settings_obj = self._bus.get_object(self.NM_SERVICE, conn_path_obj)
                settings_iface = dbus.Interface(
                    settings_obj, "org.freedesktop.NetworkManager.Settings.Connection"
                )

                try:
                    settings = settings_iface.GetSettings()
                except dbus.DBusException:
                    continue

                # Extract SSID from settings
                if "802-11-wireless" in settings:
                    ssid_bytes = settings["802-11-wireless"]["ssid"]
                    return "".join(chr(b) for b in ssid_bytes)

            return None
        except dbus.DBusException:
            return None

    def scan_wifi_networks(self, timeout: float = 10.0) -> list[dict[str, object]]:
        """Scan for available WiFi networks.

        Args:
            timeout: Maximum time to wait for scan completion.

        Returns:
            List of network info dicts with 'ssid', 'signal', 'security'.
        """
        try:
            # Find WiFi device
            devices = self._nm.Get("org.freedesktop.NetworkManager", "Devices")
            wifi_device_path = None

            for dev_path in devices:
                dev_obj = self._bus.get_object(self.NM_SERVICE, dev_path)
                dev_iface = dbus.Interface(dev_obj, "org.freedesktop.DBus.Properties")
                dev_type = dev_iface.Get(self.NM_DEVICE_INTERFACE, "DeviceType")
                if dev_type == self.NM_DEVICE_TYPE_WIFI:
                    wifi_device_path = dev_path
                    break

            if not wifi_device_path:
                return []

            # Request scan
            wifi_obj = self._bus.get_object(self.NM_SERVICE, wifi_device_path)
            wifi_iface = dbus.Interface(wifi_obj, self.NM_DEVICE_WIFI_INTERFACE)

            # Get current access points first
            old_aps = set(wifi_iface.GetAccessPoints())

            # Request new scan
            wifi_iface.RequestScan({})

            # Wait for scan to complete
            start_time = time.time()
            new_aps = set()
            while time.time() - start_time < timeout:
                current_aps = set(wifi_iface.GetAccessPoints())
                new_aps = current_aps - old_aps
                if new_aps:
                    break
                time.sleep(0.5)

            # Collect all access point info
            networks = []
            all_aps = wifi_iface.GetAccessPoints()

            for ap_path in all_aps:
                try:
                    ap_obj = self._bus.get_object(self.NM_SERVICE, ap_path)
                    ap_iface = dbus.Interface(ap_obj, "org.freedesktop.DBus.Properties")

                    ssid_bytes = ap_iface.Get(self.NM_AP_INTERFACE, "Ssid")
                    ssid = "".join(chr(b) for b in ssid_bytes)
                    signal = int(ap_iface.Get(self.NM_AP_INTERFACE, "Strength"))
                    flags = int(ap_iface.Get(self.NM_AP_INTERFACE, "Flags"))
                    wpa_flags = int(ap_iface.Get(self.NM_AP_INTERFACE, "WpaFlags"))
                    rsn_flags = int(ap_iface.Get(self.NM_AP_INTERFACE, "RsnFlags"))

                    # Determine security
                    has_security = (flags & 0x1) or wpa_flags or rsn_flags
                    security = (
                        "WPA2"
                        if rsn_flags
                        else ("WPA" if wpa_flags else ("WEP" if flags & 0x1 else "Open"))
                    )

                    networks.append(
                        {
                            "ssid": ssid,
                            "signal": signal,
                            "security": security,
                            "path": str(ap_path),
                            "has_security": bool(has_security),
                        }
                    )
                except dbus.DBusException:
                    continue

            # Sort by signal strength
            networks.sort(key=lambda x: int(x["signal"]), reverse=True)  # type: ignore[call-overload]
            return networks

        except dbus.DBusException as e:
            raise NetworkManagerError(f"Scan failed: {e}") from e

    def connect_to_open_network(self, ssid: str) -> bool:
        """Connect to an open (no password) WiFi network.

        Args:
            ssid: The network SSID.

        Returns:
            True if connection successful.
        """
        try:
            # Use nmcli for simpler connection to open networks
            result = subprocess.run(
                ["nmcli", "device", "wifi", "connect", ssid],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            return False
        except FileNotFoundError:
            raise NetworkManagerError("nmcli not found. Install NetworkManager.") from None

    def is_connected_to_internet(self, test_host: str = "8.8.8.8", timeout: int = 3) -> bool:
        """Check if we have actual internet connectivity.

        Args:
            test_host: IP to ping for connectivity check.
            timeout: Ping timeout in seconds.

        Returns:
            True if internet is reachable.
        """
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(timeout), test_host],
                capture_output=True,
                timeout=timeout + 2,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False


def get_connection_info() -> dict[str, object]:
    """Get current connection information.

    Returns:
        Dict with 'connected', 'ssid', 'has_internet' keys.
    """
    try:
        nm = NetworkManager()
        ssid = nm.get_active_connection_ssid()

        result: dict[str, object] = {
            "connected": ssid is not None,
            "ssid": ssid,
            "has_internet": False,
        }

        if ssid:
            result["has_internet"] = nm.is_connected_to_internet()

        return result
    except NetworkManagerError as e:
        return {"connected": False, "ssid": None, "has_internet": False, "error": str(e)}


def find_network(ssid: str, timeout: float = 10.0) -> dict[str, object] | None:
    """Scan for a specific network.

    Args:
        ssid: Network SSID to find.
        timeout: Scan timeout.

    Returns:
        Network info dict if found, None otherwise.
    """
    try:
        nm = NetworkManager()
        networks = nm.scan_wifi_networks(timeout=timeout)
        for net in networks:
            if net["ssid"] == ssid:
                return net
        return None
    except NetworkManagerError:
        return None
