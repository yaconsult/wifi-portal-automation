"""Main controller for WiFi Portal Automation."""

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from wifi_portal_automation.captive_portal import CaptivePortalError, handle_cmvwifi_connection
from wifi_portal_automation.config import DEFAULT_CONFIG, resolve_config
from wifi_portal_automation.network_manager import (
    NetworkManager,
    NetworkManagerError,
    get_connection_info,
)

# Configuration constants — built-in example defaults; override via
# --config pointing at a networks.toml.
PREFERRED_NETWORKS = [
    s for s in DEFAULT_CONFIG.preferred if s not in DEFAULT_CONFIG.preferred_open
]
PUBLIC_NETWORK = DEFAULT_CONFIG.preferred_open[0]

SCAN_TIMEOUT = 15.0  # seconds to wait for WiFi scan
CONNECT_TIMEOUT = 30  # seconds to wait for connection


# Logging setup
def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging.

    Args:
        verbose: Enable debug logging.

    Returns:
        Configured logger.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("wifi_portal_automation")


@dataclass
class ConnectionDecision:
    """Decision result for connection handling."""

    action: str  # "none", "connect_cmvwifi", "wait"
    reason: str
    preferred_available: str | None = None
    public_available: bool = False


class WiFiController:
    """Controller for automated WiFi connection management."""

    def __init__(
        self,
        preferred_networks: list[str] | None = None,
        public_network: str = PUBLIC_NETWORK,
        logger: logging.Logger | None = None,
    ):
        """Initialize controller.

        Args:
            preferred_networks: List of preferred private network SSIDs.
            public_network: SSID of the public network requiring captive portal.
            logger: Logger instance.
        """
        self.preferred_networks = preferred_networks or PREFERRED_NETWORKS
        self.public_network = public_network
        self.logger = logger or logging.getLogger("wifi_portal_automation")
        self._nm: NetworkManager | None = None

    def _get_nm(self) -> NetworkManager:
        """Get or create NetworkManager instance."""
        if self._nm is None:
            self._nm = NetworkManager()
        return self._nm

    def check_current_state(self) -> dict[str, object]:
        """Check current WiFi connection state.

        Returns:
            Dict with connection info.
        """
        return get_connection_info()

    def decide_action(
        self, current_ssid: str | None, available_networks: list[dict[str, object]]
    ) -> ConnectionDecision:
        """Decide what action to take based on current state and available networks.

        Logic:
        1. If connected to a preferred network with internet -> do nothing
        2. If connected to public network with internet -> do nothing
        3. If connected but no internet -> might need captive portal handling
        4. If not connected:
           - If preferred network available -> let NetworkManager handle (do nothing)
           - If only public network available -> connect to it
           - If neither -> do nothing

        Args:
            current_ssid: Currently connected SSID or None.
            available_networks: List of available network info dicts.

        Returns:
            ConnectionDecision with recommended action.
        """
        # Extract SSIDs from available networks
        available_ssids = {net["ssid"] for net in available_networks}

        # Check which preferred networks are available
        preferred_available = None
        for pref in self.preferred_networks:
            if pref in available_ssids:
                preferred_available = pref
                break

        public_available = self.public_network in available_ssids

        # If already connected to preferred network with internet
        if current_ssid in self.preferred_networks:
            return ConnectionDecision(
                action="none",
                reason=f"Already connected to preferred network: {current_ssid}",
                preferred_available=current_ssid,
                public_available=public_available,
            )

        # If already connected to public network
        if current_ssid == self.public_network:
            return ConnectionDecision(
                action="none",
                reason="Already connected to public network",
                preferred_available=preferred_available,
                public_available=True,
            )

        # If connected to some other network
        if current_ssid is not None:
            return ConnectionDecision(
                action="none",
                reason=f"Connected to other network: {current_ssid}",
                preferred_available=preferred_available,
                public_available=public_available,
            )

        # Not connected - check what's available
        if preferred_available:
            # Preferred network available - let NetworkManager autoconnect
            return ConnectionDecision(
                action="wait",
                reason=f"Preferred network '{preferred_available}' available, waiting for autoconnect",
                preferred_available=preferred_available,
                public_available=public_available,
            )

        if public_available:
            # Only public network available - connect to it
            return ConnectionDecision(
                action="connect_cmvwifi",
                reason=f"No preferred networks available, connecting to {self.public_network}",
                preferred_available=None,
                public_available=True,
            )

        # Nothing useful available
        return ConnectionDecision(
            action="none",
            reason="No preferred or public networks available",
            preferred_available=None,
            public_available=False,
        )

    def connect_to_public_wifi(self) -> bool:
        """Connect to public WiFi and handle captive portal.

        Returns:
            True if successfully connected with internet access.
        """
        self.logger.info(f"Connecting to {self.public_network}...")

        try:
            nm = self._get_nm()

            # Connect to open network
            if not nm.connect_to_open_network(self.public_network):
                self.logger.error(f"Failed to connect to {self.public_network}")
                return False

            self.logger.info(f"Connected to {self.public_network}, handling captive portal...")

            # Handle captive portal
            if handle_cmvwifi_connection():
                self.logger.info("Successfully connected with internet access")
                return True
            else:
                self.logger.error("Failed to complete captive portal login")
                return False

        except (NetworkManagerError, CaptivePortalError) as e:
            self.logger.error(f"Connection failed: {e}")
            return False

    def run_once(self) -> bool:
        """Run a single connection check cycle.

        Returns:
            True if operation succeeded (connected or no action needed).
        """
        try:
            # Check current connection state
            state = self.check_current_state()
            current_ssid = state.get("ssid")
            has_internet = state.get("has_internet", False)

            self.logger.debug(f"Current state: ssid={current_ssid}, internet={has_internet}")

            # If connected to preferred network with internet, we're good
            if current_ssid in self.preferred_networks and has_internet:
                self.logger.debug(f"Connected to preferred network '{current_ssid}' with internet")
                return True

            # If connected to public network with internet, we're good
            if current_ssid == self.public_network and has_internet:
                self.logger.debug("Connected to public network with internet")
                return True

            # If connected to public network but no internet, handle captive portal
            if current_ssid == self.public_network and not has_internet:
                self.logger.info(
                    "Connected to public network but no internet, handling captive portal..."
                )
                return handle_cmvwifi_connection()

            # Scan for available networks
            self.logger.debug("Scanning for WiFi networks...")
            nm = self._get_nm()
            available = nm.scan_wifi_networks(timeout=SCAN_TIMEOUT)

            if not available:
                self.logger.debug("No networks found in scan")
                return True  # Not an error, just no networks

            self.logger.debug(f"Found {len(available)} networks: {[n['ssid'] for n in available]}")

            # Decide action
            decision = self.decide_action(
                current_ssid if isinstance(current_ssid, str) else None, available
            )
            self.logger.info(f"Decision: {decision.action} - {decision.reason}")

            # Execute decision
            if decision.action == "connect_cmvwifi":
                return self.connect_to_public_wifi()
            elif decision.action == "wait":
                # Give NetworkManager a moment to autoconnect
                time.sleep(5)
                # Re-check state
                new_state = self.check_current_state()
                if new_state.get("ssid") == decision.preferred_available:
                    return True
                # If still not connected, we'll try again next cycle
                return True
            else:
                # No action needed
                return True

        except NetworkManagerError as e:
            self.logger.error(f"NetworkManager error: {e}")
            return False
        except Exception as e:
            self.logger.exception(f"Unexpected error: {e}")
            return False

    def run_daemon(self, interval: int = 60) -> None:
        """Run as daemon with periodic checks.

        Args:
            interval: Seconds between checks.
        """
        self.logger.info(f"Starting daemon mode (interval={interval}s)")

        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                self.logger.info("Stopping daemon")
                break
            except Exception as e:
                self.logger.exception(f"Error in daemon loop: {e}")

            time.sleep(interval)


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success).
    """
    parser = argparse.ArgumentParser(
        description="Auto-connect to a preferred open WiFi network with captive portal handling"
    )
    parser.add_argument("--daemon", "-d", action="store_true", help="Run in daemon mode")
    parser.add_argument(
        "--interval", "-i", type=int, default=60, help="Check interval in seconds (daemon mode)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="networks.toml overriding the built-in example SSID lists",
    )

    args = parser.parse_args()

    logger = setup_logging(args.verbose)

    config = resolve_config(Path(args.config) if args.config else None)
    controller = WiFiController(
        preferred_networks=[
            s for s in config.preferred if s not in config.preferred_open
        ],
        public_network=(
            config.preferred_open[0] if config.preferred_open else PUBLIC_NETWORK
        ),
        logger=logger,
    )

    if args.daemon:
        controller.run_daemon(interval=args.interval)
        return 0
    else:
        success = controller.run_once()
        return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
