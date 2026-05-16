# Changelog

All notable changes to **archward** are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.6] — 2026-05-15

**AUR build quarantine — chronically broken packages skip themselves.**
Packages that repeatedly fail to build (dotnet NuGet CVE blocks, checksum
mismatches, upstream build breakage) are now tracked automatically. After a
configurable number of counted failures (default 3, spaced ≥ 24h apart to
prevent run-inflation) the package enters a timed quarantine and is skipped
on subsequent runs with escalating backoff (7 → 14 → 28 days). Quarantine is
version-aware — a new upstream version clears it immediately. Every state
transition is logged in the AUR phase stream. CLI and Preferences exposure
give full visibility and manual control. Includes the v0.4.5 Awareness features
released together.

### Added

- **AUR build quarantine (F1).** New `archward.aur.quarantine` module tracks
  build failures per package (keyed by name + version). Failure counting
  enforces a 24-hour minimum gap between counted events. Quarantine activates
  after `aur.quarantine_min_failures` counted failures (default 3); the package
  is then skipped for `aur.quarantine_initial_days` days (default 7), with
  backoff doubling on each retry failure up to `aur.quarantine_max_days`
  (default 28). Quarantine clears automatically when a new upstream version is
  available, on retry success, or on manual clear. Resolved entries are kept
  as history. State file: `~/.local/state/archward/aur_quarantine.json`.

- **AUR phase transparency.** Phase start announces all active quarantine
  entries before touching the pending list. Skipped packages log their retry
  date. Retry-window packages log that they're being retried. On quarantine
  activation, the full build log tail is emitted to `log.warning()` for
  post-mortem. A lightweight error classifier (`_classify_error()`) matches
  the captured output against known patterns (dotnet NuGet audit, checksum
  mismatch, network errors, dependency failure, makepkg phase failure) and
  emits a one-line actionable hint to the phase log stream.

- **Pre-flight quarantine FYI.** If any packages are active (counting or
  quarantined), a single INFO-level message appears in the pre-flight log
  with a count and a CLI reference (`archward aur quarantine list`). Not a
  gate — no overridable WARN; the user already saw it on prior runs.

- **Result view quarantine section.** After a pipeline run, the result panel
  lists active quarantine entries (package, version, status, failure count,
  retry date) and points to Preferences → AUR for the full history.

- **`archward aur quarantine list`.** Prints a table of all quarantine entries
  (active and resolved): package, version, status, failure count, retry/resolved
  date, last error snippet. No sudo required. Always exits 0.

- **`archward aur quarantine clear [PKG] [--yes]`.** Clears one package or all
  active entries (counting + quarantined → resolved). Without PKG, lists
  affected entries and asks for confirmation unless `--yes`. Exits 2 if the
  named package isn't in quarantine state.

- **Preferences → AUR quarantine config.** Four new fields exposed in the AUR
  tab: `quarantine_enabled` (master switch), `quarantine_min_failures`,
  `quarantine_initial_days`, `quarantine_max_days`. All have inline help text.

- **Preferences → AUR quarantine history table.** Fully editable QTableWidget
  below the config section: active rows (counting + quarantined) allow editing
  failure count, retry-after date (YYYY-MM-DD), and status via QComboBox.
  Resolved rows are shown greyed-out and read-only. Three buttons: Clear
  selected, Clear resolved, Clear all. Changes are saved to the state JSON on
  dialog Save; discarded on Cancel.

- **stdin=DEVNULL in `_run_pipe()`** (defensive fix). yay/paru in non-
  interactive mode should never read stdin; passing DEVNULL prevents any
  accidental stdin inheritance.

### Tests

534 → **568** (+34). New files:
- `test_aur_quarantine.py` — 27 tests (check() action logic FRESH/COUNTING/
  SKIP/RETRY, record_failure() 24h gate + threshold + escalation + cap,
  record_success(), clear(), save/load roundtrip, corrupt JSON, _classify_error()
  patterns)
- `test_update_aur_quarantine.py` — 7 integration tests (quarantined package
  skipped, added to effective_ignore, retry window not ignored, version change
  clears quarantine, failure/success recording, disabled passthrough)

## [0.4.5] — 2026-05-15

**Awareness: Arch News pre-flight, orphan detection, security advisories, reliability.**
archward now knows what's happening in the ecosystem before and after an update.
Every major recent breakage incident — NVIDIA Pascal driver drop, Plasma 6.4 X11
session removal, linux-firmware conflicts, CHAOS RAT AUR malware — was announced
on archlinux.org/news/ first. archward now fetches that feed before each update.
The verify phase gained two new universal checks: orphaned packages and open
Arch Security Advisories. Four reliability gaps closed: three missing subprocess
timeouts and a blocking GUI sudo warmup.

### Added

- **Arch News RSS pre-flight check (F1).** New `archward.system.arch_news`
  module fetches `archlinux.org/feeds/news/` (Atom, stdlib only, no new deps)
  and filters items published since your last archward run. If any exist, the
  pre-flight phase raises an overridable WARN: title + link per item, so you
  can review before the update proceeds. Caches to
  `~/.local/state/archward/news_cache.json` (1-hour TTL) to avoid hammering
  the feed on repeated dry-runs. SKIPs (not FAILs) when offline or on any
  parse error. New config field `gates.skip_news_check = false` (Preferences →
  Gates) to disable for users who monitor Arch News through another channel.
  First-run fallback: treats all items from the past 30 days as unread.

- **Orphan package report — verify phase (F2).** New universal check runs
  `pacman -Qdtq` and WARNs (not FAILs) if any orphaned packages are found.
  Includes the package names in detail and a "What to do?" hint pointing at
  `pacman -Qi <pkg>` / `pacman -Rns <pkg>`. 15-second timeout; WARN on
  timeout. Some users intentionally keep orphans — WARN (not FAIL) respects
  that.

- **Arch Security Advisory verify check (F3).** New `archward.system.security_advisories`
  module fetches `security.archlinux.org/all.json` (public, no auth, ~200KB)
  and cross-references installed packages with open advisories using `vercmp`.
  Critical/High severity → FAIL; Medium/Low → WARN. SKIPs silently when the
  network is unavailable, when `arch-audit` is installed (to avoid
  double-reporting), or when `verify.security_advisories = false` (Preferences
  → Verify). 4-hour cache TTL. "What to do?" hint references
  `security.archlinux.org/`.

- **Preferences help text** for `gates.skip_news_check`,
  `verify.security_advisories`, and verify-hint keys for `orphans` and
  `security-advisories`.

### Changed

- **Async sudo warmup (F4b).** The GUI's `_warmup_sudo_for_run()` previously
  called `strategy.warmup()` on the Qt main thread, blocking the event loop
  while the KDE askpass dialog was open. The warmup now runs in a dedicated
  `WarmupWorker(QThread)` — the status bar shows "Authenticating…" and remains
  repaintable while the user types their password. Non-fatal failure path
  unchanged (pipeline proceeds with a warning).

- **Missing subprocess timeouts (F4a).** Three call sites lacked timeouts and
  could hang indefinitely on a slow/unresponsive system:
  - `pacman/query.py` `_run()` — 30s timeout; `TimeoutExpired` returns
    `(1, "", "timeout")` and logs a warning.
  - `privilege/sudo.py` `AskpassStrategy.warmup()` — 5s timeout; returns
    `False` on expiry.
  - `system/cache_policy.py` `paccache_timer_state()` — already had a 5s
    timeout; no change needed.

### Tests

477 → **534** (+57). New files:
- `test_arch_news.py` — 19 tests (feed parse, cache hit/miss/stale, unread
  filter, snapshot-since, first-run window, network error → empty)
- `test_security_advisories.py` — 20 tests (JSON parse, cache, vercmp
  matching, status filtering, verify integration: disabled/arch-audit/
  offline/PASS/FAIL/WARN)
- `test_verify_orphans.py` — 5 tests (0 orphans PASS, N orphans WARN,
  singular vs plural, timeout WARN, pacman not found)
- `test_reliability_timeouts.py` — 4 tests (query timeout fallback, sudo
  warmup timeout)

Updated:
- `test_gates_preflight.py` — hermetic autouse fixture stubs news fetch; 4
  new news-specific tests (no items PASS, unread WARN, plural, skip config)
- `test_main_window_sudo_warmup.py` — 3 new tests for WarmupWorker (success,
  failure, exception) + integration test for async start_run flow

## [0.4.4] — 2026-05-15

**Rollback-substrate awareness + production-reliability plugs.**
archward's headline promise — "a bad update is always recoverable" —
rests entirely on the pre-update `.pkg.tar.*` still living in
`/var/cache/pacman/pkg/`. Until now archward never inspected the cache
policy that governs whether it survives. A user could run archward for
months, fully believing they were protected, while a post-transaction
`paccache` hook silently deleted the rollback substrate *inside the
very `pacman -Syu` archward runs*. This release closes that hole and
three adjacent ones the production-reliability audit surfaced.

### Added

- **Cache-policy awareness + GUI control (Preferences → Cache, the
  13th tab).** New `archward.system.cache_policy` module detects the
  live policy: `paccache.timer` state, `PACCACHE_ARGS`, pacman
  `CleanMethod`, dangerous post-transaction cleaning hooks, and cache
  size/count. It computes a rollback-safety verdict —
  DANGEROUS / TIGHT / BALANCED / GENEROUS / UNMANAGED — and shows it
  as a colour-coded banner. Four one-click environment presets (Home
  `-rk3`, Workstation `-rk5 -ruk2`, Server `-rk10`, Mission-critical
  `-rk15` no-timer) apply behind a **preview-then-confirm sudo dialog**
  that shows the exact `tee /etc/conf.d/pacman-contrib` +
  `systemctl …  paccache.timer` commands before running them, through
  the same allowlisted `run_capture`/`SudoStrategy` path as rollback.
  A custom keep-N spinbox covers the in-between cases. archward
  *detects and warns* about package/third-party cleaning hooks; it
  never silently mutates someone else's hook.

- **Pre-flight cache-orphaning guard (F2).** Before the snapshot is
  even taken, pre-flight runs the cache assessment. A cleaning hook or
  a DANGEROUS verdict raises an overridable WARN — an interactive run
  gets an explicit "rollback for this update may not work, proceed?"
  prompt; auto/dry-run log it loudly and continue.

- **Post-update `rollback-cache` verify check (F2).** After the
  update, archward compares the snapshot's package versions against
  what's installed and checks the pacman cache for every changed
  package's *old* file. If a hook ate them, this is a verify **FAIL**
  (archward's job is the *recoverable* update — that did not hold),
  with the v0.4.0 "What to do?" button pointing at
  archive.archlinux.org. Honours pacman.conf `CacheDir` (relocated /
  multiple caches are scanned, not just the default), and SKIPs (never
  FALSE-FAILs) if the cache can't be read / the scan times out.

- **`boot-integrity` verify check (F3).** The classic silent killer:
  a kernel upgraded but the mkinitcpio/dracut pacman hook didn't
  regenerate the initramfs. pacman exits 0, verify is otherwise green,
  and the box fails to boot — exactly when the user can least fix it.
  archward FAILs on exactly one unambiguous signal: an
  `initramfs-<flavour>.img` older than its `vmlinuz-<flavour>` (with
  stable kernel filenames the two MUST move in lockstep). It does NOT
  check grub.cfg mtime — with stable filenames grub.cfg references a
  fixed path and legitimately predates the kernel by months on a
  perfectly bootable system, so a mtime check there is a guaranteed
  false positive. Every indeterminate case SKIPs — no flavour-named
  initramfs (dracut-kver / exotic), a Unified Kernel Image present
  (the standalone initramfs isn't authoritative then), or `/boot`
  absent. A false FAIL on a working exotic setup is worse than a
  missed check. "What to do?" surfaces the mkinitcpio *and* dracut
  regen commands.

- **Snapshot-completeness validation before rollback/verify (F4).**
  New `snapshot.validate_snapshot()`. `load_snapshot_from_disk`
  deliberately tolerates missing sections (right for *loading*) — but
  a snapshot whose `packages/all.txt` or `configs/` is gone would
  fail cryptically half-way through a restore, after pacman state was
  already touched. The CLI (`archward verify` / `archward rollback …`,
  exit 3) and the GUI Snapshot Browser (refusal dialog + a red
  "Incomplete" banner in the detail panel) now refuse up front with
  the specific missing section named. The hard-required set is
  `.timestamp` + non-empty `packages/all.txt` + `configs/`;
  `critical.txt` is intentionally **not** required (the rollback path
  reconstructs it from `all.txt` + kernel patterns, so pre-v0.2.0
  snapshots that predate critical.txt stay usable).

### Changed

- `gates.preflight_checks()` now takes `cfg` (for `allow_override`)
  in addition to the bus.
- `pacman.runner.run_capture()` grew an optional `input_text=` kwarg
  (feeds stdin) so the Cache tab can write `/etc/conf.d/pacman-contrib`
  via the allowlisted `sudo tee`. Backward compatible — every
  pre-0.4.4 caller passes nothing and behaves identically.

### Documentation

- **`docs/recovery.md`** — new "Cache policy: is rollback even
  possible?" section (the Cache tab, the verdicts, the
  archive.archlinux.org fallback when the substrate is already gone).
- **`docs/cli.md`** — notes the new pre-flight WARN, the
  `rollback-cache` / `boot-integrity` verify rows, and the exit-3
  incomplete-snapshot refusal.
- **`man/archward.1`** — documents the two new universal verify checks
  and the cache-safety pre-flight gate.
- **README** — Preferences tab list updated to 13 (Cache); GUI
  walkthrough mentions the rollback-safety verdict + presets.

### Hardening pass (config-variety audit)

A deliberate second pass after a live-box mis-fire (boot-integrity
FAILed on a healthy machine because grub.cfg legitimately predated the
kernel). Every v0.4.4 check was re-audited for the same
"assumed-one-config-is-universal" class of bug across Arch's real
spread of bootloaders, init systems, initramfs generators and pacman
layouts. Fixes folded into the entries above:

- `rollback-cache` reads pacman.conf `CacheDir` (relocated / multiple
  caches) instead of the hard-coded default, and SKIPs instead of
  mass-FALSE-FAILing when the cache can't be scanned.
- `boot-integrity` dropped the grub.cfg-mtime heuristic entirely and
  now SKIPs when a UKI is present.
- Cache verdict no longer calls `CleanMethod = KeepInstalled
  KeepCurrent` (both set — safe) DANGEROUS; only `KeepCurrent` without
  `KeepInstalled` is.
- `validate_snapshot` no longer hard-requires `critical.txt` (legacy
  snapshots stay usable).

### Tests

395 → **477** (+82). New files:
- `test_cache_policy.py` — 49 tests (keep-N parse matrix, timer-state
  branches, PACCACHE_ARGS/CleanMethod/`CacheDir` parse, the
  KeepInstalled+KeepCurrent safe-combo edge, dangerous-hook detection
  incl. the glib-compile-schemas false-positive regression, cache
  stats, the full verdict matrix, every preset's command set).
- `test_cache_tab.py` — 5 tests (verdict render, dangerous-hook
  warning, preview-then-confirm apply path with `tee`+`input_text`,
  abort-on-No, mission-critical timer-disable).
- `test_cache_safety_verify.py` — 10 tests (skip-no-list, nothing
  changed, old file present/missing, hook vs prune cause, epoch
  prefix, relocated + multiple `CacheDir`, scan-failure SKIP).
- `test_boot_integrity.py` — 9 tests (no /boot, no kernel, fresh vs
  stale initramfs, no-matching-initramfs skip, UKI-present skip,
  multi-kernel one-stale, plus the live-box regression: ancient
  grub.cfg + fresh initramfs must PASS).
- `test_snapshot_validate.py` — 11 tests (complete, every missing
  section, critical.txt-absent stays valid, multi-problem, CLI
  rollback exit-3 refusal + resolver).
- `test_gates_preflight.py` extended for the cache-safety WARN.

## [0.4.3] — 2026-05-15

**CLI parity with the GUI Snapshot Browser** plus a post-reboot verify
mode. Both address the same use case: a kernel / driver / pacnew update
that broke at next boot, and the user is stuck in tty1 without the GUI.

### Added

- **`archward verify [--snapshot ID]`** — re-runs the verify phase
  against the latest snapshot (or a specific one). No new snapshot is
  taken; no update is performed. The post-reboot diagnostic — catches
  failures that only manifest after reboot (DKMS modules that didn't
  rebuild, pacnew left unmerged, mkinitcpio hooks, systemd unit syntax
  changes). Plugins (e.g. the bundled `archward-verify-zerotier`
  example) run in this mode too — the verify view's plugin bucket
  populates exactly as in the full pipeline.

- **`archward snapshot {list,show,prune}`** — TTY-friendly snapshot
  inspection. `list` is newest-first with timestamps + age, distro,
  kernel, captured-config count (default 20 newest, `--all` for full).
  `show <id>` dumps the meta block + captured configs + critical
  packages with versions. `prune [--keep N]` is a thin wrapper around
  the existing retention helper, with a stdin Y/N confirm unless
  `--yes` is passed.

- **`archward rollback {config,package,all-configs,all-packages}`** —
  full Snapshot-Browser CLI parity for recovery operations. `config
  <id> <filename>` restores a single captured file to its /etc
  location (perm-preserving via the existing `restore_config`
  primitive). `package <id> <pkg>` downgrades a single package to its
  snapshot version from `/var/cache/pacman/pkg/`. `all-configs <id>`
  and `all-packages <id>` are bulk variants that auto-take a
  pre-rollback snapshot first (matching the GUI's v0.2.2 rollback-of-
  rollback behavior).

  Boot-critical packages (glibc, systemd, openssl, etc.) require BOTH
  `--confirm-boot-critical` AND a case-sensitive `YES` typed on stdin
  — matches the GUI's Type-YES friction gate. The existing `--yes`
  flag does NOT auto-confirm the boot-critical gate; that's an
  intentionally separate friction layer.

- **`archward pacnew {list,diff,apply}`** — manual `.pacnew`
  resolution from the CLI. `list` shows the live `.pacnew` files with
  their classified-strategy + note. `diff <path>` renders a unified
  diff (accepts either the live or the `.pacnew` path). `apply <path>
  --strategy=keep_ours|take_new|edit|leave` mirrors the GUI's per-row
  Pacnew view buttons.

- **REBOOT_NEEDED breadcrumb in the report text + desktop
  notification.** When the pipeline emits `RESULT:REBOOT_NEEDED`, the
  CLI report (and the rotating `archward.log`) now lists the post-
  reboot commands explicitly — including the "if your desktop fails
  to come back, drop to tty1" path with `archward snapshot list`,
  `archward verify`, and `archward rollback package <id> <pkg>`. The
  desktop notification has a one-line summary so it doesn't crowd
  libnotify.

### Changed

- **`cli.py` restructured to use `argparse.add_subparsers()`.**
  Backward compatible — bare `archward` (no subcommand) still runs the
  full pipeline; every existing flag (`--dry-run`, `--auto`, `--yes`,
  `--detect`, `--write-config`, `--no-aur`, `--profile`, `--version`)
  parses identically. Subcommands live alongside, not instead of, the
  flag forms.

- **New `archward.pipeline.snapshot.load_snapshot_from_disk(path)`
  helper.** Reconstructs a full `Snapshot` model from an existing
  on-disk snapshot dir (`.timestamp` + `system/os-release.txt` +
  `system/kernel-running.txt` + …). Used by the new CLI subcommands
  AND by the GUI's Snapshot Browser, which previously did the same
  parsing ad-hoc inline. Single source of truth.

### Documentation

- **`docs/recovery.md`** — task-oriented "my system broke, what do I
  type" walkthrough. Covers: desktop won't come back (the tty1 path),
  finding the responsible package from a verify FAIL, single-package
  rollback, the no-cached-package fallback (Arch Linux Archive),
  bulk rollback + rollback-of-rollback, the boot-critical YES gate,
  and the won't-boot-at-all chroot path. This is the headline doc for
  users in distress.
- **`docs/cli.md`** — exhaustive per-subcommand reference: every flag,
  exit code, side-effect, and example output.
- **`man/archward.1`** — roff man page covering the flag form + all
  subcommands. Installed to `/usr/share/man/man1/archward.1` by the
  AUR package; `docs/cli.md` + `docs/recovery.md` install to
  `/usr/share/doc/archward/`.
- The `RESULT:REBOOT_NEEDED` CLI breadcrumb now ends with a pointer to
  `man archward` / `/usr/share/doc/archward/recovery.md` for the full
  guide. README's Subcommands + Post-reboot sections link both docs.

### Internal

- New package `archward.cli_subcommands` houses the four subcommand
  modules (`verify`, `snapshot`, `rollback`, `pacnew`). Each module is
  Qt-free by design — the CLI is the recovery path when the GUI can't
  run, so no GUI imports allowed.

### Tests

329 → **395** (+66). New files:
- `test_snapshot_loader.py` — 7 tests (round-trip, missing-marker,
  partial dir, whitespace, future-timestamp, quoted os-release).
- `test_cli_dispatch.py` — 20 tests (subparser routing + every
  existing flag form's backward compatibility).
- `test_cli_verify.py` — 7 tests (latest-snapshot resolution,
  explicit `--snapshot ID`, missing-snapshot exit 3, exit-code
  mapping for VERIFY_FAILED / REBOOT_NEEDED).
- `test_cli_snapshot.py` — 10 tests (list / show / prune,
  newest-first ordering, limit + --all).
- `test_cli_rollback.py` — 13 tests (snapshot resolution,
  filename → live-target mapping, boot-critical refusal without
  flag, YES gate behavior with flag, `--yes` doesn't bypass the
  YES gate).
- `test_cli_pacnew.py` — 9 tests (list / diff / apply with both
  path forms).

## [0.4.2] — 2026-05-15

**Hotfix: sudo askpass appears at run start, not mid-snapshot.**

### Fixed

- **GUI never warmed the sudo timestamp before starting a pipeline.**
  `MainWindow` built the sudo strategy at `__init__` time but never
  called `strategy.warmup()` — that call only happened in the CLI's
  `setup_app()` path. Consequence: the first sudo call inside the
  snapshot config-gather phase (`sudo -A cp /etc/pacman.conf …`, then
  `sudo -A tar /etc/ssh/sshd_config.d`, etc.) was what triggered the
  askpass dialog. For users without NOPASSWD coverage for `cp` / `tar`
  / `chown`, this meant:
  1. The password dialog popped up *mid-snapshot* instead of upfront,
     surprising anyone who'd looked away after clicking Run Update.
  2. If ksshaskpass intermittently failed to parse the prompt — a
     known external bug, seen in archward logs as
     `ksshaskpass: Unable to parse phrase "[sudo] password for rob: "`
     followed by `sudo: no password was provided` — sudo bailed and
     archward continued to the next file, which prompted again.
     Users reported "had to enter password a few times" per run.

  Fix: `MainWindow._start_run()` now calls a new
  `_warmup_sudo_for_run()` helper before constructing the
  `PipelineWorker`. The askpass dialog appears immediately on the
  Run / Dry-Run click; subsequent sudo calls in the same run reuse
  the cached timestamp. If warmup fails, the status bar surfaces the
  failure and the pipeline runs anyway — sudo will re-prompt at the
  first call, matching pre-v0.4.2 behavior (no regression on the
  cold-cache path).

### Tests

325 → **329** (+4). New `tests/unit/test_main_window_sudo_warmup.py`:
warmup-method success / failure / exception swallowing, plus a
call-order regression guard asserting `_warmup_sudo_for_run` runs
before `PipelineWorker` is constructed.

## [0.4.1] — 2026-05-15

**Theme: audit & reliability.** Zero new features. Three parallel deep
audits (data integrity, promise verification, edge-case / error
handling) produced a prioritized fix list; this release addresses
everything CRITICAL + HIGH + the two documentation drifts they found.

### Reliability

- **F1 — Atomic `write_config()`.** The serializer now writes to
  `<path>.tmp` + `os.replace()` so a mid-write failure (disk full,
  process killed, permission change) leaves the original `config.toml`
  intact instead of truncating it. Indirectly fixes the same issue
  for `--detect` apply and the v0.3.3 auto-prune-services write-back,
  both of which funnel through `write_config()`.

- **F2 — Subprocess timeouts in the snapshot phase.** `ip addr`,
  `ss -tlnp`, `wg show`, and `df -h` previously ran without timeouts;
  a broken interface or stuck WireGuard / NFS mount could hang the
  pipeline forever. Each now has a short timeout and degrades
  gracefully on `TimeoutExpired` (the section's output gets a
  `(timed out)` marker and the snapshot continues).

- **F3 — Atomic pacnew Take-New.** When `chown` or `chmod` failed
  after the `.pacnew → original` move already succeeded, the file
  was left with the .pacnew's default perms (typically 644 root:root).
  For files like sshd_config (mode 600) this was a silent permission
  downgrade — a security regression. The apply now recovers from the
  `.pre-archward.bak` on partial failure and surfaces both errors.

- **F4 — Per-plugin verify timeout.** Plugins now run in a daemon
  thread with a 30 s join timeout (`PLUGIN_TIMEOUT_S`). A hanging
  plugin produces a synthetic `FAIL` row instead of freezing verify.
  Mirrors the existing exception-isolation pattern; the daemon thread
  doesn't block interpreter exit.

- **F5 — `systemctl is-active` / `systemctl cat` timeouts** in
  `archward.system.services`. A hung systemd manager used to freeze
  the verify phase; both wrappers now return safe defaults on
  timeout (`is_active` → False, `unit_exists` → True).

- **F6 — Reboot-log fs probe timeout.** `Path(cfg.verify.reboot_log)
  .exists()` / `.stat()` on an offline NFS mount could hang verify
  forever. New `_call_with_timeout` helper wraps both calls; on
  timeout we emit a `WARN` row instead of blocking.

- **F7 — Hook timeout kills the process group.** Pre-fix, the hook
  timeout killed only the shell parent; a hook that did `sleep 99 &`
  orphaned the background sleep. Now uses `Popen(preexec_fn=os.setsid)`
  + `os.killpg()` so the whole process group goes down.

- **F8 — Snapshot partial-failure cleanup.** If any gather step
  raised, the half-populated snapshot dir was left on disk forever
  (retention can't prune what has no `.timestamp` marker). Snapshot
  is now all-or-nothing: any exception triggers `shutil.rmtree()` of
  the partial dir before re-raising.

- **F9 — `$ARCHWARD_RESULT` env var for `post_verify` hooks.**
  Documented in `docs/hooks.md` since v0.3.1 but never actually set.
  The Discord-webhook hook template (v0.4.0 F4) actually uses
  `$ARCHWARD_RESULT` — so the prebaked snippet was broken on a
  fresh install. The pipeline now computes the RESULT tag *before*
  the post_verify phase and threads it through to the hook env.

- **F10 — PKGBUILD prefetch size limit.** `fetch_pkgbuild()` now
  refuses any PKGBUILD larger than 512 KiB (returning `None`, which
  the modal handles as a fetch failure). A malicious PKGBUILD with
  an embedded multi-MB blob could otherwise OOM archward via
  `read_text()`.

- **F11 — Askpass-misconfigured diagnostic.** When the user sets
  `privilege.askpass = /bad/path`, `discover_askpass()` now logs a
  clear warning and falls back to the auto-detection chain (instead
  of silently returning `None` and letting sudo block on a TTY the
  GUI doesn't have).

### Documentation drift

- **F12 —** Removed the claim that pacnew rules can be "reordered"
  from the v0.4.0 CHANGELOG entry. The editable table supports
  add / edit / remove but not reordering. (Up/Down buttons are a
  v0.5+ candidate; for now the docs match the shipped product.)

- **F13 — Stale-lock recovery hint.** The pacman-db-lock FAIL
  detail now differentiates stale vs live lock and tells the user
  the exact recovery command for the stale case
  (`sudo rm /var/lib/pacman/db.lck`). Live-lock case still asks
  them to wait.

### Tests

288 → **325** (+37). New: `test_snapshot_timeouts`,
`test_pacnew_apply`, `test_services_timeout`, `test_reboot_log_timeout`,
`test_snapshot_cleanup`, `test_sudo`, `test_gates_preflight`. Extended:
`test_config_loader` (atomic write), `test_verify_plugins`
(per-plugin timeout), `test_hooks` (process-group kill + ARCHWARD_RESULT
env), `test_prefetch` (size limit).

### Internal

- New helper `archward.pipeline.verify_phase._call_with_timeout(fn,
  timeout_s)`: runs `fn()` on a daemon thread, returns its value or
  raises `TimeoutError`. Used by F4 (plugin timeout) and F6 (reboot-
  log stat timeout). Daemon thread guarantees interpreter exit isn't
  blocked by hung callables.

## [0.4.0] — 2026-05-15

**Theme: keep users in archward.** Six features close the GUI's biggest
"escape paths" — places where the existing workflow forced users to drop
to a terminal or hand-edit `config.toml` (and in doing so sidestep
archward's snapshot/gate/verify safety net).

### Added

- **F1 — GUI-editable pacnew rules.** The Preferences → Pacnew tab is no
  longer read-only; rules can be added, edited, and removed
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
