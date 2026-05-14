# Changelog

All notable changes to **archward** are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/indyfive11/archward/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/indyfive11/archward/releases/tag/v0.1.4
[0.1.3]: https://github.com/indyfive11/archward/releases/tag/v0.1.3
[0.1.2]: https://github.com/indyfive11/archward/releases/tag/v0.1.2
[0.1.1]: https://github.com/indyfive11/archward/releases/tag/v0.1.1
[0.1.0]: https://github.com/indyfive11/archward/releases/tag/v0.1.0
