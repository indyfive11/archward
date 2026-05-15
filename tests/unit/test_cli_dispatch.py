"""Tests for v0.4.3 part 2 — argparse subparser routing + backward compat.

Asserts that:
  - Legacy flag forms (`archward --dry-run`, etc.) still parse without
    setting args.command.
  - Each new subcommand sets args.command + the right action field.
  - Unknown subcommand exits 2 with help (argparse default).
"""

from __future__ import annotations

import pytest

from archward.cli import _build_parser


# ── Backward-compat: legacy flag forms ────────────────────────────────


def test_no_args_no_subcommand() -> None:
    """Bare `archward` parses successfully with args.command == None."""
    args = _build_parser().parse_args([])
    assert args.command is None
    assert args.dry_run is False
    assert args.auto is False


def test_dry_run_flag_still_works() -> None:
    args = _build_parser().parse_args(["--dry-run"])
    assert args.command is None
    assert args.dry_run is True


def test_detect_flag_still_works() -> None:
    args = _build_parser().parse_args(["--detect"])
    assert args.command is None
    assert args.detect is True


def test_profile_with_no_subcommand() -> None:
    args = _build_parser().parse_args(["--profile", "lab"])
    assert args.command is None
    assert args.profile == "lab"


# ── New subcommands: verify ───────────────────────────────────────────


def test_verify_subcommand() -> None:
    args = _build_parser().parse_args(["verify"])
    assert args.command == "verify"
    assert args.snapshot is None


def test_verify_with_snapshot_id() -> None:
    args = _build_parser().parse_args(["verify", "--snapshot", "2026-05-15_134116"])
    assert args.command == "verify"
    assert args.snapshot == "2026-05-15_134116"


# ── New subcommands: snapshot ────────────────────────────────────────


def test_snapshot_list_default() -> None:
    args = _build_parser().parse_args(["snapshot", "list"])
    assert args.command == "snapshot"
    assert args.snapshot_action == "list"
    assert args.limit == 20
    assert args.all is False


def test_snapshot_list_all() -> None:
    args = _build_parser().parse_args(["snapshot", "list", "--all"])
    assert args.command == "snapshot"
    assert args.all is True


def test_snapshot_show() -> None:
    args = _build_parser().parse_args(["snapshot", "show", "2026-05-15_134116"])
    assert args.command == "snapshot"
    assert args.snapshot_action == "show"
    assert args.snapshot_id == "2026-05-15_134116"


def test_snapshot_prune_with_keep() -> None:
    args = _build_parser().parse_args(["snapshot", "prune", "--keep", "5"])
    assert args.command == "snapshot"
    assert args.snapshot_action == "prune"
    assert args.keep == 5


# ── New subcommands: rollback ────────────────────────────────────────


def test_rollback_config() -> None:
    args = _build_parser().parse_args(["rollback", "config", "snap-id", "mirrorlist"])
    assert args.command == "rollback"
    assert args.rollback_action == "config"
    assert args.snapshot_id == "snap-id"
    assert args.filename == "mirrorlist"


def test_rollback_package_requires_id_and_name() -> None:
    args = _build_parser().parse_args(["rollback", "package", "snap-id", "nvidia"])
    assert args.command == "rollback"
    assert args.rollback_action == "package"
    assert args.package == "nvidia"
    assert args.confirm_boot_critical is False


def test_rollback_package_with_confirm_flag() -> None:
    args = _build_parser().parse_args(
        ["rollback", "package", "snap-id", "glibc", "--confirm-boot-critical"]
    )
    assert args.confirm_boot_critical is True


def test_rollback_all_configs() -> None:
    args = _build_parser().parse_args(["rollback", "all-configs", "snap-id"])
    assert args.command == "rollback"
    assert args.rollback_action == "all-configs"
    assert args.snapshot_id == "snap-id"
    assert args.yes is False


def test_rollback_all_packages() -> None:
    args = _build_parser().parse_args(["rollback", "all-packages", "snap-id"])
    assert args.command == "rollback"
    assert args.rollback_action == "all-packages"
    assert args.confirm_boot_critical is False


# ── New subcommands: pacnew ──────────────────────────────────────────


def test_pacnew_list() -> None:
    args = _build_parser().parse_args(["pacnew", "list"])
    assert args.command == "pacnew"
    assert args.pacnew_action == "list"


def test_pacnew_diff() -> None:
    args = _build_parser().parse_args(["pacnew", "diff", "/etc/sshd_config"])
    assert args.command == "pacnew"
    assert args.pacnew_action == "diff"
    assert args.path == "/etc/sshd_config"


def test_pacnew_apply_requires_strategy() -> None:
    args = _build_parser().parse_args(
        ["pacnew", "apply", "/etc/sshd_config", "--strategy=take_new"]
    )
    assert args.command == "pacnew"
    assert args.pacnew_action == "apply"
    assert args.strategy == "take_new"


def test_pacnew_apply_rejects_unknown_strategy() -> None:
    """argparse must reject anything not in the choices list."""
    with pytest.raises(SystemExit):
        _build_parser().parse_args(
            ["pacnew", "apply", "/etc/foo", "--strategy=delete_everything"]
        )


# ── Error path: unknown subcommand ───────────────────────────────────


def test_unknown_subcommand_exits() -> None:
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["nonexistent-command"])
