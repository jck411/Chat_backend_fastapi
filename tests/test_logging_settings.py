"""Tests for logging settings parsing."""

from pathlib import Path

from src.backend.logging_settings import parse_logging_settings


def test_parse_logging_settings_with_retention(tmp_path: Path) -> None:
    """Test parsing logging settings with retention_hours."""
    config_file = tmp_path / "logging_settings.conf"
    config_file.write_text(
        """
# Test config
terminal = debug
sessions = info
conversations = warning
retention_hours = 72
"""
    )

    settings = parse_logging_settings(config_file)

    assert settings.terminal_level == 10  # DEBUG
    assert settings.sessions_level == 20  # INFO
    assert settings.conversations_level == 30  # WARNING
    assert settings.retention_hours == 72


def test_parse_logging_settings_defaults(tmp_path: Path) -> None:
    """Test default values when config file doesn't exist."""
    config_file = tmp_path / "nonexistent.conf"

    settings = parse_logging_settings(config_file)

    assert settings.terminal_level == 20  # Default INFO
    assert settings.sessions_level == 20  # Default INFO
    assert settings.conversations_level == 20  # Default INFO
    assert settings.retention_hours == 48  # Default retention


def test_parse_logging_settings_partial(tmp_path: Path) -> None:
    """Test parsing with only some settings specified."""
    config_file = tmp_path / "logging_settings.conf"
    config_file.write_text(
        """
terminal = debug
retention_hours = 24
"""
    )

    settings = parse_logging_settings(config_file)

    assert settings.terminal_level == 10  # DEBUG
    assert settings.sessions_level == 20  # Default INFO
    assert settings.conversations_level == 20  # Default INFO
    assert settings.retention_hours == 24


def test_parse_logging_settings_disabled_retention(tmp_path: Path) -> None:
    """Test parsing with retention disabled."""
    config_file = tmp_path / "logging_settings.conf"
    config_file.write_text(
        """
terminal = info
retention_hours = 0
"""
    )

    settings = parse_logging_settings(config_file)

    assert settings.retention_hours == 0


def test_parse_logging_settings_invalid_retention(tmp_path: Path) -> None:
    """Test parsing with invalid retention value falls back to default."""
    config_file = tmp_path / "logging_settings.conf"
    config_file.write_text(
        """
terminal = info
retention_hours = invalid
"""
    )

    settings = parse_logging_settings(config_file)

    assert settings.retention_hours == 48  # Falls back to default


def test_parse_logging_settings_negative_retention(tmp_path: Path) -> None:
    """Test parsing with negative retention value clamps to 0."""
    config_file = tmp_path / "logging_settings.conf"
    config_file.write_text(
        """
terminal = info
retention_hours = -10
"""
    )

    settings = parse_logging_settings(config_file)

    assert settings.retention_hours == 0  # Clamped to 0


def test_parse_logging_settings_off_level(tmp_path: Path) -> None:
    """Test parsing with 'off' level."""
    config_file = tmp_path / "logging_settings.conf"
    config_file.write_text(
        """
terminal = off
sessions = off
conversations = info
retention_hours = 48
"""
    )

    settings = parse_logging_settings(config_file)

    assert settings.terminal_level is None
    assert settings.sessions_level is None
    assert settings.conversations_level == 20  # INFO
    assert settings.retention_hours == 48
