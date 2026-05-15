# Changelog

All notable changes to **archward** are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] — 2026-05-15

**Theme: keep users in archward.** Six features close the GUI's biggest
"escape paths" — places where the existing workflow forced users to drop
to a terminal or hand-edit `config.toml` (and in doing so sidestep
archward's snapshot/gate/verify safety net).

### Added

- **F1 — GUI-editable pacnew rules.** The Preferences → Pacnew tab is no
  longer read-only; rules can be added, edited, reordered, and removed
  via an editable table. Strategy is a per-row combo
  (`keep_ours` / `take_new` / `review_needed`). A "Restore defaults…"
  button rewinds to the 9 shipped rules after confirmation. Mirrors the
  Services tab pattern.

- **F2 — In-GUI pacman/AUR interactive prompts.** When
  `pacman.noconfirm=False`, archward routes pacman + AUR helpers
  through a PTY so their `[Y/n]` / provider-selection prompts surface
  inside the GUI instead of hanging the run. A new inline input row at
  the bottom of the Update view lights up on each detected prompt
  (pre-filled with the sensible default — `Y` for yes/no, `1` for
  numeric), accepts the user's answer, and writes it back to the
  subprocess stdin. Cancel mid-prompt routes a SIGINT to the subprocess
  group; pacman handles it cleanly between transactions.
  - New `archward.pacman.prompts` module: `PROMPT_PATTERNS` regex
    table + `PromptKind` enum (`YES_NO` / `NUMERIC` / `FREE`).
  - `pacman.runner.run_streaming()` gains a `prompt_provider` kwarg.
    When `None` (default), the legacy pipe-based code path runs
    unchanged — zero regression risk for `noconfirm=True` users.
  - `aur/adapters/_pacman_like.py` drops the hardcoded `--noconfirm`;
    when interactive, also passes `--editmenu=false --diffmenu=false
    --cleanmenu=false` so yay/paru don't spawn `$EDITOR` (F3 handles
    PKGBUILD review properly).
  - New `UpdatePrompter` (cross-thread bridge in
    `archward.ui.prompter`) mirrors the proven `GuiPrompter` pattern.

- **F3 — PKGBUILD review modal with per-package skip.** Now that yay /
  paru can run without `--noconfirm`, the AUR phase pre-fetches each
  pending PKGBUILD via `git clone --depth=1` of the AUR git repo and
  shows it in a modal — Approve / Reject / Cancel-review. Rejected
  packages are added to the helper's `--ignore` list so the remaining
  approved ones still build. Fetch failures surface Skip / Retry /
  Cancel-review buttons. KISS: plain-text PKGBUILD viewer, no syntax
  highlighting, no diff against previous version.
  - New `archward.aur.prefetch.fetch_pkgbuild()`.
  - New `archward.ui.dialogs.pkgbuild_review.PkgbuildReviewDialog`.
  - New `PkgbuildPrompter` (worker-thread → main-thread modal bridge).
  - `pipeline/update_aur.py` defines a `PkgbuildReviewer` Protocol and
    drives the loop between `list_pending` and `helper.run_update`.

- **F4 — Hook templates.** Each editor on the Preferences → Hooks tab
  gets an "Insert template…" combobox above it. Selection appends the
  template body (with a `# template: <name>` header line) to whatever
  the user has typed — append-on-select, never replace. Ships with 4
  prebaked snippets: btrfs `@home` snapshot, "refuse update if
  `/mnt/backup` is stale," Discord webhook on completion, restart
  user-level systemd services after kernel update.
  - New `archward.ui.dialogs.hook_templates` module with the
    `HOOK_TEMPLATES` dict.

- **F5 — Verify failure remediation hints.** The Verify view gains a
  4th column `Action`. FAIL rows with a registered hint show a
  "What to do?" button; click → `QMessageBox.information` with
  context-specific guidance (e.g. kernel mismatch → reboot, service
  inactive → `systemctl status / journalctl -xeu`). PASS / WARN rows
  and FAIL rows with no registered hint get nothing — no empty popups.
  Hints live in `help_text.HELP` under a new `verify_hint` section,
  keyed by check name (universal checks: `kernel`, `pacnew`, `disk`,
  `pacman_log`, `reboot_log`) or bucket (`service`, `plugin`).

- **F6 — Snapshot retention.** Two changes wire up the
  `keep_snapshots` setting that has been GUI-exposed but no-op since
  v0.1.0:
  - **Auto-prune at end of pipeline.** `run_pipeline()` now calls
    `prune_snapshots(cfg)` after the report phase. Keeps the N newest
    snapshots by mtime; deletes the rest. `keep_snapshots <= 0`
    disables.
  - **"Prune now…" button** in the snapshot browser. `QInputDialog`
    asks for the keep-count (defaults to the configured value); confirm
    dialog summarizes "delete N old snapshots, keep M newest"; refresh
    after.
  - New `archward.pipeline.retention` module with the standalone
    helper.

### Branding

The shield+A icon (teal `#0e7490`) is now the basis for the application's
visual identity. Centralized in `archward.ui.theme.BrandPalette` (light +
dark variants). Touches:

- **Window icon wired at runtime.** `app.setWindowIcon()` + Wayland
  `app.setDesktopFileName("archward")` + `StartupWMClass=archward` in
  the `.desktop` file. Plasma now associates the running window with the
  launcher so the taskbar uses our icon, not a default. SVG bundled at
  `src/archward/data/archward.svg` for `pip install`-only setups.
- **Toolbar brand cue.** Shield icon + "Archward <version>" label
  added directly to the toolbar (two plain QLabel widgets — earlier
  attempts wrapped them in a custom QHBoxLayout container which
  collapsed under heavy paint traffic during pacman -Syu).
- **About dialog.** New `Help → About` modal showing the icon at 96px,
  version, license, GitHub / AUR links.
- **Phase rail colors.** Running rows bolded + brand teal foreground.
  Passed (done) rows get the brand teal glyph. Warn / fail / skipped
  keep their semantic colors. Pending rows stay default. Palettes are
  cached at construction time — `set_status()` fires many times during
  an update phase, and per-call palette lookups starved the paint queue
  in an earlier draft (visible as widgets going black during pacman -Syu).
- **Result banner success.** RESULT:SUCCESS uses brand-themed teal
  instead of generic green. Failures stay red, info states stay amber.
- **Verify view bucket headers** ("universal", "services", "plugin",
  "hooks") render in brand teal bold.
- **Preferences section help.** Every `_section_help()` banner gets a
  3px teal left border, tying tabs into the brand.
- **Inline prompt + Send button** (F2) themed in brand teal — text
  color only. Earlier work added a `:default` pseudo + `setDefault(True)`
  on the hidden Send button, which corrupted the parent window's
  default-button chain and prevented sibling buttons (Preferences,
  About) from repainting until a hover event triggered a style poll.
- **PKGBUILD review modal header** (F3) gets a faint teal background
  with a brand teal left border.
- **`_open_in_editor()` priority fix** (Profiles tab bug) — now
  `$VISUAL` → `xdg-open` → `$EDITOR`. Previously a `$EDITOR=nvim`
  user clicking "Open in editor" got nothing (terminal editor spawned
  without a TTY → silent fail).

### Changed

- `_PacnewTab` rewritten — no longer extends `QTreeWidget` read-only;
  uses `QTableWidget(0,3)` with per-row strategy combobox. `dump()`
  rebuilds the `PacnewConfig.rules` tuple from cell contents (blank
  pattern rows dropped silently to match `_ServicesTab` behavior).
- Help text key `("pacnew", "_section_rules")` rewritten — no longer
  says "edit by hand in config.toml."
- `_HooksTab` layout adds a horizontal row above each editor for the
  template combobox.
- `pipeline/update_aur.run_aur_update()` gains `pkgbuild_reviewer` +
  `prompt_provider` kwargs.
- `pipeline/update_official.run_official_update()` gains
  `prompt_provider` kwarg.
- `aur.helper.AurHelper.run_update()` Protocol signature extended with
  `noconfirm` + `prompt_provider` kwargs (default-preserving).
- All AUR adapters (`yay`, `paru`, `aurutils`) honor the new kwargs.

### Tests

224 → **287**. New files:
- `test_preferences_pacnew_tab.py` — F1 round-trip + add/remove/restore.
- `test_prompt_detection.py` — every regex matches its fixture line.
- `test_pacman_runner_pty.py` — PTY-backed Linux-only smoke against
  a `bash -c 'read -p ...'` fixture (yes-no, no-prompt, SIGINT cancel,
  pipe-path backward compatibility).
- `test_aur_adapter_yay.py` — extended with argv-shape assertions for
  the new `noconfirm`/`prompt_provider` kwargs and `--ignore` plumbing.
- `test_prefetch.py` — mocked `git clone` for success / clone-failure /
  timeout / missing-binary / missing-PKGBUILD branches.
- `test_pkgbuild_review_dialog.py` — modal result enum per button.
- `test_verify_hints.py` — column 3 button presence per status, hint
  key normalization (services/plugin bucket override), every shipped
  hint resolves to non-empty text.
- `test_hook_templates.py` — dict shape, kind validity, formatted
  insertion (header comment + trailing separator).
- `test_retention.py` — newest-N kept, keep≤0 disabled, explicit
  override, missing dir graceful, dirs without `.timestamp` ignored.

## [0.3.5] — 2026-05-14

### Added

- **"Diff vs default…" button on the Profiles tab.** Opens a modal
  unified-diff viewer comparing the selected profile against archward
  defaults. Useful for "what does this profile actually change?"
  without dropping to a shell. Disabled when the default config row is
  selected (would diff against itself). Reuses the existing
  `_DiffHighlighter` from the .pacnew diff viewer for consistent +/-
  coloring across the app.
  - New pure-Python helper `archward.config.diff.unified_diff(a, b, …)`
    serializes both `ConfigModel`s to TOML via `tomli_w` and runs the
    output through `difflib.unified_diff`. Trivially unit-testable
    independent of Qt; 5 new tests in `test_config_diff.py`.
  - New `TextDiffDialog` sibling class in
    `archward/ui/dialogs/diff_dialog.py` that takes pre-rendered diff
    text (vs the file-reading `DiffDialog` used for .pacnew).

- **"Import…" and "Export…" buttons on the Profiles tab.** Move
  profiles between machines without dropping to a shell.
  - **Import** opens a `QFileDialog` for a `.toml` from anywhere on
    disk, validates it parses as an archward config, prompts for a
    profile name (defaulting to the source filename's stem when valid),
    and copies the file into `~/.config/archward/profiles/`. Refreshes
    the list and selects the imported entry.
  - **Export** opens a `QFileDialog.getSaveFileName` and copies the
    selected profile to the chosen path. Disabled when the default
    config row is selected.

### Tests

219 → **224**. New `tests/unit/test_config_diff.py` covers the diff
helper (identical configs → empty diff, additions show as `+`,
removals show as `-`, custom header labels, line-terminator
invariants).

## [0.3.4] — 2026-05-14

### Added

- **Remember-last-used profile (opt-in).** A new checkbox at the bottom
  of the Preferences → Profiles tab — "Remember last-used profile
  across launches". When enabled, `archward-gui` launched without
  `--profile` reopens whatever profile was active when you last closed
  the window. Off by default to avoid hidden state. Only affects the
  GUI; the CLI continues to honor `--profile` explicitly. Backed by
  QSettings, so the toggle and the remembered path live in
  `~/.config/archward/archward.conf` and are independent of any
  profile's `config.toml`.

  - New module `archward.ui.persistent_state` exposes
    `get_remember_last_profile()`, `set_remember_last_profile()`,
    `get_last_used_profile_path()`, `set_last_used_profile_path()`,
    `clear_last_used_profile_path()`. Self-protecting: returns `None`
    if the file no longer exists, so a profile you delete won't
    resurrect a stale path on the next launch.
  - `MainWindow.__init__()` writes the active path to QSettings on
    every successful construction (if the toggle is on).
  - `MainWindow._on_profile_switch_requested()` writes the new path
    on every in-window switch (same condition).
  - `cli.main_gui()` consults QSettings after creating the
    `QApplication` (so the org/app names are set) and uses the
    remembered path only when `--profile` wasn't passed.

- **Profiles tab section help.** Italic intro paragraph at the top of
  the Profiles tab explaining what profiles are and the switch
  semantics. Matches the section-help pattern already used by the
  Hooks and Risk tabs. Closes a minor polish gap: the Profiles tab
  shipped in v0.3.2 without a section intro.

### Tests

211 → **219**. New `tests/unit/test_persistent_state.py` covers the
QSettings round-trip: default-off, set-persists, returns-None when
toggle is off / file is missing / key is cleared, and the
`None`-path-records-default semantics. Tests use an isolated
QSettings storage path so the user's real `archward.conf` is never
touched.

## [0.3.3] — 2026-05-14

### Added

- **Custom verify probes via entry points.** Third-party packages can
  contribute additional checks to the verify phase without forking
  archward. Plugins register a callable in the `archward.verify_checks`
  entry-point group with the contract
  `(cfg: ConfigModel, snapshot: Snapshot) -> list[VerifyCheck]`. Each
  produced check lands in a new third bucket `plugin` alongside the
  existing `universal` and `services` buckets in the Verify view. A
  raising plugin is contained — failure becomes a synthetic FAIL row
  (`plugin raised <Class>: <message>`) so other plugins still run.
  See [`docs/plugins.md`](docs/plugins.md) for a worked example.

  - Closes the last open seam from PLAN.md §v2 around verify
    extensibility (one remaining open item — service auto-prune —
    landed in this same release).
  - `VerifyCheck.bucket` Literal extended to `["universal", "services", "plugin"]`.
  - `run_verify()` now takes the full `Snapshot` (not just the path)
    so plugin authors don't have to re-parse on-disk snapshot state.
  - Plugins discovered at archward start-up via
    `importlib.metadata.entry_points(group="archward.verify_checks")`.
    Restart archward to pick up new plugins.

- **Stale service detection across three surfaces.** Before this
  release, `archward --detect` only proposed *additions* to
  `services.to_verify`, and if a unit was removed from disk (file
  deleted, backing package uninstalled), it lingered in the verify
  list and showed up as a generic `not active` FAIL with no
  indication that the underlying problem was config drift, not a
  stopped service. v0.3.3 closes the loop with three coordinated
  changes:

  1. **Verify-phase distinguishes "gone" from "stopped"** —
     `_service_check` calls `unit_exists()` first. A missing unit
     becomes a WARN (`no such unit (file removed/uninstalled) — run
     archward --detect to clean up`), not a severity-based FAIL.
     Every run surfaces the staleness; the message points the user
     at the fix.

  2. **`archward --detect` proposes removals** with a separate
     `Remove N stale service entries? [y/N]` prompt (default N so
     accidental unit-file moves don't silently drop entries). GUI
     Preferences → Advanced → Re-detect mirrors with an independent
     `QMessageBox.question` for the removals.

  3. **Opt-in inline auto-prune** via a new
     `services.auto_prune: bool = false` config flag. When True,
     the verify phase silently drops stale entries from
     `services.to_verify` AND writes the pruned config back to disk
     in the same idempotent path `--detect` uses. A single PASS row
     `auto-pruned N stale unit(s)` records what was removed for
     audit-trail visibility. Off by default; configurable via the
     Preferences → Services tab checkbox.

  Helpers:

  - `archward.system.services.unit_exists(unit)` uses
    `systemctl cat --no-pager <unit>` (exit 0 = file resolves).
  - `ConfigDiff.service_removals: tuple[str, ...]` surfaces stale
    entries; `detect_stale_services(cfg)` filters `to_verify`
    through `unit_exists`.
  - `apply_detection(...)` gains `accept_service_removals: bool = False`.
  - `run_verify(cfg, snapshot, bus, *, config_path=None)` accepts
    an optional `config_path` so the inline auto-prune can persist
    the pruned cfg. `run_pipeline()` accepts and threads
    `config_path` to honor the active `--profile`.

### Changed

- `pipeline.run_verify(cfg, snapshot, bus)` — second positional arg is
  now a `Snapshot` (was `Path` to the snapshot dir). Internal callers
  updated; the prior API surface had no third-party consumers.

### Tests

211 passing (193 → +18). New coverage:
- `tests/unit/test_verify_plugins.py` (13): plugin contract +
  failure isolation (8), stale-service WARN routing (2), inline
  auto-prune behavior (3).
- `tests/unit/test_detect.py` (5): stale detection, diff
  propagation, opt-in apply, default-off, additions+removals
  compose.

## [0.3.2] — 2026-05-14

### Added

- **Profiles (`--profile NAME`)** — run archward against a named config file
  at `~/.config/archward/profiles/<NAME>.toml` instead of the default
  `config.toml`. Useful for per-machine, per-role, or per-experiment
  configs without juggling files. Supported by both the CLI and the
  GUI. Fills in the v2 seam reserved since v0.1.0 (PLAN.md §11).

  - First run with `--profile foo` bootstraps
    `~/.config/archward/profiles/foo.toml` with defaults (same behavior
    as the default config), so creating a new profile is a one-liner.
  - **CLI:** the flag threads through every config-touching entry point:
    `archward --profile foo` (run), `archward --profile foo --detect`
    (auto-detect against the profile), `archward --profile foo
    --write-config` (overwrite a profile with defaults).
  - **GUI:** `archward-gui --profile foo` launches the window against
    the profile. The active profile name is shown in the window title
    (`Archward — profile: foo`) and status bar; the Preferences dialog
    title shows it too, its Advanced-tab "Active config file" label
    points at the profile path, and Save writes back to the profile
    file (not the default config). Snapshot/log dirs follow the
    profile's `[general]` overrides.
  - **GUI Profiles tab (new in Preferences):** in-place profile
    management without leaving the window. Lists every profile plus
    the default `config.toml` (as a switchable pseudo-profile, not
    renameable or deletable). Buttons: **Switch** (reloads the window
    against the selected profile; refused while a pipeline runs),
    **Open in editor**, **New from defaults…**, **Save current as
    new…**, **Rename…**, **Delete…**. Switching with unsaved edits
    prompts Save / Discard / Cancel; Save writes to the *current*
    profile before switching, so edits aren't lost or accidentally
    carried into the target. Renaming the active profile auto-saves
    any dirty draft to the new path so the post-rename reload
    preserves the user's work.
  - Profile names are validated against
    `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` so a malicious or typo'd argument
    cannot escape the profile directory (no leading dot, no path
    separators, no shell-meaningful characters). Invalid names exit
    with code 2 and a clear message — same validation in both
    front-ends, sharing `_resolve_config_path()` in `cli.py`.
  - New helpers in `archward.config.paths`: `profile_dir()`,
    `valid_profile_name()`, `profile_config_path()`.

### Changed

- `app.build_config()` / `app.setup_app()` now accept an optional
  `config_path: Path | None` argument so callers can override the
  default `~/.config/archward/config.toml` location. The CLI and GUI
  both use this to plumb `--profile` through; existing callers passing
  no argument retain the v0.3.1 behavior.
- `MainWindow.__init__()` and `PreferencesDialog.__init__()` /
  `_AdvancedTab.__init__()` now accept `config_path: Path | None`. The
  `archward-gui` entry point gains a minimal argparse (just `--profile`
  and `--version`) — other CLI flags remain CLI-only because their
  GUI equivalents are toolbar actions, not invocation knobs.
- `MainWindow._on_config_saved()` now calls `setup_logging()` on the
  freshly-saved `log_dir`. Previously, changing `[general].log_dir` via
  Preferences silently left the old `RotatingFileHandler` attached so
  the file handler kept writing to the previous path. Pre-existing
  latent bug; surfaced and fixed alongside the Profiles tab work
  because the new in-place switcher exercises the same reload path
  more often.

### Added (helpers)

- `archward.config.paths.iter_profiles()` — alpha-sorted list of valid
  profile stems under `~/.config/archward/profiles/`. Filenames with
  invalid stems are silently ignored (defense in depth).

## [0.3.1] — 2026-05-14

### Added

- **Hook output visibility** — hook outcomes are now first-class GUI state,
  not just log-pane text:
  - **Phase rail** gains two new rows: `Pre-hooks` (between Risk and Update)
    and `Post-hooks` (between Verify and Result). Each row shows pass/warn/
    fail status from its hook outcomes, same icon system as other phases.
  - **Verify view** gains a third bucket `hooks` alongside `universal` and
    `services`. Each hook row is tagged `[pre]` or `[post]`, shows the
    command (truncated to 70 chars; full command in the tooltip), exit
    code, status, and a one-line preview of the captured output. Multi-line
    output is attached as a child node.
  - **`HookResult` model** captures command, exit code, status (PASS / FAIL /
    TIMEOUT), output lines, and phase tag. `HookRunner` now returns
    `HookRunOutcome(proceed, results)` so the pipeline can plumb per-hook
    detail into `PipelineResult.pre_hook_results` /
    `PipelineResult.post_hook_results`.
  - Pipeline emits `PHASE_START` / `PHASE_RESULT` events for `hooks_pre` /
    `hooks_post` carrying the hook-results payload, so the GUI absorbs and
    renders them through the same routing path as other phases.

- **Hooks** — fills in the `pipeline/hooks.py:HookRunner` stub that's been
  reserved as a v2 seam since v0.1.0. Users can now wire shell commands
  into two pipeline checkpoints:

  - `[hooks].pre_update` — runs after risk-approval, before pacman -Syu.
    Useful for "verify backup is fresh", custom notifications, etc.
  - `[hooks].post_verify` — runs after the verify phase regardless of
    update outcome. Useful for "sync new state to backup", report
    generation, etc.

  Each command runs via `/bin/sh -c <cmd>` so pipes, env vars, and
  redirection all work without quoting gymnastics. `ARCHWARD_PHASE` is
  injected into the env so hooks can distinguish pre_update vs post_verify.
  Stdout/stderr from each hook is streamed into the archward log pane.

- New TOML schema additions:
  ```toml
  [hooks]
  pre_update = [
      "rsync -a ~/Documents /mnt/backup/",
      "echo Pre-update at $(date) >> ~/.archward-runs.log",
  ]
  post_verify = [
      "notify-send 'archward done' \"$ARCHWARD_PHASE\"",
  ]
  timeout_seconds = 60                  # per-hook timeout
  fail_pipeline_on_error = false        # default: warn only
  ```

  With `fail_pipeline_on_error = true`, any non-zero pre_update hook
  aborts the update before pacman runs. post_verify hooks never abort
  (the update already happened).

- **Hooks tab in Preferences** — 10th editable schema tab joins the
  existing 9. Two QPlainTextEdits (one command per line), a timeout
  spinbox, and the fail-on-error checkbox. Help text follows the same
  pattern as the rest of the dialog.

### Tests

- 148 unit tests (136 baseline + 12 covering HookRunner: empty pre/post
  sets, success, failure-no-abort, failure-with-abort, abort-stops-second-
  hook, no-abort-continues-past-failure, post-verify-never-aborts, timeout
  kills hung hook, shell features via /bin/sh -c, captured output_lines,
  phase tag propagation).

## [0.3.0] — 2026-05-14

MINOR bump for a new capability: per-row deselect in the Risk view.

### Added

- **Per-row package deselect in the Risk view** — the previous v0.1.x flow
  was a modal `QMessageBox` asking "Proceed with N HIGH RISK package(s)?
  [Yes/No]" — all-or-nothing. v0.3.0 replaces this with inline interaction:
  every row in the HIGH/MEDIUM/LOW tree now has a checkbox (defaulted
  checked = include in update), and the view gains **Proceed with update**
  / **Cancel update** buttons at the bottom. Unchecked package names flow
  through as `--ignore=<pkg>` flags on the pacman command line so pacman
  resolves the rest of the transaction without those packages.

- **Prompter Protocol extended** — `decide_high_risk(high) → (proceed,
  ignored_pkg_names)` replaces the boolean `confirm_high_risk`.
  Implementations:
  - **GuiPrompter** — activates the RiskView's buttons via a queued Qt
    signal, blocks on a `threading.Event`, returns the (proceed,
    deselected_names) pair when the user clicks.
  - **CliPrompter** — keeps the legacy Y/N prompt; returns an empty
    ignore list. CLI interactive deselect can land in a later release.
  - **AutoYesPrompter / AutoNoPrompter** — return `(True, [])` /
    `(False, [])` unchanged.

- **`PipelineResult.deselected_packages`** — tuple of package names the
  user dropped from the HIGH-risk run. Logged into the pipeline log as
  "User deselected N package(s): pkg1, pkg2, ..." so the audit trail
  matches what pacman actually saw.

- **`closeEvent` hardening** — `GuiPrompter.cancel_pending_decision()` is
  called when the main window closes mid-decision, so the worker thread
  doesn't hang waiting on user input that's no longer reachable.

### Tests

- 136 unit tests (125 baseline + 11 new):
  - 7 covering `decide_high_risk` across all 4 prompter implementations
    (auto-yes / auto-no / cli with y/yes/n/empty/EOF).
  - 4 covering `pacman_argv`'s `--ignore` flag handling (empty list, single
    package, multi-package, ordering with extra_args).

### Migration

- The `Prompter.confirm_high_risk(high) → bool` method is replaced by
  `decide_high_risk(high) → (bool, list[str])`. Custom prompter
  implementations need updating. Built-in prompters (CLI / Auto / Gui)
  already handle the new contract.

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

[Unreleased]: https://github.com/indyfive11/archward/compare/v0.3.5...HEAD
[0.3.5]: https://github.com/indyfive11/archward/releases/tag/v0.3.5
[0.3.4]: https://github.com/indyfive11/archward/releases/tag/v0.3.4
[0.3.3]: https://github.com/indyfive11/archward/releases/tag/v0.3.3
[0.3.2]: https://github.com/indyfive11/archward/releases/tag/v0.3.2
[0.3.1]: https://github.com/indyfive11/archward/releases/tag/v0.3.1
[0.3.0]: https://github.com/indyfive11/archward/releases/tag/v0.3.0
[0.2.2]: https://github.com/indyfive11/archward/releases/tag/v0.2.2
[0.2.1]: https://github.com/indyfive11/archward/releases/tag/v0.2.1
[0.2.0]: https://github.com/indyfive11/archward/releases/tag/v0.2.0
[0.1.4]: https://github.com/indyfive11/archward/releases/tag/v0.1.4
[0.1.3]: https://github.com/indyfive11/archward/releases/tag/v0.1.3
[0.1.2]: https://github.com/indyfive11/archward/releases/tag/v0.1.2
[0.1.1]: https://github.com/indyfive11/archward/releases/tag/v0.1.1
[0.1.0]: https://github.com/indyfive11/archward/releases/tag/v0.1.0
