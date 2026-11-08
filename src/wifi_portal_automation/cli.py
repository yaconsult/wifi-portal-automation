"""CLI entry point for wpa-auto."""

import sys

from wifi_portal_automation.controller import main

if __name__ == "__main__":
    sys.exit(main())
