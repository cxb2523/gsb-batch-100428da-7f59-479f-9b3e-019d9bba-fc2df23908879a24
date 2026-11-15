from pathlib import Path

import pytest

from proxyrotate.config import (
    AppConfig,
    ConfigError,
    apply_cli_overrides,
    load_config,
)


class TestDefaults:
    def test_missing_file_gives_defaults(self, tmp_path):
        config = load_config(tmp_path / "absent.toml")
        assert config.proxy_file == Path.home() / ".config" / "proxyrotate" / "proxies.txt"
        assert config.sing_box is None
        assert config.rotation_interval == 60.0
        assert config.failure_cooldown == 30.0
        assert config.cooldown_max == 600.0
        assert config.health_check is True
        assert config.health_timeout == 5.0
        assert config.log_level == "info"
        assert config.rotation_requests is None
        assert config.rotation_mb is None


class TestTomlLoading:
    def test_overrides(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text(
            'proxy_file = "/etc/my-proxies.txt"\n'
            'rotation_interval = 15\n'
            "health_check = false\n"
            'log_level = "DEBUG"\n',
            encoding="utf-8",
        )
        config = load_config(path)
        assert config.proxy_file == Path("/etc/my-proxies.txt")
        assert config.rotation_interval == 15.0
        assert config.health_check is False
        # levels are normalized to lowercase
        assert config.log_level == "debug"

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        path = tmp_path / "config.toml"
        path.write_text(
            'proxy_file = "~/proxies/list.txt"\n'
            'sing_box = "~/bin/sing-box"\n',
            encoding="utf-8",
        )
        config = load_config(path)
        assert config.proxy_file == tmp_path / "proxies" / "list.txt"
        # sing_box stays a raw string; discover_binary() expands it later.
        assert config.sing_box == "~/bin/sing-box"

    def test_unknown_key_rejected(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("rotation_interval = 5\ntyop = 1\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="unknown option.*tyop"):
            load_config(path)

    def test_invalid_toml(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("not [ valid toml", encoding="utf-8")
        with pytest.raises(ConfigError, match="invalid TOML"):
            load_config(path)

    def test_wrong_types(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('health_check = "yes"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="health_check"):
            load_config(path)

    def test_bad_log_level(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('log_level = "verbose"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="log_level"):
            load_config(path)

    def test_negative_interval(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("rotation_interval = -3\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="rotation_interval"):
            load_config(path)

    def test_activity_and_cooldown_keys(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text(
            "rotation_requests = 25\n"
            "rotation_mb = 150.5\n"
            "cooldown_max = 900\n",
            encoding="utf-8",
        )
        config = load_config(path)
        assert config.rotation_requests == 25
        assert config.rotation_mb == 150.5
        assert config.cooldown_max == 900.0

    def test_rotation_requests_must_be_positive_int(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("rotation_requests = 0\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="rotation_requests"):
            load_config(path)

    def test_rotation_requests_rejects_float(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("rotation_requests = 2.5\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="rotation_requests"):
            load_config(path)

    def test_rotation_mb_must_be_positive(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("rotation_mb = 0\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="rotation_mb"):
            load_config(path)

    def test_cooldown_max_must_be_positive(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("cooldown_max = 0\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="cooldown_max"):
            load_config(path)

    def test_config_file_recorded(self, tmp_path):
        path = tmp_path / "config.toml"
        config = load_config(path)
        assert config.config_file == path


class TestCliOverrides:
    def make_args(self, **kwargs):
        return kwargs

    def test_cli_beats_file(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("rotation_interval = 99\n", encoding="utf-8")
        config = load_config(path)

        apply_cli_overrides(
            config,
            proxy_file=Path("/tmp/x.txt"),
            sing_box=None,
            every=10,
            cooldown=None,
            health_check=False,
            health_timeout=None,
            log_level=None,
        )
        assert config.rotation_interval == 10
        assert config.proxy_file == Path("/tmp/x.txt")
        assert config.health_check is False

    def test_none_means_no_override(self, tmp_path):
        config = AppConfig()
        apply_cli_overrides(
            config,
            proxy_file=None,
            sing_box=None,
            every=None,
            cooldown=None,
            health_check=None,
            health_timeout=None,
            log_level=None,
        )
        assert config.rotation_interval == 60.0
        assert config.log_level == "info"
