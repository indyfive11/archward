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
        "Old snapshots beyond this count are pruned at app exit. 10 covers "
        "5-10 weeks of bi-weekly updates; raise to 30+ for a longer rollback "
        "history. Each ~100-500 KB, so disk cost is negligible."
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
        "Rules are edited by hand in config.toml. Use the Advanced tab's "
        "'Open config.toml' to launch your editor."
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
