"""Inline help text for every Preferences field.

Keyed by `(section, field)` tuples mirroring `models/config.py` exactly. Keeping
help next to the schema (rather than in the UI file) makes future updates and
i18n easier — translators get one dict to look at, and renaming a config field
forces a touch here too.

Style guide for help strings:
- One or two short lines, plain language, no jargon.
- Mention the consequence of choosing wrong, not just what the field does.
- "Why might I change this?" is a better question than "What does this field do?"
"""

from __future__ import annotations

# (section, field) → help string. Sections match ConfigModel attribute names.
HELP: dict[tuple[str, str], str] = {
    # ── General ────────────────────────────────────────────────────────────
    ("general", "snapshot_dir"): (
        "Where archward stores per-update state snapshots (package list, "
        "configs, services, network). Each snapshot is small (~100-500 KB)."
    ),
    ("general", "keep_snapshots"): (
        "Hard ceiling on total snapshot count — archward never keeps more than "
        "this many regardless of age. Protects disk when you run updates "
        "frequently. Raise it if you want more than 10 rollback points available."
    ),
    ("general", "keep_days"): (
        "Delete snapshots older than this many days. 0 disables age-based "
        "pruning (count-only mode). Pairs with 'always keep at least' so a "
        "long idle period never leaves you with zero snapshots."
    ),
    ("general", "keep_min"): (
        "Always keep at least this many snapshots regardless of age. Protects "
        "against the 'came back from vacation and everything is gone' failure "
        "mode. 2 means you always have the last two runs to fall back on."
    ),
    ("general", "after_snapshot"): (
        "After a clean verify pass, take a second snapshot of the post-update "
        "state. Paired pre+post snapshots let you see exactly which packages "
        "changed, and give you a 'known-good post-update' baseline you can "
        "compare against or roll back from."
    ),
    ("general", "log_dir"): (
        "Where the rotating archward log file lives. Useful when debugging a "
        "run after the fact — see `tail -f` on this directory's archward.log."
    ),
    ("general", "keep_logs"): (
        "Number of rotated log files to retain. Each archive caps at ~2 MB."
    ),
    ("general", "notify_on_completion"): (
        "Show a desktop notification when the pipeline finishes. Uses "
        "`notify-send` (libnotify) — silently disabled if not installed. "
        "Notification urgency mirrors the RESULT tag: success → low (auto-"
        "dismiss), reboot/pacnew → normal, failures → critical (persist)."
    ),

    # ── Gates ──────────────────────────────────────────────────────────────
    ("gates", "snapshot_max_age_minutes"): (
        "How fresh the snapshot must be before an update is allowed. Older "
        "snapshots are rejected so the rollback reference matches the system "
        "state at update time. If you bumped this and archward refuses to "
        "start, take a new snapshot."
    ),
    ("gates", "min_disk_gb"): (
        "Minimum free space on / for the update to proceed. pacman needs room "
        "to download + extract; below 5 GB you risk a half-finished "
        "transaction. Run `sudo paccache -rk3` if you're under the threshold."
    ),
    ("gates", "allow_override"): (
        "Whether failing recoverable gates (e.g. low disk) can be overridden. "
        "Off = strict; the update refuses to run. On = you'll be prompted to "
        "proceed anyway in the CLI / a dialog in the GUI."
    ),
    ("gates", "skip_news_check"): (
        "Skip the Arch News pre-flight check. When OFF (default), archward "
        "fetches archlinux.org/news/ before each update and warns if items "
        "were posted since your last run — these announcements often contain "
        "manual steps required before or after an update (e.g. NVIDIA driver "
        "drops, major ABI changes, AUR malware disclosures). Enable this only "
        "if you monitor Arch News through another channel."
    ),

    # ── Risk ───────────────────────────────────────────────────────────────
    ("risk", "_section"): (
        "archward classifies every pending update as HIGH (requires explicit "
        "approval), MEDIUM (service packages — verify after update), or LOW "
        "(everything else)."
    ),
    ("risk", "high"): (
        "Packages that always require explicit approval before updating. "
        "Add anything whose breakage would be hard to recover from: glibc, "
        "systemd, openssh, etc. Exact match — `linux` only matches the "
        "package literally named `linux`, not `linux-lts`."
    ),
    ("risk", "medium_patterns"): (
        "fnmatch globs (`*`, `?`) for service packages that should be "
        "verified after update. The default list covers daemons commonly "
        "run on personal Arch desktops (docker, qemu, postgresql, nginx, …)."
    ),
    ("risk", "kernel_patterns"): (
        "fnmatch globs for kernel + kernel-headers packages. Anything "
        "matching classifies HIGH and flags `is_kernel=True`, which drives "
        "the REBOOT_NEEDED result tag. Headers matter because DKMS modules "
        "must rebuild against the new kernel."
    ),
    ("risk", "kernel_pattern_exclude"): (
        "Packages that match a kernel pattern but are NOT bootable kernels "
        "(firmware blobs, docs, tools). Excluded from the HIGH+kernel "
        "classification so they don't spuriously trigger REBOOT_NEEDED."
    ),

    # ── Services ───────────────────────────────────────────────────────────
    ("services", "to_verify"): (
        "One systemd unit per line. After every update archward runs "
        "`systemctl is-active <unit>` and fails the verify phase if any are "
        "down. Use `archward --detect` to auto-populate from enabled+active "
        "services."
    ),
    ("services", "severity"): (
        "Per-unit overrides for what `inactive` means. Default for unlisted "
        "units is `critical` → FAIL. Use `watch` for services that may "
        "legitimately be down sometimes (e.g. a periodic timer's main service)."
    ),
    ("services", "auto_prune"): (
        "When ON, the verify phase silently removes entries from the list "
        "whose unit file no longer resolves (e.g. the package was "
        "uninstalled) and writes the pruned config back to disk. When OFF "
        "(default), stale entries surface as a WARN row pointing you to "
        "`archward --detect` for manual confirmation. Leave OFF if you "
        "sometimes move unit files around (rebuilds, migrations) and don't "
        "want them silently dropped."
    ),

    # ── Pacnew ─────────────────────────────────────────────────────────────
    ("pacnew", "default_strategy"): (
        "What recommendation to show for `.pacnew` files that don't match "
        "any rule below. `review_needed` is the safe default. Set to "
        "`keep_ours` if you generally trust your customizations over upstream."
    ),
    ("pacnew", "_section_rules"): (
        "Each rule matches a `.pacnew` file by glob pattern and prescribes a "
        "strategy (keep_ours / take_new / review_needed). Rules evaluate "
        "top-to-bottom; first match wins. Unmatched files use the default "
        "strategy above. Note is free text — shown in the Pacnew view as the "
        "reason. Use 'Restore defaults…' to rewind to the shipped 9 rules."
    ),

    # ── AUR ────────────────────────────────────────────────────────────────
    ("aur", "enabled"): (
        "If unchecked, the AUR phase is skipped entirely — `pacman -Syu` "
        "still runs but no AUR helper is invoked. Useful on machines where "
        "you maintain AUR packages outside archward."
    ),
    ("aur", "skip"): (
        "One-shot override: skip AUR just for this run. Equivalent to the "
        "`--no-aur` CLI flag. Reset to off for the next run."
    ),
    ("aur", "helper_preference"): (
        "Order matters — first binary found on PATH wins. Recommended order: "
        "`yay` (broadest install base), `paru` (more actively maintained), "
        "`aurutils` (best-effort; requires chroot setup)."
    ),
    ("aur", "quarantine_enabled"): (
        "When checked, packages that fail to build repeatedly are quarantined "
        "and skipped for a configurable window. Quarantine is version-aware — "
        "a new upstream version clears it automatically. Disable only if you "
        "prefer to manage failing AUR packages entirely by hand."
    ),
    ("aur", "quarantine_min_failures"): (
        "How many distinct build failures (spaced at least 24 h apart) before "
        "a package is quarantined. Default 3 ≈ three separate update attempts. "
        "Lower to quarantine sooner; raise if you want to give flaky packages "
        "more chances before skipping them."
    ),
    ("aur", "quarantine_initial_days"): (
        "Days to skip a quarantined package before retrying it. After the "
        "window opens archward tries the build once — success clears "
        "quarantine; failure doubles the window (up to the maximum)."
    ),
    ("aur", "quarantine_max_days"): (
        "Upper bound on the retry window. A permanently broken PKGBUILD will "
        "never wait longer than this between retries. 28 days is a good "
        "balance: long enough to reduce noise, short enough to catch a fix "
        "within a month."
    ),

    # ── Pacman ─────────────────────────────────────────────────────────────
    ("pacman", "noconfirm"): (
        "Pass `--noconfirm` to pacman. archward's transaction preview catches "
        "the cases where --noconfirm picks wrong (replacements, provider "
        "conflicts). If you regularly hit those, turn this off and approve "
        "manually in a terminal."
    ),
    ("pacman", "extra_args"): (
        "One pacman flag per line. Common: `--needed` (skip up-to-date), "
        "`--overwrite /etc/foo` (allow specific path conflicts). Test in a "
        "terminal before adding here."
    ),

    # ── Verify ─────────────────────────────────────────────────────────────
    ("verify", "enabled"): (
        "If unchecked, the post-update verify phase is skipped. The final "
        "RESULT tag will reflect only the update outcome (no kernel-match or "
        "service-check signals)."
    ),
    ("verify", "reboot_log"): (
        "Path to a 'reboot recommended' log file. archward warns if its mtime "
        "is newer than the snapshot timestamp. EndeavorOS ships "
        "`eos-reboot-required` which writes here; on other distros, clear "
        "this to disable the check."
    ),
    ("verify", "security_advisories"): (
        "When ON (default), the verify phase fetches the Arch Security "
        "Advisory feed and warns if any installed packages have open CVEs. "
        "Critical and High advisories produce a FAIL row; Medium and Low "
        "produce a WARN. Automatically skips if `arch-audit` is installed "
        "(defers to that tool to avoid double-reporting). Disable if you "
        "check security.archlinux.org through another workflow."
    ),
    ("verify", "stale_libs"): (
        "When ON, the verify phase scans running processes for deleted shared "
        "library files (.so) that are still mapped into memory. After a package "
        "update, long-running services continue using the old library version "
        "until restarted — this check surfaces which ones need a restart. "
        "Off by default.\n\n"
        "Use the 'Enable full coverage' button to let archward scan system "
        "services (sshd, NetworkManager, etc.) in addition to user-visible "
        "processes. This writes a NOPASSWD sudoers entry via askpass — archward "
        "shows you the exact content before writing. Without it, only "
        "user-visible processes (KDE/Plasma, pipewire, browsers) are scanned."
    ),

    # ── Privilege ──────────────────────────────────────────────────────────
    ("privilege", "mode"): (
        "How archward escalates for sudo'd operations.\n"
        "• `auto` / `persistent_sudo` — askpass + upfront timestamp warmup "
        "(best for desktop sessions).\n"
        "• `askpass` — explicit askpass per call, no warmup.\n"
        "• `pkexec` — route through polkit (reserved for a future phase)."
    ),
    ("privilege", "askpass"): (
        "Override the askpass binary path. Leave blank to auto-discover "
        "(ksshaskpass → lxqt-openssh-askpass → ssh-askpass). Set this if "
        "your DE ships an askpass under a non-standard name."
    ),

    # ── Hooks ──────────────────────────────────────────────────────────────
    ("hooks", "_section"): (
        "Optional shell commands that run at pipeline checkpoints. Useful "
        "for: triggering a backup before the update, sending custom "
        "notifications, syncing your dotfiles to a snapshot, etc. Commands "
        "run via /bin/sh -c so pipes, env vars, redirection all work."
    ),
    ("hooks", "pre_update"): (
        "One shell command per line. Each runs after risk-approval and "
        "before pacman -Syu. Non-zero exit logs a warning; with "
        "fail_pipeline_on_error=true a failure aborts the update entirely."
    ),
    ("hooks", "post_verify"): (
        "One shell command per line. Each runs after the verify phase "
        "regardless of update success or failure. Non-zero exits log a "
        "warning but never abort the pipeline (the update already ran)."
    ),
    ("hooks", "timeout_seconds"): (
        "Per-hook timeout. A hook hung waiting on input would otherwise "
        "lock the pipeline forever; this kills it after N seconds. "
        "Increase for legitimately slow hooks (rsync over slow links etc.)."
    ),
    ("hooks", "fail_pipeline_on_error"): (
        "If checked, a non-zero exit from any pre_update hook aborts the "
        "pipeline before pacman runs. Useful for 'verify backup is fresh' "
        "hooks. post_verify hooks never abort regardless of this setting."
    ),

    # ── Verify remediation hints (v0.4.0) ─────────────────────────────────
    # Surfaced by the "What to do?" button per FAIL row in VerifyView.
    # Keyed by the normalized check name (hyphens → underscores). Bucket
    # name keys handle generic checks (services, plugin) where the
    # individual check name is unit-/plugin-specific.
    ("verify_hint", "kernel"): (
        "The running kernel doesn't match the just-installed kernel — DKMS "
        "modules built against the old version may misbehave. Reboot to "
        "load the new kernel; verify with `uname -r` after coming back up."
    ),
    ("verify_hint", "pacnew"): (
        "Pacman left .pacnew files in /etc — package defaults diverged from "
        "your customizations. Open the Pacnew view (left rail) to resolve "
        "each one: View Diff to compare, then Keep / Take New / Edit. "
        "Leaving them unresolved means the updated daemon may use stale config."
    ),
    ("verify_hint", "disk"): (
        "Free disk space on / fell below the configured floor during the "
        "update. Reclaim space: `sudo paccache -rk2` (drop old cached "
        "package versions), then re-check with `df -h /`. If still tight, "
        "raise gates.min_disk_gb in Preferences to match reality."
    ),
    ("verify_hint", "pacman_log"): (
        "pacman's log contains ALPM warnings or errors from this run. "
        "Inspect with `tail -100 /var/log/pacman.log`. Common causes: a "
        "file conflict (resolved with --overwrite), a hook script failure, "
        "or a package returning non-zero from its install scripts."
    ),
    ("verify_hint", "reboot_log"): (
        "A 'reboot recommended' marker is newer than the snapshot. The "
        "kernel, glibc, or systemd was updated and a session/userspace "
        "restart won't pick it up cleanly. Reboot at your next convenience."
    ),
    ("verify_hint", "service"): (
        "A systemd unit that was supposed to be active isn't. Diagnose with "
        "`systemctl status <unit>` for the immediate state and "
        "`journalctl -xeu <unit>` for the failure trail. If the unit was "
        "intentionally retired, remove it from Preferences → Services → "
        "to_verify (or run `archward --detect`)."
    ),
    ("verify_hint", "plugin"): (
        "A third-party verify plugin produced this FAIL. The check's message "
        "column above is the plugin's own diagnostic. If the plugin itself "
        "is broken, uninstall it (`pip uninstall <name>`) — archward's "
        "built-in checks will continue without it."
    ),

    # ── Cache (v0.4.4) ────────────────────────────────────────────────────
    ("cache", "_section"): (
        "archward's rollback works by reinstalling the OLD package from "
        "/var/cache/pacman/pkg/. Your pacman cache policy decides whether "
        "that old package still exists when you need it. This tab shows "
        "the live policy + a rollback-safety verdict, and applies "
        "environment presets (Home / Workstation / Server / "
        "Mission-critical). A post-transaction cleaning hook is the "
        "dangerous case — it deletes the rollback substrate inside the "
        "same update archward runs."
    ),

    # ── Verify remediation hints (continued) ──────────────────────────────
    ("verify_hint", "boot_integrity"): (
        "An initramfs is older than its kernel — the mkinitcpio/dracut "
        "pacman hook didn't run or failed, so the running kernel and its "
        "initramfs are out of sync and the system may not boot. "
        "Regenerate it BEFORE rebooting: `sudo mkinitcpio -P` (mkinitcpio) "
        "or `sudo dracut-rebuild` / `sudo dracut --regenerate-all -f` "
        "(dracut). If you also just installed a brand-new kernel flavour, "
        "refresh the boot menu too: `sudo grub-mkconfig -o "
        "/boot/grub/grub.cfg` (GRUB) or `sudo bootctl update` "
        "(systemd-boot) — a routine same-flavour kernel update does NOT "
        "need this."
    ),
    ("verify_hint", "rollback_cache"): (
        "Pre-update versions of the packages just updated are no longer in "
        "/var/cache/pacman/pkg/ — a cache-cleaning hook or aggressive "
        "paccache policy deleted them. archward can't roll these back. "
        "Pull the old version from https://archive.archlinux.org/ and "
        "`sudo pacman -U` it, or adjust the policy in Preferences → Cache "
        "so this doesn't happen next time."
    ),
    ("verify_hint", "orphans"): (
        "These packages were installed as dependencies but nothing currently "
        "requires them. Review each one with `pacman -Qi <pkg>` — if you're "
        "not using it, remove it with `sudo pacman -Rns <pkg>` (or remove "
        "multiple at once). Some orphans are intentional (e.g. a standalone "
        "tool with no reverse deps); leave them if they're wanted."
    ),
    ("verify_hint", "security_advisories"): (
        "Open security advisories remain unpatched on your system. Run "
        "`pacman -Syu` to pull available fixes, or see "
        "https://security.archlinux.org/ for details and workarounds. "
        "Critical and High severity advisories should be addressed promptly. "
        "If `arch-audit` is installed, it will handle this check instead."
    ),
    ("verify_hint", "stale-libs"): (
        "Restart each listed service to load the updated libraries.\n"
        "For system services:\n"
        "  sudo systemctl restart <unit>\n"
        "For user services (kwin_wayland, pipewire, wireplumber, etc.):\n"
        "  systemctl --user restart <unit>\n"
        "Or log out and back in to restart all user-session services at once.\n"
        "A full reboot resolves all stale-library issues simultaneously."
    ),

    # ── Profiles ──────────────────────────────────────────────────────────
    ("profiles", "_section"): (
        "Profiles are named alternate config files at "
        "~/.config/archward/profiles/<NAME>.toml. The default config.toml "
        "is shown as a switchable pseudo-profile (★ marks the active one). "
        "Use profiles when one machine wears multiple hats — lab vs daily, "
        "lenient vs enforcing hooks, baremetal vs VM — without juggling "
        "files. Switching reloads the running window in place; refused "
        "while a pipeline is running."
    ),
    ("profiles", "remember_last_used"): (
        "When enabled, archward-gui launched without --profile reopens "
        "whatever profile was active when you last closed the window. "
        "Disabled by default to avoid hidden state. Only affects the GUI; "
        "the CLI always honors --profile explicitly."
    ),
}


def get(section: str, field: str) -> str:
    """Look up help text for a (section, field) pair. Returns '' if unknown."""
    return HELP.get((section, field), "")
