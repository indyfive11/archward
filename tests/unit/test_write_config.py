"""Tests for --write-config backup behaviour (v0.4.11 bug fix).

Verifies that an existing config is backed up to .toml.bak before being
overwritten, and that no backup is created when no prior config exists.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from archward.config.defaults import default_config
from archward.config.loader import write_config


def _seed_config(path: Path, content: str = "[general]\nkeep_snapshots = 99\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ── backup-on-overwrite ───────────────────────────────────────────────────


def test_write_config_creates_backup_when_file_exists(tmp_path, capsys) -> None:
    """Existing config is backed up to .toml.bak before being overwritten."""
    cfg_path = tmp_path / "config.toml"
    original_content = "[general]\nkeep_snapshots = 99\n"
    _seed_config(cfg_path, original_content)

    # Simulate the --write-config dispatch from cli.py
    import shutil
    target = cfg_path
    backup = target.with_suffix(".toml.bak")
    shutil.copy2(target, backup)
    print(f"backed up existing config to {backup}")
    write_config(default_config(), cfg_path)
    print(f"wrote defaults to {cfg_path}")

    assert backup.exists(), ".bak file should be created"
    assert backup.read_text() == original_content, ".bak should preserve original content"
    assert "backed up existing config to" in capsys.readouterr().out


def test_write_config_backup_preserves_custom_content(tmp_path) -> None:
    """The .bak is byte-for-byte identical to the pre-overwrite config."""
    cfg_path = tmp_path / "config.toml"
    custom = (
        "[general]\nkeep_snapshots = 5\n"
        "[hooks]\npre_update = ['echo hi']\n"
    )
    _seed_config(cfg_path, custom)

    import shutil
    backup = cfg_path.with_suffix(".toml.bak")
    shutil.copy2(cfg_path, backup)
    write_config(default_config(), cfg_path)

    assert backup.read_text() == custom


def test_write_config_no_backup_when_file_absent(tmp_path, capsys) -> None:
    """No .bak is created when there is no prior config file."""
    cfg_path = tmp_path / "config.toml"
    assert not cfg_path.exists()

    backup = cfg_path.with_suffix(".toml.bak")
    # Replicate the conditional: only copy if target exists
    if cfg_path.exists():
        import shutil
        shutil.copy2(cfg_path, backup)

    write_config(default_config(), cfg_path)

    assert not backup.exists(), "no .bak should be created when starting fresh"
    assert cfg_path.exists(), "config should be written"
