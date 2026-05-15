# CLAUDE.md — archward project context

You've been launched in `~/dev/archward/`, a greenfield Python/PySide6 project. The canonical implementation plan is in [`PLAN.md`](./PLAN.md) — **read it before doing anything else**. This file gives you the operational context needed to start work.

## What is archward?

A safe-update GUI for Arch-based Linux distributions (Arch, EndeavourOS, Manjaro, CachyOS, Garuda, Artix). It:

1. Snapshots system state (packages, configs, services)
2. Gates the update (snapshot fresh? disk space OK?)
3. Classifies pending updates HIGH/MEDIUM/LOW risk
4. Runs `sudo pacman -Syu` (streaming output)
5. Runs an auto-detected AUR helper (yay/paru/aurutils), gracefully skipping if none
6. Detects and resolves new `.pacnew` files
7. Verifies the system afterward (kernel, services, disk, pacnew, pacman log)
8. Reports `RESULT:` tags compatible with the existing bash script harness

Target user: any Arch-based-distro user who wants snapshot-backed, gated updates. Ships as an AUR package (PKGBUILD in `packaging/`).

## How this project came to be

The maintainer ran a bash-based "safe system update pipeline" on his desktop for months: `pre-update-snapshot.sh`, `system-update.sh`, `post-update-verify.sh`. Those scripts work well but were full of host-specific hardcoding (custom VPN IPs, self-hosted media-server probes, machine-local backup paths). archward is the **general-use rewrite** — same workflow shape, machine-neutral, GUI-fronted.

The bash scripts are the **reference implementation for behavior** — read them to understand *what* the gates/risk-classification/pacnew-strategy/verify checks should do. archward is a clean rewrite (not a port); take the *concepts* and reimplement them properly in Python.

## Read first (in this order)

1. [`PLAN.md`](./PLAN.md) — the canonical spec. Project structure, data model, TOML schema, pipeline phases, GUI design, packaging, test plan, implementation order.
2. `/home/rob/bin/system-update.sh` — gate logic, risk classification, .pacnew strategy table (maintainer's local reference; not part of the public repo)
3. `/home/rob/bin/pre-update-snapshot.sh` — what state to capture
4. `/home/rob/bin/post-update-verify.sh` — *separate the universal checks from host-specific ones*. v1 only implements the universal ones.
5. `/home/rob/dev/liberty-books/main.py` and `/home/rob/dev/liberty-books/ui/` — sibling PySide6 project; mirror these conventions where they apply.
6. `/home/rob/dev/endeavoring-conky/README.md` and `/home/rob/dev/endeavoring-conky/LICENSE` — sibling public-repo conventions (GPL-3.0, README structure, AUR-friendly layout).

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
| v1 verify scope | **Universal checks + opt-in services list ONLY.** No network probes, no HTTP health, no port-listen checks, no mountpoint checks — those are host-specific and shipped as `[hooks]` (v0.3.1+). |

## v2 reservations — leave seams, do not implement

Per PLAN.md §11. All originally-reserved seams have now shipped.

Originally reserved, now shipped (do not reintroduce as TODOs):

- **`[hooks]` (v0.3.1)** — `HookRunner` runs pre-update + post-verify shell hooks with `HookResult` capture, GUI rail rows, and a Verify-view bucket. See `docs/hooks.md`.
- **Profiles (v0.3.2)** — `--profile NAME` on both CLI and GUI, plus in-window Profiles tab in Preferences for list/switch/new/rename/delete.
- **Custom verify probes (v0.3.3)** — `archward.verify_checks` entry-point group; third-party plugins contribute checks that land in a `plugin` bucket. See `docs/plugins.md`.
- **Auto-prune stale services on `--detect` (v0.3.3)** — `--detect` now proposes removals (opt-in) for `services.to_verify` entries whose unit files no longer resolve. CLI and Preferences both surface the prompt.
- **Preferences inline help (v0.1.2)** — every schema tab has italic help labels under each field.

## Implementation order (from PLAN.md §13)

Start at **Phase 1: CLI core, no GUI/AUR/config**. Build a CLI tool that runs the existing bash pipeline behavior end-to-end on Rob's machine. Then layer config, AUR, and GUI in subsequent phases. Each phase is independently demoable.

1. CLI core (skeleton, models, sudo, pacman query/runner, snapshot, gates, hard-coded risk, official update, universal verify, RESULT tags)
2. Config + auto-detect
3. AUR phase (yay first, then paru, then aurutils)
4. Minimal GUI shell (main_window, phase_rail, log_pane, qt_bus)
5. Phase views (snapshot/gates simplest; risk/pacnew complex)
6. Preferences dialog
7. Packaging polish (desktop file, icon, README, PKGBUILD)
8. v2 backlog (not v1)

## Rob's preferences — strict rules

These are from `~/.claude/CLAUDE.md` and project memories. **Follow these without being reminded.**

### Pull requests / commits
- **NEVER commit or push to any repo without being explicitly asked.** This applies to all repos including this one.
- **NEVER open a non-draft PR/MR while work is actively in progress.** Use `gh pr create --draft` until the branch is stable, tested, and review-ready.

### Communication style
- **No trailing summaries.** Don't end a response with "I just did X, Y, Z" — the diff speaks for itself.
- Short, direct responses. Match the task: simple question → direct answer, not headers and sections.
- Only use emojis if explicitly requested.

### Sudo / privilege (CRITICAL — see below)
- **NEVER run sudo blind from a Bash tool call.** Check sudo timestamp first with `sudo -n true`. If expired, do NOT try password-based sudo — it will fail (Bash tool is non-TTY) and increment PAM faillock. **5 failures = Rob locked out of sudo.** This already happened today.
- **Use askpass instead.** `SUDO_ASKPASS=/usr/bin/ksshaskpass sudo -A <cmd>` pops a KDE password dialog Rob can answer. Within a single Bash tool invocation, the sudo timestamp persists across multiple calls.
- **Sudo timestamp does NOT persist across Bash tool calls** because each call is a fresh non-TTY shell. To prime sudo for the whole tool run, set `SUDO_ASKPASS` and call `sudo -A -v` at the start.
- The user has a `/etc/sudoers.d/claude-tasks` NOPASSWD entry for a specific allowlist (`tee, systemctl, chmod, pacman, ufw, sysctl, augenrules, grub-mkconfig, mkdir, wg, wg-quick, ln, resolvectl, cp, rm, ip, vpn-full, vpn-split, ls, udevadm`). Anything outside that list needs askpass.
- **NEVER run VPN toggle commands** (`vpn-full`, `vpn-split`, `wg-quick switches`). They change the source IP mid-stream and hang Claude's own API connection.

### Files / sudo writes
- Use `sudo tee` (not `sudo echo >`) for privileged file writes — redirection happens before sudo elevates.

### Tasks / planning
- Use TaskCreate/TaskUpdate for multi-step work. Mark in-progress when starting; completed when done.
- For non-trivial features, use Plan mode to align before coding.

## Reference / memory pointers

Rob has an extensive memory system at `/home/rob/.claude/projects/-home-rob/memory/`. **Read `MEMORY.md` there first** for the index. Memories particularly relevant to this project:

- `project_system_update_pipeline.md` — the bash pipeline this project generalizes (gates, HIGH RISK list, .pacnew strategies, RESULT tag workflow). Baseline of what archward must replicate.
- `project_liberty_books.md` — Rob's other PySide6 project; reference for repo layout, requirements.txt patterns, virtualenv conventions.
- `project_endeavoring_conky.md` — Rob's other public AUR-aspiring repo; reference for GitHub conventions, config-file gitignore pattern, AUR-style README.
- `feedback_sudo_faillock.md` — the critical "don't trigger sudo blind" rule above, with the painful prior incident.
- `feedback_no_auto_commit.md` — the never-commit-without-asking rule.
- `feedback_draft_prs.md` — the draft-PR rule.
- `feedback_sudo_tee_pattern.md` — `sudo tee` not `sudo echo >`.
- `feedback_vpn_toggle.md` — don't run VPN toggles.

## Repository setup — when ready

When Rob is ready to make this a git repo (only when explicitly asked):

```bash
cd ~/dev/archward
git init
git add .
git commit -m "Initial commit: project structure and plan"
# DO NOT push or create remote without explicit user request
```

The eventual remote will be `git@github.com:indyfive11/archward.git` (matches the `indyfive11` GitHub handle Rob uses for `endeavoring-conky` and `liberty-books`).

## AUR package — submission state and release workflow

archward ships as `archward` on the AUR (first published v0.3.2, 2026-05-14).
**Page:** https://aur.archlinux.org/packages/archward
**Maintainer:** `indyfive11`

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

- The plan is canonical. If something in this CLAUDE.md contradicts PLAN.md, PLAN.md wins.
- If a design question arises that isn't covered, ask Rob — don't guess.
- If you need to deviate from the plan (e.g., a referenced library doesn't exist), surface the deviation explicitly before committing to it.
