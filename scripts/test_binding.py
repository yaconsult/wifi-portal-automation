#!/usr/bin/env python3
"""Test whether interface binding actually bypasses cellular routing.

Run this with cellular ON and cmvwifi associated.  It creates a
WiFi-bound session and checks if requests actually go through WiFi.
"""

import socket

from wifi_portal_automation.wifi_binding import create_wifi_session


def main():
    print("=== Interface binding test ===")
    print()

    # Create the WiFi-bound session
    try:
        s = create_wifi_session("wlan1")
        adapter = s.get_adapter("http://")
        print(f"Source IP: {adapter.source_ip}")
        print(f"Interface: {adapter.interface}")
    except Exception as e:
        print(f"Failed to create session: {e}")
        return

    print()

    # Test 1: Check what local IP a plain socket uses (no binding)
    print("=== Plain socket (no binding) ===")
    try:
        plain = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        plain.connect(("8.8.8.8", 53))
        print(f"  Plain socket local IP: {plain.getsockname()[0]}")
        plain.close()
    except OSError as e:
        print(f"  Plain socket error: {e}")

    print()

    # Test 2: Check what local IP a bound socket uses
    print("=== Bound socket (to wlan1 IP) ===")
    try:
        bound = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bound.bind((adapter.source_ip, 0))
        bound.connect(("8.8.8.8", 53))
        print(f"  Bound socket local IP: {bound.getsockname()[0]}")
        bound.close()
    except OSError as e:
        print(f"  Bound socket error: {e}")

    print()

    # Test 3: HTTP request through the bound session
    print("=== HTTP request through bound session ===")
    try:
        r = s.get("http://1.1.1.1/", timeout=10, allow_redirects=False)
        print(f"  Status: {r.status_code}")
        print(f"  Location: {r.headers.get('Location', 'none')}")
        print(f"  Response URL: {r.url}")
    except Exception as e:
        print(f"  HTTP error: {e}")

    print()

    # Test 4: HTTP request through plain requests (no binding)
    print("=== HTTP request through plain requests ===")
    try:
        import requests

        r2 = requests.get("http://1.1.1.1/", timeout=10, allow_redirects=False)
        print(f"  Status: {r2.status_code}")
        print(f"  Location: {r2.headers.get('Location', 'none')}")
        print(f"  Response URL: {r2.url}")
    except Exception as e:
        print(f"  HTTP error: {e}")

    print()
    print("Done. Paste this output back.")


if __name__ == "__main__":
    main()
