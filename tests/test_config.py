"""Tests for wifi_portal_automation.config — networks.toml loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from wifi_portal_automation.config import (
    DEFAULT_CONFIG,
    load_config,
    resolve_config,
)


class TestLoadConfig:
    """load_config parses networks.toml with per-key defaults."""

    def test_full_config(self, tmp_path: Path) -> None:
        p = tmp_path / "networks.toml"
        p.write_text(
            'preferred = ["A", "B", "Home"]\n'
            'preferred_open = ["A", "B"]\n'
            'fallback = "ISPwifi"\n'
        )
        cfg = load_config(p)
        assert cfg.preferred == ("A", "B", "Home")
        assert cfg.preferred_open == ("A", "B")
        assert cfg.fallback == "ISPwifi"

    def test_missing_keys_use_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "networks.toml"
        p.write_text('preferred_open = ["OnlyOpen"]\n')
        cfg = load_config(p)
        assert cfg.preferred_open == ("OnlyOpen",)
        # Unspecified preferred = open entries merged into defaults
        assert cfg.preferred == ("OnlyOpen",) + DEFAULT_CONFIG.preferred
        assert cfg.fallback == DEFAULT_CONFIG.fallback

    def test_open_must_be_subset_of_preferred(self, tmp_path: Path) -> None:
        p = tmp_path / "networks.toml"
        p.write_text(
            'preferred = ["A"]\npreferred_open = ["A", "B"]\n'
        )
        with pytest.raises(ValueError, match="missing from preferred"):
            load_config(p)

    def test_bad_type_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "networks.toml"
        p.write_text('preferred = "notalist"\n')
        with pytest.raises(ValueError, match="list of SSID"):
            load_config(p)

    def test_bad_fallback_type_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "networks.toml"
        p.write_text('fallback = ["x", "y"]\n')
        with pytest.raises(ValueError, match="single SSID"):
            load_config(p)


class TestResolveConfig:
    """resolve_config precedence: explicit > well-known > defaults."""

    def test_explicit_path_wins(self, tmp_path: Path) -> None:
        p = tmp_path / "networks.toml"
        p.write_text('fallback = "Chosen"\n')
        assert resolve_config(p).fallback == "Chosen"

    def test_no_args_gives_builtin(self) -> None:
        # On machines without the well-known file this returns the
        # built-in example config; either way it must be a valid
        # config object.
        cfg = resolve_config(None)
        assert cfg.preferred
        assert cfg.fallback

    def test_well_known_path_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "networks.toml"
        p.write_text('fallback = "FromWellKnown"\n')
        import wifi_portal_automation.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", p)
        assert resolve_config(None).fallback == "FromWellKnown"

    def test_explicit_beats_well_known(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        well_known = tmp_path / "a.toml"
        well_known.write_text('fallback = "WK"\n')
        explicit = tmp_path / "b.toml"
        explicit.write_text('fallback = "EX"\n')
        import wifi_portal_automation.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "DEFAULT_CONFIG_PATH", well_known)
        assert resolve_config(explicit).fallback == "EX"


class TestNudgeUsesConfig:
    """wifi_nudge --preferred/--defer-to-preferred expand via config."""

    @staticmethod
    def _run(p: Path, mocker: pytest.MockFixture, *cli: str) -> list[str]:
        """Invoke main() with a stubbed run_nudge; return its targets."""
        from wifi_portal_automation.wifi_nudge import NudgeReport, main

        report = NudgeReport(
            action="throttled",
            ok=True,
            wifi_enabled=True,
            connected_ssid=None,
            targets=[],
            visible_targets=[],
            connect_rc=None,
        )
        mock_nudge = mocker.patch(
            "wifi_portal_automation.wifi_nudge.run_nudge",
            return_value=report,
        )
        mocker.patch(
            "wifi_portal_automation.wifi_nudge.find_su",
            return_value="/system/bin/su",
        )
        rc = main(["--config", str(p), *cli])
        assert rc == 0
        _su, targets, *_ = mock_nudge.call_args[0]
        return targets

    def test_config_expands_preferred(
        self, tmp_path: Path, mocker: pytest.MockFixture
    ) -> None:
        p = tmp_path / "networks.toml"
        p.write_text(
            'preferred = ["Open1", "Home1"]\n'
            'preferred_open = ["Open1"]\n'
            'fallback = "FB"\n'
        )
        targets = self._run(p, mocker, "--preferred", "--dry-run", "--json")
        assert targets == ["Open1"]

    def test_default_targets_from_config(
        self, tmp_path: Path, mocker: pytest.MockFixture
    ) -> None:
        """No --ssid/--preferred: default target = first preferred_open."""
        p = tmp_path / "networks.toml"
        p.write_text(
            'preferred = ["Z", "Y"]\npreferred_open = ["Z", "Y"]\n'
        )
        targets = self._run(p, mocker, "--dry-run", "--json")
        assert targets == ["Z"]
