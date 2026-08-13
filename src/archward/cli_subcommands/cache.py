"""archward cache {status,gaps,set-keep,clean} — pacman cache management.

NOTE: set-keep requires systemd (paccache.timer). Non-systemd Arch-based
distros (Artix/OpenRC, Artix/runit, etc.) are not yet supported — the
command will detect a missing timer unit and exit with a clear message.
"""

from __future__ import annotations

import sys
from pathlib import Path

from archward.app import build_config, build_sudo_strategy
from archward.pacman.runner import run_capture
from archward.system import cache_policy as cp


def cmd_status(args, config_path: Path | None) -> int:
    policy = cp.detect_cache_policy()

    size_mb = policy.cache_size_bytes / (1024 * 1024)
    hooks = policy.cleaning_hooks

    print(f"Rollback safety:   {policy.safety.value.upper()}")
    print(f"Timer state:       {policy.timer_state}")
    print(f"Effective keep:    {policy.effective_keep} versions"
          + ("" if policy.timer_state == "enabled" else " (timer not active)"))
    if policy.paccache_args:
        print(f"PACCACHE_ARGS:     {policy.paccache_args}")
    print(f"CleanMethod:       {', '.join(policy.clean_method)}")
    print(f"Cache size:        {size_mb:.1f} MB ({policy.cache_file_count} files)")
    if hooks:
        print(f"Cleaning hooks:    {', '.join(h.name for h in hooks)}  [DANGEROUS]")
    print()
    print(policy.explanation)
    return 0


def _scan_cache(cache_dirs: tuple[Path, ...]) -> set[str]:
    """Return the set of package filenames across all cache dirs."""
    names: set[str] = set()
    for d in cache_dirs:
        if not d.is_dir():
            continue
        try:
            for entry in d.iterdir():
                name = entry.name
                if entry.is_file() and (
                    name.endswith(".pkg.tar.zst")
                    or name.endswith(".pkg.tar.xz")
                    or name.endswith(".pkg.tar.gz")
                ):
                    names.add(name)
        except OSError:
            pass
    return names


def cmd_gaps(args, config_path: Path | None) -> int:
    from archward.pacman import query as pq

    installed = pq.list_all()
    cache_dirs = cp.read_cache_dirs()
    cache_names = _scan_cache(cache_dirs)
    gaps = cp.rollback_gaps(installed, cache_names)

    if not gaps:
        print("No rollback gaps — all installed packages have a cached prior version.")
        return 0

    print(f"{len(gaps)} package(s) have no cached rollback candidate:")
    for name, ver in sorted(gaps):
        print(f"  {name}  {ver}")
    return 0


def cmd_set_keep(args, config_path: Path | None) -> int:
    n = args.n

    if n < 1:
        # Hard floor (v0.5) — no --force bypass. paccache -rk0 (or a negative
        # written into PACCACHE_ARGS) deletes the ENTIRE rollback cache,
        # contradicting the rollback substrate archward exists to protect.
        print(
            f"Refusing keep={n}: keep must be at least 1 (0 would delete the "
            "entire rollback cache).",
            file=sys.stderr,
        )
        return 2

    if n < 2 and not args.force:
        print(
            f"Refusing keep={n}: fewer than 2 versions leaves no rollback candidate.",
            file=sys.stderr,
        )
        print("Use --force to override.", file=sys.stderr)
        return 2

    timer_state = cp.paccache_timer_state()
    if timer_state == "not-installed":
        print(
            "paccache.timer is not available on this system.\n"
            "archward cache set-keep requires systemd. On non-systemd Arch-based\n"
            "distros (Artix etc.), configure retention manually via cron or your\n"
            "init system's equivalent.",
            file=sys.stderr,
        )
        return 2

    cfg = build_config(config_path)
    strategy = build_sudo_strategy(cfg)

    paccache_args = f"-rk{n}"
    conf_content = (
        "# Managed by archward (cache set-keep). PACCACHE_ARGS is appended to\n"
        "# the bare `paccache -r` the paccache.service runs.\n"
        f"PACCACHE_ARGS='{paccache_args}'\n"
    )

    commands: list[tuple[list[str], str | None]] = [
        (["tee", str(cp._CONF_D)], conf_content),
        (["systemctl", "enable", "--now", "paccache.timer"], None),
    ]
    for argv, input_text in commands:
        code, _out, err = run_capture(argv, strategy=strategy, input_text=input_text)
        if code != 0:
            print(f"Command failed: {' '.join(argv)}\n{err}", file=sys.stderr)
            return 1

    print(f"paccache retention set to {n} versions. paccache.timer enabled.")
    return 0


def cmd_clean(args, config_path: Path | None) -> int:
    policy = cp.detect_cache_policy()
    keep = policy.effective_keep

    size_mb = policy.cache_size_bytes / (1024 * 1024)
    print(f"Cache: {size_mb:.1f} MB ({policy.cache_file_count} files)")
    print(f"Will prune to {keep} versions per package (paccache -rk{keep}).")
    print()

    if sys.stdout.isatty():
        try:
            ans = input("Proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if ans not in ("y", "yes"):
            print("Aborted.")
            return 0

    cfg = build_config(config_path)
    strategy = build_sudo_strategy(cfg)

    code, out, err = run_capture(
        ["paccache", f"-rk{keep}"],
        strategy=strategy,
    )
    if out:
        print(out, end="")
    if code != 0:
        print(err, file=sys.stderr)
        return 1
    return 0
