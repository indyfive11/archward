# Changelog

All notable changes to **archward** are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2] — 2026-05-14

### Added

- **Bulk rollback in the Snapshot Browser** — two new actions added to the
  right panel:
  - **Restore all configs from this snapshot** — iterates every captured
    config (sshd_config, pacman.conf, fstab, …), restoring each to its /etc
    location. Each file gets its own `.pre-rollback.bak` so per-file
    rollback paths are preserved. Failures don't abort the rest of the
    operation; the summary lists what succeeded and what didn't.
  - **Apply all package versions from this snapshot** — single atomic
    `pacman -U pkg1 pkg2 …` so the package transaction either fully
    succeeds or fully rolls back. Plan is shown in the confirm modal:
    per-package current → target diffs, packages skipped (not in cache),
    boot-critical warnings.

- **Safety net for bulk package apply** — a pre-rollback snapshot is taken
  automatically before the bulk operation runs, so the rollback-of-rollback
  path is always available. If the pre-rollback snapshot fails the bulk
  operation aborts before touching any package.

- **Type-YES confirmation gate** — when the change set includes any
  boot-critical package (`glibc`, `lib32-glibc`, `systemd`, `systemd-libs`,
  `openssl`, `lib32-openssl`), a second modal appears requiring the user
  to type "YES" (uppercase) before pacman is invoked. Getting these wrong
  can leave the system unbootable; the friction is intentional.

- New `pipeline/rollback.py` primitives:
  - `BulkResult` dataclass with `changed` / `skipped` / `per_item_results`.
  - `plan_bulk_package_apply()` computes the change set against current
    state; returns (changes, skipped) tuples.
  - `restore_all_configs()` and `apply_all_packages()` are pure functions
    — UI orchestrates confirmation + pre-snapshot + worker thread.
  - `BOOT_CRITICAL` frozenset of package names the UI gates on.

### Tests

- 125 unit tests (118 baseline + 7 covering the bulk planner: unchanged
  skipped, not-in-cache skipped, real changes detected with full tuple
  shape, boot-critical refusal without override, override actually invokes
  pacman -U, no-changes bypasses pacman entirely, BOOT_CRITICAL contains
  the expected name set).

## [0.2.1] — 2026-05-14

### Added

- **Direction-aware package rollback actions** — SnapshotBrowser now labels
  the action button `Upgrade to X` when the snapshot's version is newer than
  the currently-installed one and `Downgrade to X` when it's older. Modal
  title, confirmation body, and log line all match. Boot-critical /
  kernel-downgrade warnings only fire on actual downgrades. Direction is
  computed via a new `pacman.query.vercmp()` wrapper around the `vercmp`
  binary that ships with pacman (handles epoch prefixes, pkgrel suffixes,
  same rules pacman uses for dependency resolution).

- **Non-blocking rollback actions** — `_RollbackWorker(QThread)` now runs
  restore_config and downgrade_package off the main thread. A
  QProgressDialog with an indeterminate spinner appears while pacman -U or
  the file ops are running so the GUI no longer freezes for the few seconds
  the operation takes. No Cancel button: interrupting pacman -U mid-flight
  is unsafe by the same logic that keeps the main pipeline from killing
  pacman during updates.

### Tests

- 118 unit tests (112 baseline + 6 covering vercmp: a<b, a>b, equal, pkgrel
  bump, epoch trump, plain dotted versions; gracefully skipped if the
  vercmp binary isn't on PATH).

## [0.2.0] — 2026-05-14

### Added

- **Snapshot Browser + granular rollback** (minor bump for new capability).
  New "Snapshot Browser…" toolbar button opens a modal that lists every
  snapshot in `general.snapshot_dir` (newest-first with relative age), shows
  per-snapshot metadata (timestamp, distro, kernel-at-snapshot, AUR helper),
  and provides per-file / per-package rollback actions.

  Two action types:
  - **Restore config** — copies a snapshot config back to its `/etc` location.
    Backs up the live file to `<file>.pre-rollback.bak` first, then preserves
    the *current* file's ownership and mode on the restored copy (so a
    snapshot taken when sshd_config was 644 doesn't loosen a since-hardened
    600 file).
  - **Downgrade package** — runs `sudo pacman -U <cached pkg>` for the
    snapshot's version, sourced from `/var/cache/pacman/pkg/`. Refuses to
    act if the requested version isn't already cached (no network fetch).
    Boot-critical packages (glibc / systemd / openssl) and kernel downgrades
    get an extra-loud confirm-modal warning.

- New `pipeline/rollback.py` with `RollbackOp` / `RollbackResult` dataclasses,
  `restore_config`, `downgrade_package`, `find_package_in_cache`,
  `parse_critical_packages`, `list_snapshot_configs`. Shaped so v0.2.1's
  bulk variants (`restore_all_configs`, `downgrade_critical`) are just
  iteration.

- DiffDialog reuse: "View Diff" on a snapshot config opens a unified-diff
  modal of the current `/etc` file vs the snapshot's copy (theme-aware
  highlighting from v0.1.4).

### Tests

- 107 unit tests (97 baseline + 10 covering rollback primitives:
  critical.txt parsing, config-filename mapping, cache lookup with
  exact-name boundary, version-not-cached fallback, suffix variants
  (.zst/.xz/.gz), RollbackOp immutability).

## [0.1.4] — 2026-05-14

### Added

- **Dark theme aware colors** across all phase views, result banner, and
  diff highlighter. Earlier releases used hard-coded Bootstrap-alert hex
  literals (`#155724` dark green, `#fff3cd` light amber, `#f8d7da` light
  pink) tuned for light themes; under Breeze Dark / Adwaita Dark those
  read as near-black on dark green or near-white on dark backgrounds.
- New `ui/theme.py` module exposing `is_dark_theme()` (YIQ luminance of
  active `QPalette.Window` color, integer math for boundary precision)
  and `status_palette()` returning a `StatusPalette` dataclass with
  light/dark variants of every status color (pass/warn/fail/skipped,
  high/medium/kernel risk, pacnew recommendations, result banner bg/fg
  pairs, diff add/del/hunk).
- Views and the result banner consume `status_palette()` at construction
  time. Theme switch mid-session won't repaint live widgets — restart
  archward to pick up the new colors.

### Tests

- 97 unit tests (89 baseline + 8 theme tests covering luminance threshold
  boundaries, palette selection on dark vs light, light/dark palette
  field parity, and no-QApplication fallback).

## [0.1.3] — 2026-05-14

### Added

- **Desktop notifications on pipeline completion** via `notify-send`
  (libnotify). Default-on; opt-out via `general.notify_on_completion = false`
  in TOML or the Preferences General tab checkbox. Silently disabled if
  libnotify isn't installed.
- **Urgency mirrors RESULT severity**:
  - `SUCCESS` / `NEEDS_REVIEW` → `low` (auto-dismiss)
  - `REBOOT_NEEDED` / `PACNEW_MERGE_NEEDED` → `normal`
  - `VERIFY_FAILED` / `UPDATE_FAILED` → `critical` (persists until dismissed)
- **Body composer** surfaces verify FAIL/WARN counts, AUR build failures
  (first 3 names + "+N more"), abort reasons, and secondary tags so the
  notification carries the same context the result strip does.
- Wired from both CLI (after final RESULT print) and GUI (`MainWindow._on_pipeline_done`).

### Tests

- 89 unit tests (80 baseline + 9 covering the notification composer:
  urgency mapping, body framing per RESULT tag, AUR failure truncation,
  secondary tag annotation, None-summary handling).

## [0.1.2] — 2026-05-14

### Added

- **Inline help text on Preferences fields.** Every editable field on
  General / Gates / Risk / Services / Pacnew / AUR / Pacman / Verify /
  Privilege now has a small gray help label explaining what it does and what
  the consequences of changing it are. Strings live in a new
  `ui/dialogs/help_text.py` keyed by `(section, field)` so the copy is
  centralized and easy to update.

## [0.1.1] — 2026-05-14

### Added

- **Pacnew interactive merge in the GUI** — per-row action buttons (View Diff,
  Keep Ours, Take New, Edit, Leave). View Diff opens a syntax-highlighted
  unified-diff modal (red `-` / green `+` / gray `@@`), reading via `sudo cat`
  fallback for root-owned originals. Edit launches meld / kdiff3 / kompare via
  `sudo -A` with a sudoedit fallback hint. Apply actions reuse the existing
  permission-preserving `take_new` path.
- **Rail click navigation** — clicking a row in the phase rail jumps the
  content stack to that phase's view. The active phase row is now highlighted
  via single-selection so the user can see where they are in the stack.
- **Auto-jump to actionable view at completion** — pipeline finish lands the
  stack on the view that matters: `pacnew` when files need attention,
  `verify` on FAIL. Avoids the v0.1.0 UX where the result strip read
  "Pacnew Merge Needed" but the stack was stuck on verify.
- **`ARCHWARD_PACNEW_INCLUDE_ALL=1` test-mode env var** — bypasses the
  `find_pacnew_files()` mtime filter so pre-staged synthetic `.pacnew` files
  show up in PacnewView for regression testing. Logs a warning when active.

### Tests

- 80 unit tests (78 baseline + 2 covering the env-var bypass).

## [0.1.0] — 2026-05-14

Initial release.

### Added

- **Safe-update pipeline** for Arch-based Linux distributions
  (Arch, EndeavourOS, Manjaro, CachyOS, Garuda, Artix, and anything with
  `arch` in `ID_LIKE`):
  - Pre-flight: pacman `db.lck` detection + single-instance archward lock.
  - Snapshot: packages, configs (pacman.conf, mirrorlist, fstab, grub,
    sshd_config + sshd_config.d/, resolved.conf, sudoers.d/), network state
    (ip addr / ss -tlnp / wg show), services, kernel + cmdline, pacnew
    baseline.
  - Gates: snapshot freshness, free disk on `/`.
  - Risk classification: explicit HIGH list, kernel patterns (incl. headers)
    with exclude list, MEDIUM fnmatch patterns, LOW fallthrough.
  - Transaction preview via `pacman -Sup` against the checkupdates DB —
    surfaces replacements / conflicts that `--noconfirm` would silently default.
  - Official update: `sudo pacman -Syu --noconfirm --noprogressbar
    --color=never`, line-buffered + ANSI-stripped streaming.
  - AUR phase: auto-detect `yay` → `paru` → `aurutils`, build-failure
    capture (last 50 lines per failed package).
  - Pacnew: rule-based recommendation (sshd_config, mirrorlist, pacman.conf,
    fstab, grub, resolved.conf, faillock.conf, sysctl.d/*, *.hook), take_new
    preserves original ownership/mode.
  - Verify: kernel match, .pacnew remaining, disk, pacman.log scan,
    EndeavorOS reboot-recommended log, per-unit `systemctl is-active` for
    configured services.
  - Report: `RESULT:SUCCESS / NEEDS_REVIEW / REBOOT_NEEDED /
    PACNEW_MERGE_NEEDED / VERIFY_FAILED / UPDATE_FAILED`.
- **CLI** (`archward`): `--dry-run`, `--auto`, `--detect` (proposes config
  diff against live system), `--no-aur`, `--write-config`, `--yes`.
- **GUI** (`archward-gui`):
  - Single QMainWindow, 9-row phase rail, QStackedWidget per-phase content
    view, collapsible log pane, persistent result strip at the bottom.
  - Per-phase views: snapshot progress, gates table, risk HIGH/MEDIUM/LOW
    tree with transaction-preview banner, update stream pane (shared
    official + AUR), pacnew table, verify grouped by bucket.
  - HIGH-risk approval and recoverable gate-override route through modal
    QMessageBox via `BlockingQueuedConnection`.
  - Preferences dialog with 10 tabs (General, Gates, Risk, Services, Pacnew,
    AUR, Pacman, Verify, Privilege, Advanced) editing the TOML
    schema in place; Save validates the whole draft via Pydantic.
  - Re-detect button proposes diff against the current draft; Reset to
    defaults with confirmation; Open config.toml in `$EDITOR`.
- **Config**: TOML loader at `~/.config/archward/config.toml` with
  per-section ValidationError recovery, path expansion (`~/.local/state/...`
  literal expansion), first-run defaults bootstrap.
- **Auto-detect**: distro (via `ID` and `ID_LIKE`), kernels (excludes
  firmware/docs/tools), AUR helper, enabled+active services, pacnew
  baseline.
- **Cancellation contract** (audit A3): SIGINT / GUI Cancel sets the cancel
  event but never kills pacman or AUR helpers mid-flight.
- **78 unit tests** covering risk classification (incl. headers + firmware
  exclude), pacnew rule matching, RESULT precedence, TOML loader edge cases,
  detect-module filters, AUR helper parsing, build-failure attribution.

### Known limitations (deferred to later releases)

- Pacnew per-row apply buttons + diff dialog (Phase-5 polish; current
  behavior is read-only display).
- Inline help text on Preferences fields (every tab needs a one-line
  explanation per audit follow-up; PLAN.md §v2).
- aurutils adapter is best-effort — see PKGBUILD optdepends note.
- v1 verify scope is universal checks + opt-in services only; network
  probes, HTTP health checks, port-listen, mountpoint checks reserved for
  v2 hooks (`pipeline/hooks.py` is a stub today).

[Unreleased]: https://github.com/indyfive11/archward/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/indyfive11/archward/releases/tag/v0.2.2
[0.2.1]: https://github.com/indyfive11/archward/releases/tag/v0.2.1
[0.2.0]: https://github.com/indyfive11/archward/releases/tag/v0.2.0
[0.1.4]: https://github.com/indyfive11/archward/releases/tag/v0.1.4
[0.1.3]: https://github.com/indyfive11/archward/releases/tag/v0.1.3
[0.1.2]: https://github.com/indyfive11/archward/releases/tag/v0.1.2
[0.1.1]: https://github.com/indyfive11/archward/releases/tag/v0.1.1
[0.1.0]: https://github.com/indyfive11/archward/releases/tag/v0.1.0
