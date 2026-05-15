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
    """CLI parser. v0.4.3 introduces subcommands alongside the existing flag
    forms. Backward-compatible: `archward` with any of the existing top-level
    flags (--dry-run, --detect, --write-config, --no-aur, --yes, --auto,
    --profile) behaves exactly as before. Subcommands (`archward verify`,
    `archward snapshot ...`, `archward rollback ...`, `archward pacnew ...`)
    expose post-reboot recovery + GUI parity.
    """
    p = argparse.ArgumentParser(
        prog="archward",
        description="Safe update pipeline for Arch-based Linux distributions.",
    )
    p.add_argument("--version", action="version", version=f"archward {__version__}")
    # Top-level flags — active when no subcommand is given (the canonical
    # "run the full pipeline" form).
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

    # Subcommands (v0.4.3). `dest="command"` puts the subcommand name in
    # `args.command` (None when no subcommand given → fall through to the
    # legacy flag-driven pipeline path in main()).
    subparsers = p.add_subparsers(
        dest="command",
        title="subcommands",
        metavar="<command>",
        description="Run with --help on any subcommand for details.",
    )
    _attach_verify_parser(subparsers)
    _attach_snapshot_parser(subparsers)
    _attach_rollback_parser(subparsers)
    _attach_pacnew_parser(subparsers)

    return p


def _attach_verify_parser(subparsers) -> None:
    """`archward verify [--snapshot ID]` — re-run verify against a snapshot."""
    sp = subparsers.add_parser(
        "verify",
        help="Re-run the verify phase against an existing snapshot.",
        description=(
            "Re-runs the verify phase against the latest snapshot (or a "
            "specific one via --snapshot). No new snapshot is taken; no "
            "update is performed. Intended for post-reboot diagnostic — "
            "catches failures that only manifest at next boot (DKMS modules, "
            "pacnew left unmerged, mkinitcpio hooks, systemd unit changes)."
        ),
    )
    sp.add_argument(
        "--snapshot",
        metavar="ID",
        default=None,
        help="snapshot directory name (e.g. 2026-05-15_134116). "
        "Default: latest snapshot.",
    )


def _attach_snapshot_parser(subparsers) -> None:
    """`archward snapshot {list,show,prune}` — TTY-friendly snapshot ops."""
    sp = subparsers.add_parser(
        "snapshot",
        help="List, show, or prune snapshots.",
    )
    snap_sub = sp.add_subparsers(dest="snapshot_action", metavar="<action>")

    list_p = snap_sub.add_parser("list", help="List snapshots newest-first.")
    list_p.add_argument(
        "--limit", type=int, default=20,
        help="Maximum snapshots to display (default: 20). Use --all for full.",
    )
    list_p.add_argument(
        "--all", action="store_true",
        help="Show every snapshot, regardless of --limit.",
    )

    show_p = snap_sub.add_parser("show", help="Show one snapshot's details.")
    show_p.add_argument("snapshot_id", help="Snapshot directory name.")

    prune_p = snap_sub.add_parser(
        "prune",
        help="Delete old snapshots, keeping the N newest.",
    )
    prune_p.add_argument(
        "--keep", type=int, default=None,
        help="Number of snapshots to retain (default: cfg.general.keep_snapshots).",
    )
    prune_p.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt.",
    )


def _attach_rollback_parser(subparsers) -> None:
    """`archward rollback {config,package,all-configs,all-packages}`."""
    sp = subparsers.add_parser(
        "rollback",
        help="Restore a config or downgrade a package from a snapshot.",
    )
    rb_sub = sp.add_subparsers(dest="rollback_action", metavar="<action>")

    cfg_p = rb_sub.add_parser(
        "config",
        help="Restore one captured config file to its /etc location.",
    )
    cfg_p.add_argument("snapshot_id", help="Snapshot directory name.")
    cfg_p.add_argument(
        "filename",
        help="Captured config filename (use `archward snapshot show` to discover).",
    )

    pkg_p = rb_sub.add_parser(
        "package",
        help="Downgrade/upgrade one package to its snapshot version.",
    )
    pkg_p.add_argument("snapshot_id", help="Snapshot directory name.")
    pkg_p.add_argument("package", help="Package name (e.g. nvidia).")
    pkg_p.add_argument(
        "--confirm-boot-critical", action="store_true",
        help="Required when the package is boot-critical (glibc, systemd, etc.).",
    )

    all_cfg_p = rb_sub.add_parser(
        "all-configs",
        help="Restore every captured config from a snapshot. Auto-takes a "
        "pre-rollback snapshot first.",
    )
    all_cfg_p.add_argument("snapshot_id", help="Snapshot directory name.")
    all_cfg_p.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt.",
    )

    all_pkg_p = rb_sub.add_parser(
        "all-packages",
        help="Bulk downgrade every drifted package to snapshot versions. "
        "Auto-takes a pre-rollback snapshot first.",
    )
    all_pkg_p.add_argument("snapshot_id", help="Snapshot directory name.")
    all_pkg_p.add_argument(
        "--confirm-boot-critical", action="store_true",
        help="Required when the plan contains boot-critical packages. "
        "Even with the flag, you must type YES on stdin.",
    )


def _attach_pacnew_parser(subparsers) -> None:
    """`archward pacnew {list,diff,apply}` — manual .pacnew resolution."""
    sp = subparsers.add_parser(
        "pacnew",
        help="List, diff, or apply actions to .pacnew files.",
    )
    pn_sub = sp.add_subparsers(dest="pacnew_action", metavar="<action>")

    pn_sub.add_parser("list", help="List current .pacnew files with classified strategy.")

    diff_p = pn_sub.add_parser("diff", help="Print unified diff of live vs .pacnew.")
    diff_p.add_argument(
        "path",
        help="Either the live /etc path or the .pacnew path — both work.",
    )

    apply_p = pn_sub.add_parser(
        "apply",
        help="Apply a resolution to a .pacnew file.",
    )
    apply_p.add_argument(
        "path",
        help="Either the live /etc path or the .pacnew path — both work.",
    )
    apply_p.add_argument(
        "--strategy", required=True,
        choices=["keep_ours", "take_new", "edit", "leave"],
        help="What to do with the .pacnew: keep_ours discards the .pacnew, "
        "take_new replaces the live file (preserving perms+owner), edit "
        "opens $VISUAL/$EDITOR on both files, leave is a no-op.",
    )


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

    # v0.4.3 subcommand dispatch. When args.command is None, fall through
    # to the legacy flag-driven pipeline path below.
    if args.command is not None:
        return _dispatch_subcommand(args, config_path)

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
        # v0.4.3: TTY recovery breadcrumb. Most users will reboot and the
        # desktop will come back fine — they just need to know to run
        # verify. But when the desktop DOESN'T come back (DKMS regression,
        # broken display manager, etc.), they're stuck in tty1 and this is
        # the only place they'll find out the CLI has the rollback tools.
        print("  ACTION: Reboot to activate the new kernel.", flush=True)
        print("    After rebooting:", flush=True)
        print("      • `archward verify` — confirm everything came back up cleanly.", flush=True)
        print("    If the desktop fails to load:", flush=True)
        print("      • Drop to a TTY (Ctrl+Alt+F2) and log in as your user.", flush=True)
        print("      • `archward snapshot list`      # see rollback points", flush=True)
        print("      • `archward verify`             # diagnose what broke", flush=True)
        print("      • `archward rollback package <id> <pkg>`  # targeted undo", flush=True)
        print("      • full guide: `man archward` or "
              "/usr/share/doc/archward/recovery.md", flush=True)

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


def _dispatch_subcommand(args, config_path) -> int:
    """Route an args.command-set parse result to the matching subcommand module.

    Returns the subcommand's exit code. Each subcommand module is responsible
    for: loading config, building (when needed) the sudo strategy, printing
    its own output, and returning a Unix-style exit code.
    """
    if args.command == "verify":
        from archward.cli_subcommands import verify as cmd
        return cmd.cmd_verify(args, config_path)
    if args.command == "snapshot":
        from archward.cli_subcommands import snapshot as cmd
        if args.snapshot_action == "list":
            return cmd.cmd_list(args, config_path)
        if args.snapshot_action == "show":
            return cmd.cmd_show(args, config_path)
        if args.snapshot_action == "prune":
            return cmd.cmd_prune(args, config_path)
        print("archward snapshot: missing action — try `archward snapshot --help`", file=sys.stderr)
        return 2
    if args.command == "rollback":
        from archward.cli_subcommands import rollback as cmd
        if args.rollback_action == "config":
            return cmd.cmd_config(args, config_path)
        if args.rollback_action == "package":
            return cmd.cmd_package(args, config_path)
        if args.rollback_action == "all-configs":
            return cmd.cmd_all_configs(args, config_path)
        if args.rollback_action == "all-packages":
            return cmd.cmd_all_packages(args, config_path)
        print("archward rollback: missing action — try `archward rollback --help`", file=sys.stderr)
        return 2
    if args.command == "pacnew":
        from archward.cli_subcommands import pacnew as cmd
        if args.pacnew_action == "list":
            return cmd.cmd_list(args, config_path)
        if args.pacnew_action == "diff":
            return cmd.cmd_diff(args, config_path)
        if args.pacnew_action == "apply":
            return cmd.cmd_apply(args, config_path)
        print("archward pacnew: missing action — try `archward pacnew --help`", file=sys.stderr)
        return 2
    print(f"archward: unknown command {args.command!r}", file=sys.stderr)
    return 2


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
    # Wayland app_id ⇒ Plasma associates the running window with the
    # .desktop launcher (matches StartupWMClass=archward). Without this
    # the taskbar can't find our icon and falls back to a generic one.
    app.setDesktopFileName("archward")
    # Belt-and-suspenders: set the QIcon explicitly so X11 and any path
    # that doesn't go through xdg_toplevel.set_app_id also gets the
    # shield+A artwork.
    from archward.ui.icon import archward_icon
    app.setWindowIcon(archward_icon())
    # NOTE — earlier v0.4.0 work overrode QPalette::Highlight to brand
    # teal. That caused ApplicationPaletteChange events to cascade across
    # every widget and interacted badly with Plasma's Wayland theme
    # propagation (visible blackouts during heavy paint traffic). The
    # win was small; brand accents are now applied to specific widgets
    # that benefit, never globally via the palette.

    # If --profile was NOT specified explicitly, consult the QSettings
    # remember-last-used flag and use the previously-active profile.
    # Requires QApplication to exist so QSettings resolves the right file.
    if config_path is None:
        from archward.ui.persistent_state import get_last_used_profile_path
        config_path = get_last_used_profile_path()

    window = MainWindow(config_path=config_path)
    window.show()
    return app.exec()
