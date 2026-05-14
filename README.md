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
5. **Official update** — `sudo pacman -Syu --noconfirm --noprogressbar
   --color=never`, line-buffered + ANSI-stripped streaming.
6. **AUR** — auto-detect `yay` → `paru` → `aurutils`, run helper update,
   capture build failures (last 50 lines per failed package). Skipped
   gracefully if no helper is on PATH.
7. **Pacnew** — find new `.pacnew` files since snapshot, classify per a
   rule table (sshd_config / mirrorlist / pacman.conf / fstab / grub /
   resolved.conf / faillock.conf / sysctl.d/* / *.hook); apply preserves
   original ownership and permissions.
8. **Verify** — kernel match, .pacnew remaining, disk, pacman.log scan,
   opt-in `systemctl is-active` services list, EndeavorOS
   reboot-recommended log.
9. **Report** — emit `RESULT:` tag (`SUCCESS` / `NEEDS_REVIEW` /
   `REBOOT_NEEDED` / `PACNEW_MERGE_NEEDED` / `VERIFY_FAILED` /
   `UPDATE_FAILED`) for scripted use; secondary tags annotate the primary.

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
  --detect          Run distro / kernel / AUR-helper / service detection and
                    propose a diff against ~/.config/archward/config.toml.
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

# ... see archward --write-config for the full schema.
```

The GUI's **Preferences** dialog edits this file with Pydantic validation;
hand-edits work too. Run `archward --detect` whenever your kernel set, AUR
helper, or enabled services change — it proposes a diff and asks before
applying.

## GUI walkthrough

The single-window GUI mirrors the CLI pipeline:

- **Phase rail** (left) — 9 rows (Preflight → Result) with status icons:
  `○ pending`, `⟳ running`, `● pass`, `▲ warn`, `✕ fail`, `– skipped`.
- **Content area** (right) — `QStackedWidget` whose page switches with the
  active phase: snapshot progress checklist, gates table, risk
  HIGH/MEDIUM/LOW tree, update stream pane (shared official + AUR), pacnew
  table, verify grouped by bucket. The view stays on the last live phase
  after completion.
- **Log pane** (bottom, collapsible) — full text of everything the pipeline
  emitted.
- **Result strip** (bottom) — slim, color-coded banner showing the final
  RESULT tag plus a compact one-liner of secondary signals.
- **Preferences…** toolbar button — 10-tab dialog over the TOML schema with
  Re-detect (propose diff), Reset to defaults, Open config in `$EDITOR`.

HIGH-risk approval and recoverable gate overrides route through modal
dialogs; the pipeline worker thread blocks on the user's answer via
`BlockingQueuedConnection`.

## Scope

archward ships **universal** safe-update behavior — gates, risk
classification, pacnew strategy, kernel-match verify — that applies to any
Arch-based machine. Host-specific concerns (custom network probes, HTTP
health checks for self-hosted services, port-listen verification, mountpoint
checks for personal backup volumes, VPN connectivity gates) are deliberately
**out of scope for v1** and reserved for v2 hooks (`pipeline/hooks.py` is
the seam — pre-update and post-verify shell commands run from your config).

## Development

```bash
./venv/bin/python3 -m pytest tests/unit/ -q
```

Test fixtures live under `tests/fixtures/`; see `docs/development.md` for
the regeneration procedure when pacman / yay output formats drift.

## License

GPL-3.0-or-later.
