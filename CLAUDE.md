# CLAUDE.md — archward project context

You've been launched in `~/dev/archward/`, a **shipped and maintained** Python/PySide6 project. v0.4.5 just shipped (2026-05-15) — the package is live on the AUR as `archward`. This file gives you the operational context for further maintenance / bug fixes / v0.5+ work.

**Read first if you're new to the project:** [`CHANGELOG.md`](./CHANGELOG.md) for what's shipped, [`README.md`](./README.md) for user-facing surface, [`PLAN.md`](./PLAN.md) for historical design rationale (v1 is shipped — PLAN is reference, not a TODO).

## What is archward?

A safe-update GUI for Arch-based Linux distributions (Arch, EndeavourOS, Manjaro, CachyOS, Garuda, Artix). Pipeline order (as of v0.4.4):

1. Pre-flight (`db.lck` + single-instance lock + cache-safety WARN, v0.4.4 F2)
2. Snapshot (packages, configs, services, network state, pacnew baseline)
3. Gates (snapshot freshness, disk)
4. Risk classification (HIGH/MEDIUM/LOW + transaction preview)
5. Pre-update hooks (`[hooks].pre_update`)
6. Official update (`sudo pacman -Syu`)
7. AUR phase (auto-detected helper)
8. Pacnew resolution
9. Verify (universal incl. `rollback-cache` + `boot-integrity` (v0.4.4) + opt-in services + `[hooks]` + plugin probes)
10. Post-verify hooks (`[hooks].post_verify`)
11. Report (RESULT tag + desktop notification)

Target user: any Arch-based-distro user who wants snapshot-backed, gated updates. Ships as an AUR package; `yay -S archward` installs the latest release.

## How this project came to be

The maintainer ran a bash-based "safe system update pipeline" on his desktop for months: `pre-update-snapshot.sh`, `system-update.sh`, `post-update-verify.sh`. Those scripts work well but were full of host-specific hardcoding (custom VPN IPs, self-hosted media-server probes, machine-local backup paths). archward is the **general-use rewrite** — same workflow shape, machine-neutral, GUI-fronted.

The bash scripts are the **reference implementation for behavior** — read them to understand *what* the gates/risk-classification/pacnew-strategy/verify checks should do. archward is a clean rewrite (not a port); take the *concepts* and reimplement them properly in Python.

## Read first (in this order)

1. [`CHANGELOG.md`](./CHANGELOG.md) — what shipped in each release. Most recent entry tells you the current state.
2. [`README.md`](./README.md) — user-facing scope, install, GUI walkthrough.
3. [`docs/development.md`](./docs/development.md) — local-dev setup, test commands, pre-tag checklist, optional NOPASSWD sudoers fragment.
4. [`docs/hooks.md`](./docs/hooks.md) — `[hooks]` user guide (v0.3.1+).
5. [`docs/plugins.md`](./docs/plugins.md) — `archward.verify_checks` entry-point plugin author guide (v0.3.3+).
6. [`PLAN.md`](./PLAN.md) — historical design spec from before v0.1.0. v1 is shipped; treat PLAN as reference, not a TODO.
7. `~/bin/system-update.sh`, `~/bin/pre-update-snapshot.sh`, `~/bin/post-update-verify.sh` — maintainer's local bash pipeline archward generalizes. Reference for behavior on edge cases.
8. `~/dev/liberty-books/` and `~/dev/endeavoring-conky/` — sibling PySide6 / AUR projects for convention comparisons.

## Locked decisions (do not relitigate)

| Decision | Locked value |
|---|---|
| Project name | `archward` (verified free on AUR) |
| Language | Python 3.11+ |
| GUI toolkit | PySide6 (Qt6) |
| UI form factor | Native desktop GUI (single QMainWindow, phase rail + log pane). **Not a TUI, not web.** |
| AUR integration | Integrated 2nd phase after pacman. Auto-detect helper preference order: `yay > paru > aurutils`. Skip gracefully if none. `--no-aur` flag opts out. |
| Configuration | Single TOML at `~/.config/archward/config.toml`. Auto-detect on first run. |
| License | GPL-3.0-or-later |
| Repo | `git@github.com:indyfive11/archward.git` (public) — **do not create or push without explicit user request** |
| AUR namespace | `archward` — registered, maintainer `indyfive11`. Published from `~/dev/archward-aur/` (PKGBUILD + .SRCINFO only; the rest is pulled from the GitHub release tarball at build time). |
| Build backend | hatchling |
| Verify scope | Universal checks (kernel, .pacnew, disk, pacman log) + opt-in `systemctl is-active` services + `[hooks]` for shell-command checks (v0.3.1+) + plugin probes via `archward.verify_checks` entry points for Python checks (v0.3.3+). Host-specific concerns (network probes, HTTP health, mountpoint, etc.) belong in hooks or plugins, not in archward core. |

## Shipped feature surface (do not reintroduce as TODOs)

Per PLAN.md §11. Every originally-reserved v2 seam plus every post-release polish item has shipped.

- **`[hooks]` (v0.3.1)** — `HookRunner` runs pre-update + post-verify shell hooks with `HookResult` capture, GUI rail rows, and a Verify-view bucket. See `docs/hooks.md`.
- **Profiles, CLI + GUI (v0.3.2)** — `--profile NAME` on both front-ends, in-window Profiles tab in Preferences for list/switch/new/rename/delete. setup_logging bugfix bundled.
- **Custom verify probes (v0.3.3)** — `archward.verify_checks` entry-point group; third-party plugins contribute checks that land in a `plugin` bucket. See `docs/plugins.md`.
- **Stale-service detection, three surfaces (v0.3.3)** — verify-phase WARN row distinguishes "no such unit" from "not active"; `archward --detect` proposes opt-in removals; `services.auto_prune` config flag enables inline auto-prune with persistent write-back.
- **Remember-last-used profile (v0.3.4)** — opt-in QSettings toggle in Preferences → Profiles; backed by `archward.ui.persistent_state`. State lives in `~/.config/archward/archward.conf`, separate from any profile's TOML.
- **Profiles tab management (v0.3.5)** — "Diff vs default" modal, "Import…" and "Export…" buttons. Diff helper at `archward.config.diff.unified_diff()`.
- **GUI-editable pacnew rules (v0.4.0)** — `_PacnewTab` is now an editable `QTableWidget` (Pattern / Strategy combo / Note) with Add / Remove / Restore-defaults. Help text no longer says "edit by hand in config.toml."
- **In-GUI pacman/AUR interactive prompts (v0.4.0)** — when `pacman.noconfirm=False`, archward routes pacman/yay/paru through a PTY, detects `[Y/n]` / provider-selection prompts via `archward.pacman.prompts.PROMPT_PATTERNS`, and surfaces them in an inline input row at the bottom of `UpdateView`. New `UpdatePrompter` mirrors the proven `GuiPrompter` cross-thread pattern.
- **PKGBUILD review modal (v0.4.0)** — when `noconfirm=False`, the AUR phase pre-fetches each pending PKGBUILD (`git clone --depth=1` of `aur.archlinux.org/<pkg>.git`) and shows a per-package modal with Approve / Reject / Cancel-review. Rejected packages → `--ignore`. See `archward/aur/prefetch.py` + `archward/ui/dialogs/pkgbuild_review.py`.
- **Hook templates (v0.4.0)** — `_HooksTab` gets an "Insert template…" combobox per editor. 4 prebaked snippets in `archward/ui/dialogs/hook_templates.py` (btrfs snapshot, stale-backup gate, Discord webhook, user-services restart). Append-on-select with `# template: <name>` header.
- **Verify failure remediation hints (v0.4.0)** — Verify view's 4th column shows a "What to do?" button on FAIL rows with a registered hint. Hints live in `help_text.HELP` under the `verify_hint` section.
- **Snapshot retention (v0.4.0)** — the `keep_snapshots` setting (GUI-exposed but no-op since v0.1.0) now actually runs at end-of-pipeline. Snapshot browser also gets a "Prune now…" button. Logic in `archward/pipeline/retention.py`.
- **Preferences inline help (v0.1.2 + ongoing)** — every schema tab has italic help labels under each field, with `_section_help()` intros on the more involved tabs.
- **CLI subcommands + recovery docs (v0.4.3)** — `archward.cli_subcommands` package (Qt-free): `verify`, `snapshot {list,show,prune}`, `rollback {config,package,all-configs,all-packages}`, `pacnew {list,diff,apply}`. `snapshot.load_snapshot_from_disk()` is the single snapshot-reconstruction source for CLI + GUI. `docs/recovery.md`, `docs/cli.md`, `man/archward.1` (installed by the AUR package).
- **Cache-policy awareness (v0.4.4)** — `archward.system.cache_policy` (pure-Python, Qt-free): detects paccache timer/args, pacman `CleanMethod`, dangerous post-transaction cleaning hooks, cache size; computes a `RollbackSafety` verdict (DANGEROUS/TIGHT/BALANCED/GENEROUS/UNMANAGED) + 4 environment presets. GUI is the 13th Preferences tab `_CacheTab` — verdict banner + preview-then-confirm sudo apply via the allowlisted `tee`/`systemctl` path (`run_capture` grew an `input_text=` kwarg for the `sudo tee`). The rollback substrate every downgrade depends on, finally inspected.
- **Production-reliability plugs (v0.4.4)** — F2: `gates.preflight_checks(cfg,bus)` raises an overridable cache-safety WARN; `verify_phase._cache_safety_check` is a `rollback-cache` universal FAIL when a hook/prune ate the just-updated packages' pre-update files (honours pacman.conf `CacheDir` incl. relocated/multiple; SKIPs not FAILs if the cache can't be scanned). F3: `verify_phase._boot_integrity_check` (`boot-integrity`) FAILs ONLY on initramfs-older-than-its-kernel (deliberately does NOT check grub.cfg mtime — stable kernel filenames mean grub.cfg legitimately predates the kernel; that heuristic was a guaranteed false positive, removed after a live-box mis-fire), SKIPs when no flavour-named initramfs / UKI present / no /boot. F4: `snapshot.validate_snapshot()` — CLI (exit 3) + GUI Snapshot Browser refuse an incomplete snapshot up front, naming the missing section; hard set is `.timestamp` + non-empty `all.txt` + `configs/` (NOT `critical.txt` — reconstructable, legacy snapshots stay usable). Verify-hint keys `rollback_cache` + `boot_integrity` wire the v0.4.0 "What to do?" button.
- **Awareness: Arch News + orphans + security advisories + reliability (v0.4.5)** — F1: `archward.system.arch_news` fetches `archlinux.org/feeds/news/` (Atom, stdlib only) and surfaces unread items in pre-flight WARN (1h cache, `~/.local/state/archward/news_cache.json`, `gates.skip_news_check` config). F2: `verify_phase._orphan_check()` runs `pacman -Qdtq` (WARN, 15s timeout). F3: `archward.system.security_advisories` fetches `security.archlinux.org/all.json` and cross-references installed packages via `pq.vercmp` — Critical/High FAIL, Medium/Low WARN, SKIPs if `arch-audit` present or offline (`verify.security_advisories` config, 4h cache). F4a: `pacman/query.py _run()` gets 30s timeout; `privilege/sudo.py warmup()` gets 5s timeout (both `TimeoutExpired` → safe fallback). F4b: `WarmupWorker(QThread)` moves `strategy.warmup()` off the Qt main thread — status bar shows "Authenticating…" and stays repaintable while the askpass dialog is open.

## Implementation history (PLAN.md §13 — completed)

The v1 phases below all shipped in v0.1.0–v0.1.4; the v2 backlog shipped in v0.3.x. Listed for archaeological context only — don't treat any item here as open work.

1. ✅ CLI core (skeleton, models, sudo, pacman query/runner, snapshot, gates, risk, official update, universal verify, RESULT tags)
2. ✅ Config + auto-detect
3. ✅ AUR phase (yay → paru → aurutils preference)
4. ✅ GUI shell (main_window, phase_rail, log_pane, qt_bus)
5. ✅ Phase views (snapshot, gates, risk, update, pacnew, verify)
6. ✅ Preferences dialog (12 tabs with inline help)
7. ✅ Packaging (desktop file, icon, README, PKGBUILD, AUR submission)
8. ✅ v2 backlog — see "Shipped feature surface" above

## Maintainer preferences — strict rules

These are from `~/.claude/CLAUDE.md` and project memories. **Follow these without being reminded.**

### Pull requests / commits
- **NEVER commit or push to any repo without being explicitly asked.** This applies to all repos including this one.
- **NEVER open a non-draft PR/MR while work is actively in progress.** Use `gh pr create --draft` until the branch is stable, tested, and review-ready.

### Communication style
- **No trailing summaries.** Don't end a response with "I just did X, Y, Z" — the diff speaks for itself.
- Short, direct responses. Match the task: simple question → direct answer, not headers and sections.
- Only use emojis if explicitly requested.

### Sudo / privilege (CRITICAL — see below)
- **NEVER run sudo blind from a Bash tool call.** Check sudo timestamp first with `sudo -n true`. If expired, do NOT try password-based sudo — it will fail (Bash tool is non-TTY) and increment PAM faillock. **5 failures = maintainer locked out of sudo.**
- **Use askpass instead.** `SUDO_ASKPASS=/usr/bin/ksshaskpass sudo -A <cmd>` pops a KDE password dialog the maintainer can answer. Within a single Bash tool invocation, the sudo timestamp persists across multiple calls.
- **Sudo timestamp does NOT persist across Bash tool calls** because each call is a fresh non-TTY shell. To prime sudo for the whole tool run, set `SUDO_ASKPASS` and call `sudo -A -v` at the start.
- The user has a `/etc/sudoers.d/claude-tasks` NOPASSWD entry for a specific allowlist (`tee, systemctl, chmod, pacman, ufw, sysctl, augenrules, grub-mkconfig, mkdir, wg, wg-quick, ln, resolvectl, cp, rm, ip, vpn-full, vpn-split, ls, udevadm`). Anything outside that list needs askpass.
- **NEVER run VPN toggle commands** (`vpn-full`, `vpn-split`, `wg-quick switches`). They change the source IP mid-stream and hang Claude's own API connection.

### Files / sudo writes
- Use `sudo tee` (not `sudo echo >`) for privileged file writes — redirection happens before sudo elevates.

### Tasks / planning
- Use TaskCreate/TaskUpdate for multi-step work. Mark in-progress when starting; completed when done.
- For non-trivial features, use Plan mode to align before coding.

## Reference / memory pointers

The maintainer has an extensive memory system at `/home/rob/.claude/projects/-home-rob/memory/`. **Read `MEMORY.md` there first** for the index. Memories particularly relevant to this project:

- `project_system_update_pipeline.md` — the bash pipeline this project generalizes (gates, HIGH RISK list, .pacnew strategies, RESULT tag workflow). Baseline of what archward must replicate.
- `project_liberty_books.md` — maintainer's other PySide6 project; reference for repo layout, requirements.txt patterns, virtualenv conventions.
- `project_endeavoring_conky.md` — maintainer's other public AUR-aspiring repo; reference for GitHub conventions, config-file gitignore pattern, AUR-style README.
- `feedback_sudo_faillock.md` — the critical "don't trigger sudo blind" rule above, with the painful prior incident.
- `feedback_no_auto_commit.md` — the never-commit-without-asking rule.
- `feedback_draft_prs.md` — the draft-PR rule.
- `feedback_sudo_tee_pattern.md` — `sudo tee` not `sudo echo >`.
- `feedback_vpn_toggle.md` — don't run VPN toggles.

## AUR package — release workflow

archward ships as `archward` on the AUR (live since v0.3.2, currently at v0.4.4 as of 2026-05-15).
**Page:** https://aur.archlinux.org/packages/archward
**Maintainer:** `indyfive11`
**Installable:** `yay -S archward`

### Local layout
- **AUR git clone:** `~/dev/archward-aur/` (separate working tree; contains
  only PKGBUILD + .SRCINFO. Everything else is pulled from the GitHub
  release tarball at `makepkg` time.)
- **Canonical PKGBUILD + .SRCINFO live in this repo at `packaging/`** —
  the AUR clone is a copy destination, not the source of truth. Always
  edit `packaging/PKGBUILD`, regenerate `.SRCINFO` there, then sync to
  `~/dev/archward-aur/` for the push.

### SSH access
- **Dedicated key:** `~/.ssh/aur` (ed25519), public half pasted into AUR
  account. Not the same as `id_ed25519`.
- **`~/.ssh/config` Host block** for `aur.archlinux.org` pins
  `IdentityFile ~/.ssh/aur`, `Port 22`, `IdentitiesOnly yes` (the last
  matters because the global ssh_config sets Port 1111 and the agent may
  hold multiple keys).
- Test with: `ssh aur.archlinux.org help` → expect the AUR command list,
  then disconnect. `Permission denied (publickey)` means the pubkey isn't
  on the account.

### Per-release submission workflow

For each tagged release on GitHub (e.g. v0.3.3):

```bash
# 1. Bump pkgver + replace SKIP with the real sha256 of the GitHub tarball.
cd ~/dev/archward/packaging
curl -sL -o /tmp/archward-vX.Y.Z.tar.gz \
    "https://github.com/indyfive11/archward/archive/vX.Y.Z.tar.gz"
sha256sum /tmp/archward-vX.Y.Z.tar.gz
#   → paste hash into PKGBUILD's sha256sums=(...) and bump pkgver=...

# 2. Regenerate .SRCINFO (required by AUR; rejected without it).
makepkg --printsrcinfo > .SRCINFO

# 3. Smoke-test the recipe locally (build only — runtime deps already installed).
makepkg -f --nodeps
#   → expect: archward-X.Y.Z-1-any.pkg.tar.zst built; install paths sane.
rm -rf src pkg archward-* *.pkg.tar.zst   # cleanup

# 4. Sync to the AUR clone, commit, push.
cp PKGBUILD .SRCINFO ~/dev/archward-aur/
cd ~/dev/archward-aur
git add PKGBUILD .SRCINFO
git commit -m "archward X.Y.Z"
git push origin master   # AUR uses 'master', not 'main'
```

### Keywords (search discoverability)
Set with `ssh aur.archlinux.org set-keywords archward <space-separated list>`.
Currently: `gui pacman safe-update snapshot update`. To change, re-run
the command with the full replacement list (set is destructive, not
additive).

### Build-deps note
Building the PKGBUILD locally needs `python-build python-installer
python-hatchling` (makedepends) and `pyside6 python-tomli-w`
(rundeps, for `makepkg -s` to pass). These are pacman-installable from
extra/. Skip the runtime-dep check with `makepkg -f --nodeps` if you
just want to verify the recipe builds without installing pyside6
system-wide.

## Test machine context

If dogfooding archward on the maintainer's primary desktop:
- Distro: EndeavourOS, kernel `linux-cachyos-bore`
- DE: KDE Plasma 6 on Wayland
- AUR helper: `yay`
- Askpass: `ksshaskpass` (KDE-native)
- Local bash pipeline scripts at `~/bin/` are the comparison baseline — archward's `RESULT:` tags should match the bash output for the same machine state.
- Existing snapshots at `~/update-snapshots/` are from the bash pipeline; archward uses `~/.local/state/archward/snapshots/` (different path, no conflict).

For testing on other distros: spin up a VM or container. PLAN.md §14 has the full manual test matrix.

## When in doubt

- **CHANGELOG.md is the canonical record of shipped state.** PLAN.md is historical — if it says something is "v2 reserved" but CHANGELOG shows it shipped in v0.3.x, CHANGELOG wins.
- If a design question arises that isn't covered, ask the maintainer — don't guess.
- Don't reintroduce items already shipped as TODOs. Cross-check "Shipped feature surface" above before proposing work that sounds like it might already exist.
- Non-trivial feature work uses Plan mode before coding; small/contained changes can dive straight in.
