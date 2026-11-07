#!/usr/bin/env python3
"""Detect WiFi interface and IP address on Android/Termux.

Run this after cmvwifi associates (you see "sign in to network" but
haven't pressed accept).  It tries multiple methods to find the
WiFi interface name and IP, since `ip addr` is blocked on Android.
"""

import fcntl
import socket
import struct

SIOCGIFADDR = 0x8915


def try_ioctl():
    """Try SIOCGIFADDR ioctl on common WiFi interface names."""
    print("=== ioctl test (SIOCGIFADDR) ===")
    for iface in ["wlan0", "wlan1", "wlan", "wlan2"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            packed = fcntl.ioctl(
                s.fileno(),
                SIOCGIFADDR,
                struct.pack("256s", iface[:15].encode()),
            )
            ip = socket.inet_ntoa(packed[20:24])
            s.close()
            print(f"  {iface}: {ip}")
        except OSError as e:
            print(f"  {iface}: {e}")
    print()


def try_proc_fib_trie():
    """Try reading /proc/net/fib_trie for local IP addresses."""
    print("=== /proc/net/fib_trie ===")
    try:
        with open("/proc/net/fib_trie") as f:
            lines = f.readlines()
        # Look for IP addresses in the routing trie
        for _i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("10.") or stripped.startswith("192.168") or stripped.startswith("172."):
                print(f"  {stripped}")
    except OSError as e:
        print(f"  cannot read: {e}")
    print()


def try_proc_tcp():
    """Try reading /proc/net/tcp for local IP addresses."""
    print("=== /proc/net/tcp ===")
    try:
        with open("/proc/net/tcp") as f:
            lines = f.readlines()
        print(f"  {len(lines)} lines total")
        for line in lines[:10]:
            print(f"  {line.rstrip()}")
    except OSError as e:
        print(f"  cannot read: {e}")
    print()


def try_proc_if_inet6():
    """Try reading /proc/net/if_inet6 for interface names."""
    print("=== /proc/net/if_inet6 ===")
    try:
        with open("/proc/net/if_inet6") as f:
            for line in f:
                print(f"  {line.rstrip()}")
    except OSError as e:
        print(f"  cannot read: {e}")
    print()


def try_socket_getsockname():
    """Try connecting a UDP socket to see what local IP is used."""
    print("=== UDP socket getsockname ===")
    for target in ["10.0.0.1", "8.8.8.8", "1.1.1.1"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 53))
            local_ip = s.getsockname()[0]
            s.close()
            print(f"  connect to {target}: local IP = {local_ip}")
        except OSError as e:
            print(f"  connect to {target}: {e}")
    print()


if __name__ == "__main__":
    try_ioctl()
    try_proc_fib_trie()
    try_proc_tcp()
    try_proc_if_inet6()
    try_socket_getsockname()
    print("Done. Paste this output back so we can fix the binding.")
