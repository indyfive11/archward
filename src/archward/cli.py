"""argparse + entry point for the CLI (and stub for the GUI)."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from archward import __version__
from archward.app import acquire_lock, check_distro_or_exit, setup_app
from archward.config.detect import apply_detection, diff_against, run_full_detection
from archward.config.loader import default_config_path, load_config, write_config
from archward.pipeline.pipeline import Mode, run_pipeline
from archward.system import notify
from archward.system.distro import detect_distro

log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="archward",
        description="Safe update pipeline for Arch-based Linux distributions.",
    )
    p.add_argument("--version", action="version", version=f"archward {__version__}")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Run snapshot + gates + risk only; do not update.",
    )
    mode.add_argument(
        "--auto",
        action="store_true",
        help="Hands-off: abort if HIGH RISK packages are present.",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Auto-confirm all prompts (HIGH-risk approval, override prompts).",
    )
    p.add_argument(
        "--detect",
        action="store_true",
        help="Run auto-detection (distro, kernels, AUR helper, services); "
        "propose config changes; exit.",
    )
    p.add_argument(
        "--write-config",
        action="store_true",
        help="Write default config to ~/.config/archward/config.toml (overwrites!) and exit.",
    )
    p.add_argument(
        "--no-aur",
        action="store_true",
        help="Skip the AUR phase entirely (overrides config aur.enabled=true).",
    )
    p.add_argument(
        "--profile",
        metavar="NAME",
        default=None,
        help="Use ~/.config/archward/profiles/<NAME>.toml instead of the "
        "default config.toml. Name must be [A-Za-z0-9][A-Za-z0-9_-]{0,63}. "
        "First-run bootstraps the profile file with defaults.",
    )
    return p


def _resolve_config_path(profile: str | None):
    """Resolve --profile to a file path, or None for the default config."""
    if profile is None:
        return None
    from archward.config import paths

    try:
        return paths.profile_config_path(profile)
    except ValueError as e:
        print(f"archward: {e}", file=sys.stderr)
        sys.exit(2)


def _install_sigint_handler(cancel_event: threading.Event) -> None:
    """Install a SIGINT handler that sets cancel_event but does not raise.

    Per audit A3: during the update phase, pacman must be allowed to finish.
    The runner consults cancel_event and stops emitting logs; it does not kill
    the subprocess.
    """
    seen = {"count": 0}

    def handler(signum: int, frame) -> None:  # noqa: ARG001
        seen["count"] += 1
        cancel_event.set()
        if seen["count"] == 1:
            print(
                "\n(SIGINT received — cancellation requested. pacman will be allowed to "
                "finish so db.lck releases cleanly.)",
                file=sys.stderr,
                flush=True,
            )
        else:
            print(
                "(SIGINT again — still waiting on pacman.)",
                file=sys.stderr,
                flush=True,
            )

    signal.signal(signal.SIGINT, handler)


def _detect_command(yes: bool, config_path) -> int:
    """Implement `archward --detect`. Returns the process exit code."""
    info = detect_distro()
    print(
        f"distro: id={info.id} pretty={info.pretty_name!r} "
        f"arch_based={info.is_arch_based} via={info.detected_via}"
    )
    if not info.is_arch_based:
        return 2

    if config_path is not None:
        print(f"profile: writing to {config_path}")
    cfg = load_config(config_path)
    det = run_full_detection()
    diff = diff_against(cfg, det)

    print(f"kernels installed: {', '.join(det.kernels) if det.kernels else '(none detected)'}")
    print(f"aur helper: {det.helper if det.helper else '(none — yay/paru/aurutils not installed)'}")
    print(f"services enabled+active: {len(det.enabled_services)} candidate(s)")
    print(f"existing .pacnew files: {len(det.pacnew_baseline)}")
    print()

    if (
        not diff.kernel_additions
        and not diff.service_additions
        and not diff.service_removals
        and not diff.aur_disable
    ):
        print("config already reflects detected state — no changes proposed.")
        return 0

    print("Proposed config changes:")
    if diff.kernel_additions:
        print(f"  + risk.high: add {', '.join(diff.kernel_additions)}")
    if diff.service_additions:
        print(f"  + services.to_verify: add {len(diff.service_additions)} service(s):")
        for s in diff.service_additions[:10]:
            print(f"      {s}")
        if len(diff.service_additions) > 10:
            print(f"      ... and {len(diff.service_additions) - 10} more")
    if diff.service_removals:
        print(f"  - services.to_verify: remove {len(diff.service_removals)} stale unit(s):")
        for s in diff.service_removals[:10]:
            print(f"      {s}  (no such unit file)")
        if len(diff.service_removals) > 10:
            print(f"      ... and {len(diff.service_removals) - 10} more")
    if diff.aur_disable:
        print("  + aur.enabled = false  (no AUR helper detected)")
    print()

    # Kernel/AUR changes are one combined prompt (both are additive/safe);
    # service additions and removals are opt-in separately so the user
    # can accept or reject each axis without sacrificing the others.
    apply_kernel_aur = bool(diff.kernel_additions) or diff.aur_disable
    accept_kernel_aur = True
    accept_services = False
    accept_service_removals = False

    if yes:
        accept_services = bool(diff.service_additions)
        accept_service_removals = bool(diff.service_removals)
    else:
        if apply_kernel_aur:
            try:
                answer = input("Apply kernel/AUR changes? [Y/n] ").strip().lower()
            except EOFError:
                answer = ""
            accept_kernel_aur = answer == "" or answer.startswith("y")

        if diff.service_additions:
            try:
                s_answer = input(
                    f"Add {len(diff.service_additions)} service(s) to verify? [y/N] "
                ).strip().lower()
            except EOFError:
                s_answer = "n"
            accept_services = s_answer.startswith("y")

        if diff.service_removals:
            try:
                r_answer = input(
                    f"Remove {len(diff.service_removals)} stale service entries? [y/N] "
                ).strip().lower()
            except EOFError:
                r_answer = "n"
            accept_service_removals = r_answer.startswith("y")

    # Build a filtered diff so the user's "no" actually drops those changes.
    from archward.config.detect import ConfigDiff

    effective = ConfigDiff(
        kernel_additions=diff.kernel_additions if accept_kernel_aur else (),
        service_additions=diff.service_additions,  # apply_detection gates on accept_services
        aur_disable=diff.aur_disable if accept_kernel_aur else False,
        helper_set_to=diff.helper_set_to,
        service_removals=diff.service_removals,  # apply_detection gates on accept_service_removals
    )

    if (
        not effective.kernel_additions
        and not (accept_services and effective.service_additions)
        and not (accept_service_removals and effective.service_removals)
        and not effective.aur_disable
    ):
        print("no changes applied.")
        return 0

    new_cfg = apply_detection(
        cfg, det, effective,
        accept_services=accept_services,
        accept_service_removals=accept_service_removals,
    )
    path = write_config(new_cfg, config_path)
    print(f"wrote {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config_path = _resolve_config_path(args.profile)

    if args.write_config:
        from archward.config.defaults import default_config

        cfg = default_config()
        path = write_config(cfg, config_path)
        print(f"wrote defaults to {path}")
        return 0

    if args.detect:
        return _detect_command(yes=args.yes, config_path=config_path)

    mode = Mode.DRY_RUN if args.dry_run else (Mode.AUTO if args.auto else Mode.INTERACTIVE)

    cancel_event = threading.Event()
    _install_sigint_handler(cancel_event)

    with acquire_lock():
        cfg, strategy, bus = setup_app(config_path=config_path)
        check_distro_or_exit(bus)
        result = run_pipeline(
            cfg,
            strategy,
            bus,
            mode,
            auto_yes=args.yes,
            no_aur=args.no_aur,
            cancel_event=cancel_event,
            config_path=config_path,
        )

    # Final report.
    print("", flush=True)
    print("=== archward result ===", flush=True)
    summary = result.summary
    if summary is None:
        print("RESULT:UPDATE_FAILED  (no summary produced)", flush=True)
        return 1

    print(summary.tag, flush=True)
    for sec in summary.secondary_tags:
        print(f"  + {sec}", flush=True)
    if result.aborted_reason:
        print(f"  reason: {result.aborted_reason}", flush=True)
    if summary.fail_count or summary.warn_count:
        print(f"  verify: {summary.fail_count} FAIL / {summary.warn_count} WARN", flush=True)
    if result.aur and result.aur.failures:
        print(f"  AUR: {len(result.aur.failures)} build failure(s) — retry these later:", flush=True)
        for f in result.aur.failures:
            print(f"    - {f.package}", flush=True)
    if summary.reboot_needed:
        print("  ACTION: Reboot to activate the new kernel.", flush=True)

    # Desktop notification on completion (no-op if libnotify missing or
    # cfg.general.notify_on_completion is False).
    notify.notify_completion(result, cfg)

    # Exit code mirrors bash pipeline behavior:
    #   0 = SUCCESS / PACNEW_MERGE_NEEDED / NEEDS_REVIEW (warnings or info only)
    #   1 = UPDATE_FAILED / VERIFY_FAILED (any failure)
    #   2 = REBOOT_NEEDED (informational; user must act)
    if summary.tag in ("RESULT:UPDATE_FAILED", "RESULT:VERIFY_FAILED"):
        return 1
    if summary.tag == "RESULT:REBOOT_NEEDED":
        return 2
    return 0


def _build_gui_parser() -> argparse.ArgumentParser:
    """Minimal argparse for archward-gui: --version and --profile NAME.

    Mirrors the CLI flag set narrowly to what the GUI session honors. Other
    flags (--dry-run, --auto, --yes, --no-aur) are CLI-only because the
    equivalent GUI behaviors are driven by toolbar actions, not invocation.
    """
    p = argparse.ArgumentParser(
        prog="archward-gui",
        description="Safe update pipeline for Arch-based Linux distributions (GUI).",
    )
    p.add_argument("--version", action="version", version=f"archward {__version__}")
    p.add_argument(
        "--profile",
        metavar="NAME",
        default=None,
        help="Use ~/.config/archward/profiles/<NAME>.toml instead of the "
        "default config.toml. Name must be [A-Za-z0-9][A-Za-z0-9_-]{0,63}. "
        "First-run bootstraps the profile file with defaults.",
    )
    return p


def main_gui(argv: list[str] | None = None) -> int:
    """Launch the archward Qt GUI."""
    args = _build_gui_parser().parse_args(argv)
    config_path = _resolve_config_path(args.profile)

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "PySide6 is not installed. Install with `pip install archward[gui]` "
            "or your package manager (Arch: `pacman -S pyside6`).",
            file=sys.stderr,
        )
        return 1

    from archward.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("archward")            # unix identity — used for QSettings paths
    app.setApplicationDisplayName("Archward")     # human-readable name shown in title bars
    app.setOrganizationName("archward")

    window = MainWindow(config_path=config_path)
    window.show()
    return app.exec()
