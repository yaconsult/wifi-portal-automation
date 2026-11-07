#!/usr/bin/env python3
"""Test SO_BINDTODEVICE from Termux.

SO_BINDTODEVICE forces the kernel to route packets through a specific
network interface, bypassing policy routing.  It requires CAP_NET_RAW,
which the `shell` user has but regular apps may not.

This script tests whether we can use SO_BINDTODEVICE from Termux.
"""

import socket

# SO_BINDTODEVICE socket option number (Linux)
SO_BINDTODEVICE = 25


def test_bindtodevice():
    """Test if SO_BINDTODEVICE works from this process."""
    print("=== SO_BINDTODEVICE test ===")
    print()

    for iface in ["wlan0", "wlan1"]:
        print(f"Testing {iface}...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            iface_bytes = iface.encode("utf-8") + b"\0"
            sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, iface_bytes)
            print("  SO_BINDTODEVICE: OK (set on socket)")

            # Try connecting to see if it actually routes
            try:
                sock.connect(("8.8.8.8", 53))
                local = sock.getsockname()
                print(f"  Connected: local IP = {local[0]}")
            except OSError as e:
                print(f"  Connect failed: {e}")

            sock.close()
        except PermissionError as e:
            print(f"  SO_BINDTODEVICE: PERMISSION DENIED ({e})")
            print("  -> Need root or CAP_NET_RAW")
        except OSError as e:
            print(f"  SO_BINDTODEVICE: error ({e})")
        print()

    # Also test with a TCP socket (what requests/urllib3 uses)
    print("=== TCP socket test ===")
    for iface in ["wlan0", "wlan1"]:
        print(f"Testing TCP {iface}...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            iface_bytes = iface.encode("utf-8") + b"\0"
            sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, iface_bytes)
            print("  SO_BINDTODEVICE: OK")
            sock.settimeout(5)
            try:
                sock.connect(("1.1.1.1", 80))
                local = sock.getsockname()
                print(f"  Connected: local IP = {local[0]}")
                # Try a simple HTTP request
                sock.sendall(b"GET / HTTP/1.0\r\nHost: 1.1.1.1\r\n\r\n")
                data = sock.recv(1024)
                print(f"  Response: {data[:100]}")
            except OSError as e:
                print(f"  Connect/recv: {e}")
            sock.close()
        except PermissionError as e:
            print(f"  PERMISSION DENIED ({e})")
        except OSError as e:
            print(f"  Error: {e}")
        print()

    # Test if we have root (su)
    print("=== Root check ===")
    import subprocess

    try:
        result = subprocess.run(["id"], capture_output=True, text=True)
        print(f"  id: {result.stdout.strip()}")
    except Exception as e:
        print(f"  id failed: {e}")

    try:
        result = subprocess.run(
            ["su", "-c", "id"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        print(f"  su id: {result.stdout.strip()}")
        print("  Root available: yes")
    except Exception as e:
        print(f"  su not available: {e}")

    print()
    print("Done. Paste this output back.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--log-file":
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            test_bindtodevice()
        output = buf.getvalue()
        with open(sys.argv[2], "w") as f:
            f.write(output)
        print(f"Written to {sys.argv[2]}")
    else:
        test_bindtodevice()
