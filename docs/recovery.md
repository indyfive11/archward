# Recovery guide

> *"Something broke after an update. Now what?"*

archward's recovery workflow is built around one idea: every update
runs against a snapshot you can roll back to. This document walks
through the recovery paths in order of how often you'll need them, on a
real system, with the exact commands to type.

Every command in this guide works from a plain shell — no GUI required.
That's deliberate: if your desktop won't come back after a kernel
update, you're going to be in tty1 (Ctrl+Alt+F2) with nothing but a
terminal.

---

## Scenario 1 — Desktop won't come back after a reboot

You ran an update, archward emitted `RESULT:REBOOT_NEEDED`, you
rebooted, and now you're staring at a black screen, a login loop, or
a kernel panic. The most common causes:

- **nvidia / virtualbox / akmod DKMS modules didn't rebuild** against
  the new kernel.
- **`mkinitcpio` didn't re-run** because a hook was disabled, so the
  initramfs is missing modules your boot needs.
- **`sddm` / `gdm` / display-manager regressed** in a package update.

### Step 1 — get to a TTY

Press **Ctrl+Alt+F2** (or F3 / F4 depending on which TTY is free).
Login as your normal user. You don't need root yet.

### Step 2 — find a working snapshot

```bash
archward snapshot list
```

Each row is a rollback point. The newest is at the top. Pick the one
that was taken *immediately before* the breaking update. The
`age` column makes that easy — usually it's the one labeled with
roughly the time you remember running the update.

```
snapshot                      age  distro        kernel                  configs
2026-05-15_142647         15m ago  endeavouros   7.0.8-1-cachyos-bore    8   ← post-broken-update
2026-05-15_134329         44m ago  endeavouros   7.0.8-1-cachyos-bore    8   ← THIS ONE (pre-update)
2026-05-15_134116         47m ago  endeavouros   7.0.7-arch2-1           8   ← older still
```

Each snapshot's `.timestamp` is set when the update PHASE STARTED —
so the snapshot immediately *before* the breaking update is your
target. Snapshots are immutable once `.timestamp` is written.

### Step 3 — diagnose what broke

```bash
archward verify --snapshot 2026-05-15_134329
```

Output looks like this on a broken system:

```
[verify] Verifying post-update state
  PASS universal/kernel: kernel matches snapshot
  FAIL universal/pacman-log: 3 ALPM error(s) found in /var/log/pacman.log
  PASS universal/disk: 652GB free on /
  FAIL services/sddm.service: inactive
  PASS plugin/zerotier-daemon: online — node 7761af65ac

=== archward verify result ===
RESULT:VERIFY_FAILED
  verify: 2 FAIL / 0 WARN
```

The **FAIL rows tell you what broke.** `services/sddm.service: inactive`
means your display manager didn't come up — that explains the black
screen. Cross-reference with `journalctl -xeu sddm.service` for the
underlying reason; common culprits are listed in
[Step 4 — finding the responsible package](#step-4--finding-the-responsible-package).

### Step 4 — finding the responsible package

The verify FAIL points at a *symptom* (`sddm.service inactive`), not
the *cause* (which package update broke it). To find the package,
correlate the symptom against what changed:

```bash
archward snapshot show 2026-05-15_134329
```

The output's *Critical packages snapshotted* block shows what the
snapshot recorded. The suspects are usually one of:

| Symptom | Likely culprit |
|---|---|
| Display manager won't start (`sddm`, `gdm`) | `nvidia` / `mesa` / `sddm` / `qt6-base` |
| Black screen, login loop | `xorg-server` / `nvidia` / `nvidia-utils` |
| Kernel panic at boot | `linux` / `linux-firmware` / `dracut`/`mkinitcpio` |
| Sound dead | `pipewire` / `wireplumber` / `pipewire-pulse` |
| Network down | `systemd` / `NetworkManager` / `iwd` |
| Bluetooth, USB, peripherals | kernel + DKMS modules |

Compare snapshot versions against current with:

```bash
pacman -Q nvidia mesa sddm                 # current versions
archward snapshot show 2026-05-15_134329 | grep -E "nvidia|mesa|sddm"   # snapshot versions
```

The differences are your candidates. If only `nvidia` differs, the
nvidia upgrade is your prime suspect.

### Step 5 — roll back the suspect package

```bash
archward rollback package 2026-05-15_134329 nvidia
```

archward looks in `/var/cache/pacman/pkg/` for the snapshot version,
runs `sudo pacman -U` to install it, and the older version replaces
the broken new one. If the cached package is present this just works.

If the cache was pruned (e.g. by `paccache` aggressively) you'll see:

```
FAIL: version 575.64.05-1 not present in /var/cache/pacman/pkg/
```

In that case you have two options:
1. Pull the old `.pkg.tar.zst` from the
   [Arch Linux Archive](https://archive.archlinux.org/) and
   `sudo pacman -U /path/to/file.pkg.tar.zst` manually.
2. Use a different snapshot whose package version *is* still cached.

### Step 6 — verify the fix

```bash
archward verify
```

Should now show `services/sddm.service: PASS` (or whatever was failing
before). When verify is clean, the fix landed cleanly.

### Step 7 — reboot or restart the service

For a desktop manager: try `sudo systemctl restart sddm.service`
first. If that brings the desktop back, you're done. If not:

```bash
sudo reboot
```

When the system comes back up, the rolled-back package should
boot normally.

---

## Scenario 2 — System boots, but something's wrong

You came back from a reboot, the desktop loaded, but a service
is broken — your VPN won't connect, sound is gone, Sonarr's down.
No reboot is needed; the diagnostic is the same.

```bash
archward verify         # see what's failing
```

For each FAIL row in the `services` or `plugin` bucket, drill into
the detail:

```bash
systemctl status <unit>     # what's the immediate error?
journalctl -xeu <unit>      # full failure log
```

In the **GUI's** Verify view, the **What to do?** button next to each
FAIL row pops a context-specific hint with these exact commands. The
CLI doesn't render the buttons but the failure messages contain
enough to point you in the right direction.

If the cause is an update regression, follow [Step 5 above](#step-5--roll-back-the-suspect-package).

---

## Scenario 3 — Unmerged `.pacnew` files

Sometimes pacman leaves `<config>.pacnew` files in `/etc` — the
upstream's new defaults didn't merge cleanly with your customizations.
archward's pipeline classifies these against a rule table but you can
also handle them manually any time:

```bash
archward pacnew list           # see what's outstanding
```

For each file shown, inspect the differences:

```bash
archward pacnew diff /etc/sshd_config.pacnew
```

Then resolve:

```bash
# Keep your version, discard the .pacnew:
archward pacnew apply /etc/sshd_config --strategy=keep_ours

# Replace your version with the upstream new (ownership + mode preserved):
archward pacnew apply /etc/sshd_config --strategy=take_new

# Open both in $EDITOR side-by-side:
archward pacnew apply /etc/sshd_config --strategy=edit
```

The same actions are available per-row in the GUI's Pacnew view.

---

## Scenario 4 — Bulk rollback (something broke EVERYTHING)

If an update simultaneously bricked sound + display + network,
single-package rollback is too tedious. The bulk option restores
*everything* to a known-good snapshot in one transaction:

```bash
# Restore every captured config to its /etc location:
archward rollback all-configs 2026-05-15_134329

# Downgrade every drifted package to its snapshot version
# (single atomic pacman -U, refuses boot-critical without --confirm):
archward rollback all-packages 2026-05-15_134329
```

Both commands **auto-take a pre-rollback snapshot first** — if the
rollback itself goes wrong, you can rollback the rollback:

```bash
# A new snapshot was just created. Find it:
archward snapshot list | head -3

# Apply it the same way:
archward rollback all-packages 2026-05-15_150000
```

Pre-rollback snapshots are why bulk rollback is safe to try.

### The boot-critical YES gate

Bulk operations refuse to downgrade `glibc`, `systemd`, `openssl`,
`mesa`, `pipewire`, `wireplumber`, or `openssh` without an explicit
`--confirm-boot-critical` flag AND a case-sensitive `YES` typed on
stdin. That's intentional friction — downgrading these can leave the
system unbootable. If you're sure:

```bash
archward rollback all-packages 2026-05-15_134329 --confirm-boot-critical
# When prompted:
Type YES (case-sensitive) to proceed: YES
```

Anything other than the four characters `YES` (uppercase) aborts.

---

## Finding the right snapshot

If your snapshot dir has a hundred entries, narrowing down which one
to use can be tedious. Two strategies:

```bash
# All snapshots, newest first:
archward snapshot list --all

# Show what was captured in a specific snapshot:
archward snapshot show 2026-05-15_134329
```

The `show` output's *Taken* timestamp tells you exactly when that
snapshot ran. Match it against your memory or your shell history to
identify the right one.

---

## Cache policy: is rollback even possible?

Every rollback in this guide pulls the old `.pkg.tar.*` from
`/var/cache/pacman/pkg/`. If that file isn't there, the downgrade
can't happen — so it's worth knowing your cache policy *before* you
need it.

Open **Preferences → Cache**. The coloured banner is archward's
rollback-safety verdict:

- **BALANCED / GENEROUS** — you have rollback headroom. Nothing to do.
- **TIGHT** — only ~1 prior version kept; a single bad update uses it
  up. Consider a roomier preset.
- **UNMANAGED** — the cache is never pruned. Rollback always works,
  but the partition grows without bound — pick a preset to cap it.
- **DANGEROUS** — *this is the one that bites.* Either a
  post-transaction cleaning hook (it deletes the rollback substrate
  *inside the same `pacman -Syu` archward runs*), or `keep ≤ 1`, or
  `CleanMethod=KeepCurrent`. Rollback for fresh updates will fail.

Pick the preset that matches the box — **Home**, **Workstation**,
**Server**, or **Mission-critical** — and archward shows the exact
`sudo` commands before applying them. archward never deletes a
package-owned or third-party cleaning hook for you; if the verdict is
DANGEROUS because of a hook, the Cache tab names the file so you can
decide.

The same check runs at **pre-flight** (an overridable warning before
the update starts) and again in **verify** (a `rollback-cache` FAIL if
the pre-update files for what just changed are already gone). If you
see that FAIL, the next section is your fallback.

---

## When the cached package is gone

`/var/cache/pacman/pkg/` is pruned occasionally — by `paccache`,
by manual `pacman -Sc`, or by aggressive cleanup hooks. If
`archward rollback package` says the cache version is missing:

```bash
archward rollback package 2026-05-15_134329 nvidia
# → FAIL: version 575.64.05-1 not present in /var/cache/pacman/pkg/
```

Use the Arch Linux Archive — every package version is preserved:

```
https://archive.archlinux.org/packages/n/nvidia/
```

Download the `.pkg.tar.zst` for the right version + your arch, then:

```bash
sudo pacman -U /tmp/nvidia-575.64.05-1-x86_64.pkg.tar.zst
```

After installing, run `archward verify` to confirm the rollback fixed
things. (archward is intentionally cache-only — pulling from the
network is policy, not bug.)

---

## When rollback itself fails

Each `restore_config` writes a `.pre-rollback.bak` next to the live
file before overwriting it. If the restore goes wrong:

```bash
sudo cp /etc/mirrorlist.pre-rollback.bak /etc/mirrorlist
```

For package rollbacks, `pacman -U` is atomic — either it fully
succeeds or it fully fails. If it errors with "conflicts with
installed package," that's pacman protecting you from a dependency
break; archward shows you the message verbatim. Resolve per pacman
conventions (usually `--overwrite=...` or a separate `pacman -R`
first).

For bulk-rollback safety, the auto-pre-rollback snapshot taken just
before `pacman -U` is your insurance — point `archward rollback
all-packages <pre-snap-id>` at it to undo.

---

## When the system won't boot at all

If you can't even reach a TTY — GRUB lands you in `emergency.target`,
the kernel hangs at "Loading initial ramdisk," or systemd-tty is
unreachable — that's outside archward's scope. You need a live USB:

1. Boot the Arch / EndeavourOS / etc. installation media.
2. Mount your root + boot partitions.
3. `arch-chroot /mnt`.
4. From inside the chroot, archward IS available if it was installed:
   `archward snapshot list`, `archward rollback package <id> <pkg>`.
5. `exit`, `umount -R /mnt`, reboot.

The [ArchWiki recovery guide](https://wiki.archlinux.org/title/General_recovery)
covers the mount + chroot dance in detail.

---

## Cheat sheet

The commands above, compressed:

```bash
# Find the right snapshot
archward snapshot list
archward snapshot show <id>

# Re-run verify (post-reboot diagnostic)
archward verify
archward verify --snapshot <id>

# Single-target rollback (reversible, safest)
archward rollback config <id> <filename>
archward rollback package <id> <pkg-name>

# Bulk rollback (auto-takes pre-rollback snapshot)
archward rollback all-configs <id>
archward rollback all-packages <id>
archward rollback all-packages <id> --confirm-boot-critical    # for boot-critical
                                                                # then type YES on stdin

# Pacnew triage
archward pacnew list
archward pacnew diff <path>
archward pacnew apply <path> --strategy=keep_ours|take_new|edit|leave

# Cleanup
archward snapshot prune --keep N
```

See [`docs/cli.md`](cli.md) for the full subcommand reference with
exit codes, flags, and behavior details.
