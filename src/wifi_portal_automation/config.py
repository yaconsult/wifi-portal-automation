"""Network-list configuration for WiFi Portal Automation.

Every tool ships with a built-in example network list (the public
WiFi SSIDs used throughout the docs and tests) so it works
out-of-the-box as a demonstration.  Real deployments point the
``--config`` flag at their own ``networks.toml``::

    wpa-nudge --config ~/networks.toml --preferred --fallback "xfinitywifi"
    wpa-auto  --config ~/networks.toml

Schema (see ``networks.example.toml`` for a commented copy)::

    # Every network that should win over the fallback when visible —
    # open and secured SSIDs alike.
    preferred = ["cmvwifi", "My Home WiFi 5G"]

    # The subset of `preferred` that is *open* — networks a script can
    # join via `cmd wifi connect-network <ssid> open` without storing
    # a passphrase.  Secured entries belong only in `preferred`.
    preferred_open = ["cmvwifi"]

    # The open fallback joined only when nothing preferred is visible.
    fallback = "xfinitywifi"

    # SSIDs with a captive portal the nudge can re-handle.  When
    # `wpa-nudge --recheck-portal` finds the phone connected to one
    # of these but the network unvalidated (CAPTIVE_PORTAL), it
    # re-runs the portal handler instead of exiting already_connected.
    portal_ssids = ["cmvwifi", "MVwifi"]
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NetworkConfig:
    """The user's ranked network lists.

    ``preferred`` holds every SSID that outranks the fallback —
    including secured home networks that only Android's own selector
    can join.  ``preferred_open`` is the subset joinable without a
    stored passphrase.
    """

    preferred: tuple[str, ...]
    preferred_open: tuple[str, ...]
    fallback: str
    portal_ssids: tuple[str, ...] = ()


# Built-in example list — the public WiFi deployment used throughout
# the docs and tests.  Overridable via --config.
DEFAULT_CONFIG = NetworkConfig(
    preferred=(
        "cmvwifi",
        "MVwifi",
        "Costco Member Wifi",
        "SHGuestNet",
        "dd-wrt",
        "dd-wrt_5G",
    ),
    preferred_open=(
        "cmvwifi",
        "MVwifi",
        "Costco Member Wifi",
        "SHGuestNet",
    ),
    fallback="xfinitywifi",
    portal_ssids=("cmvwifi", "MVwifi"),
)


def load_config(path: Path) -> NetworkConfig:
    """Load a ``networks.toml`` file.

    Args:
        path: TOML file with ``preferred``, ``preferred_open`` and
            ``fallback`` keys (see module docstring).

    Returns:
        Parsed config.  Missing keys fall back to
        :data:`DEFAULT_CONFIG` values so partial files work.

    Raises:
        ValueError: If a present key has the wrong shape, or an open
            SSID is missing from ``preferred``.
    """
    data = tomllib.loads(Path(path).read_text())

    def _list(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        value = data.get(key)
        if value is None:
            return default
        if not isinstance(value, list) or not all(
            isinstance(s, str) for s in value
        ):
            raise ValueError(f"{key} must be a list of SSID strings")
        return tuple(value)

    fallback = data.get("fallback", DEFAULT_CONFIG.fallback)
    if not isinstance(fallback, str):
        raise ValueError("fallback must be a single SSID string")

    preferred_open = _list("preferred_open", DEFAULT_CONFIG.preferred_open)
    # An unspecified `preferred` defaults to the open list plus the
    # built-in defaults, keeping preferred_open ⊆ preferred automatic.
    preferred_default = tuple(
        dict.fromkeys(preferred_open + DEFAULT_CONFIG.preferred)
    )
    cfg = NetworkConfig(
        preferred=_list("preferred", preferred_default),
        preferred_open=preferred_open,
        fallback=fallback,
        portal_ssids=_list("portal_ssids", DEFAULT_CONFIG.portal_ssids),
    )
    missing = set(cfg.preferred_open) - set(cfg.preferred)
    if missing:
        raise ValueError(
            f"preferred_open entries missing from preferred: {sorted(missing)}"
        )
    return cfg


# Well-known location checked when no --config flag is given.  On
# Android/Termux this is where the deploy step drops your networks.toml,
# so Tasker task arguments never need to carry SSID lists.
DEFAULT_CONFIG_PATH = (
    Path.home() / ".config" / "wifi-portal-automation" / "networks.toml"
)


def resolve_config(explicit: Path | None = None) -> NetworkConfig:
    """Resolve the effective network config.

    Order: explicit ``--config`` path → the well-known
    :data:`DEFAULT_CONFIG_PATH` if it exists →
    :data:`DEFAULT_CONFIG`.
    """
    if explicit is not None:
        return load_config(explicit)
    if DEFAULT_CONFIG_PATH.is_file():
        return load_config(DEFAULT_CONFIG_PATH)
    return DEFAULT_CONFIG
