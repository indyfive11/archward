# archward

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

A safe-update tool for Arch-based Linux distributions (Arch, EndeavourOS,
Manjaro, CachyOS, Garuda, Artix — and anything with `arch` in `ID_LIKE`).

archward wraps `pacman -Syu` with the workflow you actually want around
system updates. Ships as both a CLI tool (`archward`) and a PySide6 GUI
(`archward-gui`).

## Pipeline

1. **Pre-flight** — refuse to run if another pacman holds `/var/lib/pacman/db.lck`,
   or if another archward is already running (advisory `flock`).
2. **Snapshot** — capture explicit + foreign packages, pacman config, mirror
   list, fstab, grub defaults, sshd_config(.d), resolved.conf, sudoers.d/,
   network state (`ip addr`, `ss -tlnp`, `wg show`), services, kernel +
   cmdline, pacnew baseline → `~/.local/state/archward/snapshots/<id>/`.
3. **Gates** — snapshot freshness, free disk on `/`.
4. **Risk** — classify pending updates HIGH (`glibc`, `systemd`, `openssl`,
   `openssh`, `mesa`, `pipewire`, …, kernel + headers via fnmatch),
   MEDIUM (`*-server`, `docker*`, `nginx*`, …), LOW. Surfaces package
   replacements and conflict warnings that `--noconfirm` would silently
   default through (querying the synced checkupdates DB so the preview is
   meaningful even without `pacman -Sy`).
5. **Pre-update hooks** — run user-defined shell commands from
   `cfg.hooks.pre_update` (e.g. external-backup freshness gate,
   maintenance-window blackout). Non-zero exit can optionally abort the
   pipeline via `fail_pipeline_on_error`.
6. **Official update** — `sudo pacman -Syu --noconfirm --noprogressbar
   --color=never`, line-buffered + ANSI-stripped streaming. The user
   can deselect specific packages at approval time → flow through as
   `--ignore` flags.
7. **AUR** — auto-detect `yay` → `paru` → `aurutils`, run helper update,
   capture build failures (last 50 lines per failed package). Skipped
   gracefully if no helper is on PATH.
8. **Pacnew** — find new `.pacnew` files since snapshot, classify per a
   rule table (sshd_config / mirrorlist / pacman.conf / fstab / grub /
   resolved.conf / faillock.conf / sysctl.d/* / *.hook); apply preserves
   original ownership and permissions.
9. **Verify** — kernel match, .pacnew remaining, disk, pacman.log scan,
   opt-in `systemctl is-active` services list, optional
   reboot-recommended log.
10. **Post-verify hooks** — run user-defined shell commands from
    `cfg.hooks.post_verify` (e.g. HTTP health probes, mountpoint checks,
    real-time reachability). Always best-effort; never abort.
11. **Report** — emit `RESULT:` tag (`SUCCESS` / `NEEDS_REVIEW` /
    `REBOOT_NEEDED` / `PACNEW_MERGE_NEEDED` / `VERIFY_FAILED` /
    `UPDATE_FAILED`) for scripted use; secondary tags annotate the primary.
    Desktop notification fires via `notify-send` (opt-out).

## Install

### AUR (recommended once published)

```bash
yay -S archward
```

### From source

```bash
git clone git@github.com:indyfive11/archward.git ~/dev/archward
cd ~/dev/archward
python3 -m venv venv
source venv/bin/activate
pip install -e ".[gui]"   # drop [gui] for CLI-only

archward --dry-run        # snapshot + gates + risk + AUR pending (no update)
archward                  # interactive update; prompts on HIGH-risk
archward-gui              # PySide6 GUI
```

## Requirements

- Arch-based Linux distribution
- Python 3.11+
- `pacman`, `pacman-contrib` (for `checkupdates`)
- An askpass binary for unattended sudo
  (`ksshaskpass` / `lxqt-openssh-askpass` / `x11-ssh-askpass`) **or** a
  NOPASSWD sudoers entry for `pacman` and friends
- PySide6 6.6+ for the GUI (optional)

## CLI flags

```
archward [flags]

  --dry-run         Snapshot + gates + risk classification (incl. AUR pending),
                    then exit. No update is performed.
  --auto            Hands-off: abort if HIGH RISK packages are present.
  --yes             Auto-confirm all prompts (HIGH-risk + gate overrides).
  --no-aur          Skip the AUR phase regardless of config.
  --profile NAME    Use ~/.config/archward/profiles/<NAME>.toml instead of the
                    default config. Bootstraps the file with defaults on first
                    use. NAME must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}.
  --detect          Run distro / kernel / AUR-helper / service detection and
                    propose a diff against ~/.config/archward/config.toml
                    (or the --profile file if given).
  --write-config    Overwrite the config file with archward defaults and exit.
  --version         Print version and exit.
```

### Exit codes

```
0  SUCCESS / PACNEW_MERGE_NEEDED / NEEDS_REVIEW
1  UPDATE_FAILED / VERIFY_FAILED
2  REBOOT_NEEDED
```

## Configuration

`~/.config/archward/config.toml` — auto-created at first run.

```toml
schema_version = 1

[gates]
snapshot_max_age_minutes = 60
min_disk_gb = 5

[risk]
high = ["glibc", "systemd", "openssh", "mesa", ...]
kernel_patterns = ["linux", "linux-headers", "linux-cachyos*", ...]
kernel_pattern_exclude = ["linux-firmware*", "linux-docs*"]
medium_patterns = ["docker*", "qemu*", "postgresql*", "nginx*", ...]

[services]
to_verify = ["sshd.service", "NetworkManager.service", ...]

[[pacnew.rules]]
pattern = "*sshd_config*"
strategy = "review_needed"

[hooks]
pre_update = []          # shell commands run before pacman -Syu
post_verify = []         # shell commands run after the verify phase
timeout_seconds = 60
fail_pipeline_on_error = false   # true → non-zero pre_update hook aborts the pipeline

# Plus [aur], [pacman], [verify], [privilege] sections —
# run `archward --write-config` to emit the full schema with all defaults.
```

The GUI's **Preferences** dialog edits this file with Pydantic validation;
hand-edits work too. Run `archward --detect` whenever your kernel set, AUR
helper, or enabled services change — it proposes a diff and asks before
applying.

## Profiles

Use `--profile NAME` to point archward at
`~/.config/archward/profiles/<NAME>.toml` instead of the default
`config.toml`. Useful when one machine wears multiple hats (lab vs.
daily-driver, baremetal vs. VM, lenient vs. enforcing hooks) and you
don't want to shuffle config files in and out of place. Both the CLI
and the GUI honor the flag.

```bash
# CLI — run an update against a stricter "ci" profile (e.g.
# fail_pipeline_on_error=true, tighter gates). First run bootstraps
# the file with defaults.
archward --profile ci

# CLI — auto-detect kernels/services against a specific profile.
archward --profile lab --detect

# CLI — reset a profile to defaults.
archward --profile lab --write-config

# GUI — same flag. The active profile name appears in the window
# title and status bar; Preferences edits write back to the profile
# file, not the default config.
archward-gui --profile lab
```

`NAME` must match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` — no leading dot, no
path separators, no shell-meaningful characters. The default
`config.toml` is unchanged when a profile is in use.

The GUI's **Preferences → Profiles** tab also manages profiles in-place:
list / switch / new from defaults / save current as new / rename /
delete / open in editor. Switching reloads the running window against
the selected profile (no restart). The default config appears as a
switchable pseudo-profile at the top of the list (rename and delete are
disabled for it). If you switch with unsaved edits, you'll be prompted
to Save (writes to the *current* profile), Discard, or Cancel.
Switching is refused while a pipeline is running.

There's also an optional **"Remember last-used profile across launches"**
checkbox at the bottom of the tab. When enabled, `archward-gui` launched
without `--profile` reopens whatever profile was active when you last
closed the window. Off by default (no hidden state); backed by QSettings
in `~/.config/archward/archward.conf` so the toggle and remembered path
live separately from any profile's `config.toml`. CLI behavior is
unchanged — `--profile` is always explicit on the command line.

## User-defined hooks

archward's built-in pipeline handles the universal safe-update concerns —
snapshot, gates, risk, pacman + AUR, pacnew, verify. For checks that are
specific to *your* machine — external backup freshness, HTTP service
health, mountpoint state, VPN connectivity, specific bind addresses —
wire shell commands into the **pre-update** or **post-verify**
checkpoints via the `[hooks]` section of the config (or the Preferences
→ Hooks tab):

```toml
[hooks]
pre_update = [
    # Refuse update if backup is stale — your rollback path, your call:
    'find /mnt/backup/daily/ -mmin -1560 -type f 2>/dev/null | grep -q . && echo "OK: backup fresh" || { echo "WARN: backup stale"; exit 1; }',
]
post_verify = [
    # Catch "service active but HTTP wedged" mid-startup:
    'curl -sf --max-time 5 http://localhost:8096/health >/dev/null && echo "OK: service responding" || echo "WARN: service HTTP down"',
    # Catch silent NFS / FUSE drop-outs:
    'mountpoint -q /mnt/backup && echo "OK: /mnt/backup mounted" || echo "WARN: /mnt/backup not mounted"',
]
timeout_seconds = 60
fail_pipeline_on_error = false   # set to true to make pre_update hooks enforcing
```

Each command runs via `/bin/sh -c`, so pipes, env vars (`$ARCHWARD_PHASE`
is injected), and redirection all work. Hook output appears live in the
GUI log pane, lands in `archward.log`, and renders as a `hooks` bucket
in the Verify view at completion (one row per hook with status icon and
the last line of output).

**See [`docs/hooks.md`](docs/hooks.md) for the full guide** — design
patterns (the OK/WARN convention, why backup-freshness hooks are a
"canary" for backup pipeline health, when to use enforcing vs
informational), worked examples for common health checks (HTTP probes,
interface presence, real-time reachability, specific bind verification),
and safety knobs (per-hook timeout, the `fail_pipeline_on_error` flag,
idempotence requirements).

## GUI walkthrough

The single-window GUI mirrors the CLI pipeline:

- **Phase rail** (left) — 11 rows (Preflight → Snapshot → Gates → Risk →
  Pre-hooks → Update (official) → Update (AUR) → Pacnew → Verify →
  Post-hooks → Result) with status icons: `○ pending`, `⟳ running`,
  `● pass`, `▲ warn`, `✕ fail`, `– skipped`. Clickable for back-navigation;
  the active phase row is highlighted.
- **Content area** (right) — `QStackedWidget` whose page switches with the
  active phase: snapshot progress checklist, gates table, risk
  HIGH/MEDIUM/LOW tree with per-package checkboxes + Proceed/Cancel
  buttons, update stream pane (shared official + AUR), pacnew table with
  per-row View Diff / Keep Ours / Take New / Edit buttons, verify grouped
  by bucket (universal · services · plugin · hooks). The view stays on the
  last live phase after completion.
- **Log pane** (bottom, collapsible) — full text of everything the pipeline
  emitted, dual-logged to `~/.local/state/archward/logs/archward.log`.
- **Result strip** (bottom) — slim, color-coded banner showing the final
  RESULT tag plus a compact one-liner of secondary signals.
- **Snapshot Browser…** toolbar button — modal browser over all past
  snapshots with per-file config restore (perm-preserving) and per-package
  upgrade/downgrade (via `pacman -U` against the local cache). Bulk
  variants ("Restore all configs", "Apply all package versions") run in
  one atomic transaction; boot-critical packages require Type-YES
  confirmation. A pre-rollback snapshot is taken automatically so
  rollback-of-rollback works.
- **Preferences…** toolbar button — 12-tab dialog over the TOML schema
  (General · Gates · Risk · Services · Pacnew · AUR · Pacman · Verify ·
  Privilege · Hooks · Profiles · Advanced) with inline help text per
  field. The **Profiles** tab lists every profile (plus the default
  config), switches the running window in-place, and supports
  new/rename/delete/open-in-editor. The **Advanced** tab has Re-detect
  (propose diff), Reset to defaults, Open config in `$EDITOR`.

HIGH-risk approval happens **inline** in the Risk view: per-package
checkboxes default checked, Proceed/Cancel buttons enable when the
pipeline reaches risk approval, and deselected package names flow through
as `--ignore=<pkg>` to pacman. Recoverable gate overrides still use a
modal QMessageBox. Both block the pipeline worker thread on the user's
answer via Qt threading primitives.

Real-update mutations (config restore, package upgrade/downgrade) run on
a `QThread` with an indeterminate `QProgressDialog` so the GUI stays
responsive during `pacman -U`.

## Scope

archward has two layers:

**Universal — built in, applies to any Arch-based machine.** Snapshot
(packages, configs, services, kernel, network state, pacnew baseline),
pre-flight (`db.lck` + single-instance), gates (snapshot freshness, disk),
risk classification (HIGH/MEDIUM/LOW with kernel-pattern matching and
transaction preview), per-package deselect at HIGH-risk approval,
official pacman update, AUR update via auto-detected helper, pacnew rule
table with permission-preserving apply, verify (kernel match, .pacnew
remaining, disk, pacman.log scan, opt-in `systemctl is-active` services),
granular and bulk rollback (snapshot browser + per-file config restore +
per-package downgrade from the local pacman cache), desktop notifications
on completion, theme-aware GUI colors.

**Machine-specific — wired via `[hooks]` in your config.** External
backup-freshness gates, HTTP/TCP service health probes, mountpoint
checks, network-interface presence, specific bind verification,
maintenance-window blackouts. Each is a small shell command with an
OK/WARN message convention; output renders as a `hooks` bucket in the
Verify view. See **[`docs/hooks.md`](docs/hooks.md)** for design
patterns and worked examples.

**Extensible — third-party verify probes via Python entry points.**
The `archward.verify_checks` entry-point group lets any
pip-installable package contribute additional checks (in Python, with
full access to `ConfigModel` + `Snapshot`) that land in a `plugin`
bucket alongside the built-ins. Useful when shell hooks aren't
expressive enough (D-Bus probes, typed HTTP retry logic, structured
log parsing). See **[`docs/plugins.md`](docs/plugins.md)** for the
contract and a worked `archward-verify-zfs` skeleton.

**Out of scope (intentional):** running as a daemon, scheduling cron-style
recurring updates, network-only / offline-only modes, distros not in the
Arch family. archward is invoked manually (CLI) or via the GUI and runs
to completion.

## Development

```bash
./venv/bin/python3 -m pytest tests/unit/ -q
```

Test fixtures live under `tests/fixtures/`; see `docs/development.md` for
the regeneration procedure when pacman / yay output formats drift.

## License

GPL-3.0-or-later.
