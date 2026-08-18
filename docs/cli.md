# CLI reference

archward has two invocation shapes:

1. **Flag form** — `archward [flags]` runs the full update pipeline.
   This is the original interface; unchanged and backward-compatible.
2. **Subcommands** (v0.4.3+) — `archward <command> ...` expose the
   Snapshot-Browser capabilities (verify / snapshot / rollback /
   pacnew) so a user without a working GUI can recover.
   `archward aur quarantine` (v0.4.6+) manages the AUR build
   quarantine state without needing the GUI or sudo.
   **v0.4.11 additions:** `archward gates` and `archward risk` expose
   the pre-update checks standalone; `archward aur pending` lists
   pending AUR updates; `archward cache` inspects and manages the
   pacman package cache.

For a task-oriented "my system broke, what do I type" walkthrough see
**[`docs/recovery.md`](recovery.md)**. This document is the exhaustive
reference.

---

## Flag form (full pipeline)

```
archward [flags]
```

| Flag | Effect |
|---|---|
| `--dry-run` | Snapshot + gates + risk classification (incl. AUR pending), then exit. No update is performed. |
| `--auto` | Hands-off: abort the run if HIGH-RISK packages are present (instead of prompting). |
| `--yes` | Auto-confirm HIGH-risk approval + recoverable gate overrides. Does **not** auto-confirm boot-critical rollback (separate gate). |
| `--no-aur` | Skip the AUR phase regardless of `aur.enabled`. |
| `--profile NAME` | Use `~/.config/archward/profiles/<NAME>.toml` instead of the default config. NAME must match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`. First run bootstraps the file with defaults. |
| `--detect` | Run distro / kernel / AUR-helper / service detection, propose a config diff, exit. |
| `--write-config` | Write defaults to the config file and exit. If the file already exists it is backed up to `<file>.bak` first. |
| `--version` | Print version, exit. |
| `--help` | Usage, exit. |

`--dry-run` and `--auto` are mutually exclusive. `--profile` applies to
both the flag form and every subcommand (place it before the
subcommand: `archward --profile lab verify`).

### Exit codes (flag form)

```
0  RESULT:SUCCESS / RESULT:PACNEW_MERGE_NEEDED / RESULT:NEEDS_REVIEW
1  RESULT:UPDATE_FAILED / RESULT:VERIFY_FAILED
2  RESULT:REBOOT_NEEDED  (informational; user must reboot)
3  another archward instance is already running (lock contention)
4  RESULT:ABORTED  (user declined, a gate refused, or the run was
   cancelled — the update did not fail, archward chose not to proceed)
```

---

## `archward gates`

Run the pre-flight and gate checks the pipeline would run before any
update, **without taking a snapshot or performing an update**.

```
archward gates [--snapshot ID]
```

| Flag | Effect |
|---|---|
| `--snapshot ID` | Use this snapshot for the age check. Default: the latest. |

**Pre-flight checks** (always run): pacman DB lock, cache-safety WARN,
Arch News feed, AUR quarantine FYI.

**Gate checks**: snapshot age (against the resolved snapshot) and disk
space. If no snapshots exist the age check is SKIPped and the disk
check runs inline.

### Exit codes

```
0  all checks pass
1  one or more FAIL
2  warnings only (no FAIL)
```

---

## `archward risk`

Classify pending updates by risk level, **without running an update**.
Uses the same `checkupdates`-based classification as the pipeline.

```
archward risk [--no-aur]
```

| Flag | Effect |
|---|---|
| `--no-aur` | Skip the AUR pending-list query. |

Output is grouped HIGH → MEDIUM → LOW. Packages with a reason (kernel,
glibc, etc.) show it in parentheses. Transaction preview (replacements,
conflicts) follows if `pacman -Sup` reports any. AUR pending updates
from the configured helper are shown last (suppressed by `--no-aur`
or when `aur.enabled = false`).

Always exits 0 — risk classification is informational.

---

## `archward verify`

Re-run the verify phase against an existing snapshot. **No new snapshot
is taken; no update is performed.** This is the post-reboot diagnostic
— it catches failures that only surface after the kernel actually
boots (DKMS modules that didn't rebuild, mkinitcpio hooks that didn't
fire, pacnew left unmerged so a daemon read stale config, systemd
unit syntax changes).

```
archward verify [--snapshot ID]
```

| Flag | Effect |
|---|---|
| `--snapshot ID` | Verify against this snapshot. Default: the latest. |

Runs every built-in check (kernel match, .pacnew remaining,
**rollback-cache** survival, **boot-integrity** / initramfs freshness,
disk, pacman.log scan, opt-in `systemctl is-active` services) **plus
all discovered plugins** (the `archward.verify_checks` entry-point
group — e.g. the bundled ZeroTier example). Output is the same check
set the GUI's Verify view renders, in plain text.

Checks added in v0.4.4:

- **`rollback-cache`** — FAILs if a cache-cleaning hook or aggressive
  paccache policy deleted the pre-update `.pkg.tar.*` for a package
  that just changed (archward's downgrade path needs it). The full
  pipeline also raises an overridable **pre-flight** WARN for this
  *before* the update runs.
- **`boot-integrity`** — FAILs if an `initramfs-<flavour>.img` is
  older than its `vmlinuz-<flavour>` (the mkinitcpio/dracut pacman
  hook didn't run — the box may not boot). Does NOT check grub.cfg
  mtime (with stable kernel filenames it legitimately predates the
  kernel on a bootable system). SKIPs cleanly when there's no
  flavour-named initramfs (dracut-kver / UKI) or no `/boot`.

Checks added in v0.4.5:

- **`orphans`** — WARNs if `pacman -Qdtq` reports packages installed as
  deps with no remaining dependents. WARN (not FAIL) — some orphans are
  intentional. Detail lists the package names; "What to do?" points at
  `pacman -Qi` / `pacman -Rns`.
- **`security-advisories`** — Cross-references installed packages against
  the Arch Security Advisory feed (`security.archlinux.org/all.json`).
  Critical/High severity → FAIL; Medium/Low → WARN. SKIPs when
  `arch-audit` is installed or the network is unreachable. Disable via
  `verify.security_advisories = false`.

The **pre-flight** phase (v0.4.5) also checks the Arch News RSS feed
(`archlinux.org/feeds/news/`) and WARNs if items were posted since your
last update. Disable via `gates.skip_news_check = true`.

`archward verify` refuses an **incomplete snapshot** up front
(exit 3) — missing `.timestamp`, empty `packages/all.txt`, or no
`configs/` — naming the missing section instead of failing cryptically
later. (`critical.txt` is *not* required: the rollback path
reconstructs it from `all.txt` + kernel patterns, so older snapshots
that predate it stay usable.)

### Exit codes

```
0  RESULT:SUCCESS / RESULT:PACNEW_MERGE_NEEDED / RESULT:NEEDS_REVIEW
1  RESULT:VERIFY_FAILED   (one or more FAIL checks)
2  RESULT:REBOOT_NEEDED
3  snapshot not found, or incomplete (named missing section)
```

### Example

```
$ archward verify --snapshot 2026-05-15_134329
verifying against snapshot 2026-05-15_134329
  taken: 2026-05-15T13:43:31
  kernel at snapshot: 7.0.8-1-cachyos-bore

[verify] Verifying post-update state
  PASS universal/kernel: kernel matches snapshot
  PASS services/sshd.service: active
  FAIL services/sddm.service: inactive
  PASS plugin/zerotier-daemon: online — node 7761af65ac (v1.16.0)
  → verify: 1 FAIL, 0 WARN

=== archward verify result ===
RESULT:VERIFY_FAILED
  verify: 1 FAIL / 0 WARN
```

---

## `archward snapshot`

### `archward snapshot list`

Newest-first table of snapshots. Default shows the 20 newest.

```
archward snapshot list [--limit N | --all]
```

| Flag | Effect |
|---|---|
| `--limit N` | Show at most N snapshots (default 20). |
| `--all` | Show every snapshot, ignoring `--limit`. |

Columns: snapshot id, age, distro, kernel (truncated to fit the
terminal), captured-config count. Directories without a `.timestamp`
marker (incomplete / partial-failure leftovers) are filtered out.

### `archward snapshot show <id>`

Full detail for one snapshot: meta block, captured config paths
(with sizes), and the critical-package list with versions. Use this
to discover the exact `<filename>` to pass to `archward rollback
config`, and to compare snapshot package versions against current.

```
archward snapshot show <id>
```

Exit 3 if the snapshot doesn't exist or is incomplete.

### `archward snapshot prune`

Delete old snapshots, keeping the N newest. Mirrors the GUI's
"Prune now…".

```
archward snapshot prune [--keep N] [--yes]
```

| Flag | Effect |
|---|---|
| `--keep N` | Number to retain. Default: `cfg.general.keep_snapshots`. |
| `--yes` | Skip the confirmation prompt. |
| `--clean-orphans` | Also remove incomplete snapshot directories that have no `.timestamp` marker (e.g. left behind by a hard-killed run). |

Without `--yes`, prints exactly which snapshots will be deleted
(oldest first) and asks `proceed? [y/N]`. Orphan directories are
listed separately and only removed when `--clean-orphans` is passed.

---

## `archward rollback`

All five variants resolve the snapshot first and build the sudo
strategy. Resolution exits 3 if the snapshot is missing, or if it's
**incomplete** — missing `.timestamp`, missing/empty `packages/all.txt`,
or a missing/empty `configs/` directory (`critical.txt` is
reconstructable and not
required). The specific missing section is printed; archward refuses
*before* it touches pacman state, so you never get a half-applied
restore from a
bad snapshot. Each operation goes through the same pure-Python
primitives the GUI Snapshot Browser uses.

### `archward rollback config <id> <filename>`

Restore one captured config to its live `/etc` location, preserving
the live file's ownership + mode. A `.pre-rollback.bak` of the live
file is written before the overwrite (so this is reversible).

```
archward rollback config <snapshot-id> <filename>
```

`<filename>` accepts either the captured snapshot filename
(`mirrorlist`) or the full live relpath (`etc/pacman.d/mirrorlist`).
Run `archward snapshot show <id>` to see captured filenames.

Exit 2 if `<filename>` doesn't match any captured config; exit 1 if
the restore itself fails (with the failure reason).

### `archward rollback package <id> <pkg> [--confirm-boot-critical]`

Downgrade/upgrade one package to its snapshot version, from
`/var/cache/pacman/pkg/`.

```
archward rollback package <snapshot-id> <pkg-name> [--confirm-boot-critical]
```

| Flag | Effect |
|---|---|
| `--confirm-boot-critical` | Required when `<pkg>` is boot-critical (glibc, systemd, openssl, lib32-glibc, lib32-openssl, systemd-libs). Even with this flag, you must type `YES` (case-sensitive) on stdin. |

**Scope:** Only packages tracked in `packages/critical.txt` are eligible —
glibc, systemd, openssl, mesa, pipewire, openssh, kernels, and AUR foreign
packages. Standard repo packages (vim, git, curl, etc.) are not eligible even
if present in the snapshot's `packages/all.txt`. To downgrade any other
package manually, use pacman directly:

```
pacman -U /var/cache/pacman/pkg/<pkg>-<version>-*.pkg.tar.*
```

Exit 2 if the package wasn't captured in the snapshot (or isn't a critical
package), or if it's boot-critical and `--confirm-boot-critical` was omitted.
Exit 1 if the cache lacks the snapshot version (with a clear message) or the
`pacman -U` fails. Exit 0 on success **or** if the user declines the YES gate
(declining is a valid choice, not an error).

### `archward rollback all-configs <id> [--yes]`

Restore every captured config in one pass. **Auto-takes a
pre-rollback snapshot first** (so the rollback is itself reversible).

```
archward rollback all-configs <snapshot-id> [--yes]
```

Without `--yes`, lists every config it will restore and asks
`proceed? [y/N]`. Per-file failures are reported but don't abort the
rest; exit 1 if any file failed, 0 if all succeeded.

### `archward rollback all-packages <id> [--confirm-boot-critical]`

Single atomic `pacman -U` over every package whose snapshot version
differs from current. **Auto-takes a pre-rollback snapshot first.**

```
archward rollback all-packages <snapshot-id> [--confirm-boot-critical]
```

Prints the full plan (`name  current → target`, boot-critical rows
flagged) before doing anything. If the plan contains boot-critical
packages, refuses unless `--confirm-boot-critical` is set, and even
then requires a case-sensitive `YES` on stdin. Non-boot-critical
plans get a casual `proceed? [y/N]`.

Exit 0 = applied (or nothing to apply, or user declined). Exit 1 =
`pacman -U` failed. Exit 2 = boot-critical refusal without the flag.
Exit 3 = snapshot missing.

> Note: `--yes` (the flag-form auto-confirm) does **not** bypass the
> boot-critical YES gate. That gate is intentionally a separate,
> conscious decision.

### `archward rollback reinstall <id> [pkg ...] [--yes]`

List or reinstall packages *removed* since a snapshot (a dependency
dropped by a replacement, an accidental `pacman -Rns`).

```
archward rollback reinstall <snapshot-id> [pkg ...] [--yes]
```

Without arguments, lists every removed package with its snapshot
version, explicit/dep status, and reinstall source — cached
(`pacman -U` of the snapshot version), repos (`pacman -S`), or the
detected AUR helper — then asks `reinstall the above? [y/N]`. Naming
packages restricts the set; `--yes` skips the prompt.

Exit 0 = reinstalled (or nothing removed, or user declined). Exit 1 =
any reinstall failed. Exit 2 = none of the named packages are removed.
Exit 3 = snapshot missing/incomplete. AUR packages with no helper
available are skipped with a message.

---

## `archward aur`

### `archward aur quarantine list`

Print a table of all AUR build quarantine entries (active and resolved).

```
archward aur quarantine list
```

Columns: package, version, status (`counting` / `quarantined` / `resolved`),
failure count, retry-after or resolved date, last error snippet.
No sudo required. Exit 0 always; an empty state is not an error.

Example output:

```
package                   version               status        fails  retry/resolved  last error
-------------------------------------------------------------------------------------------------
radarr                    6.1.1.10360-1         quarantined       3  2026-05-22      error NU1902: Package 'MailKit' 4.15.1…
gossip-bin                0.9.2                 counting          2  —               sha256sums FAILED
old-pkg                   1.0                   resolved          3  2026-05-10      build() failed

1 active, 1 counting, 1 resolved
```

### `archward aur quarantine clear [PKG] [--yes]`

Clear one or all active quarantine entries (set status to `resolved`).

```
archward aur quarantine clear [PKG] [--yes]
```

| Arg / Flag | Effect |
|---|---|
| *(no args)* | Clear all `counting` + `quarantined` entries. Asks for confirmation listing each entry. |
| `PKG` | Clear just this package. Exit 2 if not found in quarantine state. |
| `--yes` | Skip the confirmation prompt when clearing all. |

Resolved entries cannot be re-cleared (exit 0, no-op). No sudo required.

#### Exit codes

```
0  success (or already resolved — no-op)
2  PKG not found in quarantine state
```

#### Managing quarantine in the GUI

Open **Preferences → AUR**. The quarantine history table shows all entries
with full row editing for active (`counting` / `quarantined`) rows:
- **Failures** column — edit the count to reset or force activation.
- **Retry / Resolved** column — set to a past date to force an immediate
  retry on the next run.
- **Status** column — change via dropdown; setting to `resolved` clears the entry.

Buttons below the table: **Clear selected**, **Clear resolved** (remove
resolved-only rows), **Clear all**.

### `archward aur pending`

List pending AUR updates from the auto-detected helper without taking
a snapshot or running an update.

```
archward aur pending
```

Uses the same helper-preference order as the full pipeline
(`yay` → `paru` → `aurutils`). Exit 2 if no helper is found. Exit 1 if
the helper query fails. Exit 0 with a "no pending updates" message if
the list is empty.

---

## `archward cache`

Inspect and manage the pacman package cache. All four actions share a
read-only default path; only `set-keep` and `clean` need sudo.

### `archward cache status`

Show the current retention policy, cache size, and rollback-safety
verdict.

```
archward cache status
```

Output includes: rollback safety verdict (`DANGEROUS` / `TIGHT` /
`BALANCED` / `GENEROUS` / `UNMANAGED`), paccache.timer state and
effective keep count, `PACCACHE_ARGS` if set, `CleanMethod` from
`pacman.conf`, cache size and file count, and any active post-transaction
cleaning hooks (flagged `[DANGEROUS]`). Followed by a plain-English
explanation of the verdict.

Always exits 0.

### `archward cache gaps`

List installed packages that have **no cached prior version** — i.e.
packages that cannot be rolled back from cache if the current version
breaks.

```
archward cache gaps
```

Prints each gap as `<name>  <current-version>`. "No rollback gaps"
means every installed package has at least one older version in the
cache. Exit 0 always.

### `archward cache set-keep N [--force]`

Write a `paccache.timer` drop-in that retains N versions per package,
and enable the timer.

```
archward cache set-keep N [--force]
```

| Arg / Flag | Effect |
|---|---|
| `N` | Versions to keep per package. Minimum 2 (enforced). |
| `--force` | Allow N == 1 (not recommended). N < 1 is always refused — keep=0 would delete the entire rollback cache. |

Writes `/etc/systemd/system/paccache.service.d/archward.conf`
(requires sudo), then runs `systemctl enable --now paccache.timer`.
Exit 2 if N < 1 (always), if N < 2 without `--force`, or if paccache.timer is not
available (non-systemd distros). Exit 1 if the write or systemctl
command fails.

### `archward cache clean`

Run paccache immediately, pruning to the current effective keep count.

```
archward cache clean
```

Shows cache size, the prune plan, and asks `Proceed? [y/N]` (skipped
when stdin is not a TTY). Exit 0 on success or user abort. Exit 1 if
paccache exits non-zero.

---

## `archward pacnew`

### `archward pacnew list`

Scan `/etc` for `.pacnew` files and print each with its
rule-classified strategy + note.

```
archward pacnew list
```

### `archward pacnew diff <path>`

Unified diff of the live config vs its `.pacnew`. `<path>` accepts
either form (the live `/etc/...` path or the `....pacnew` sibling).

```
archward pacnew diff <path>
```

Exit 3 if no matching `.pacnew` exists. Prints "(no differences …)"
if the two files are identical.

### `archward pacnew apply <path> --strategy=...`

Apply a resolution. Mirrors the GUI Pacnew view's per-row buttons.

```
archward pacnew apply <path> --strategy=keep_ours|take_new|edit|leave
```

| Strategy | Effect |
|---|---|
| `keep_ours` | Delete the `.pacnew`, keep the live file as-is. A copy of the discarded `.pacnew` is parked in `~/.local/state/archward/pacnew_trash/` first (v0.4.18), so this can be undone. |
| `take_new` | Replace the live file with the `.pacnew`, preserving the live file's ownership + mode (per the v0.4.1 atomic-apply fix; partial chown/chmod failure restores from a backup). |
| `edit` | Open both files via `sudoedit` with `$VISUAL` / `$EDITOR` (v0.4.18 — the editor runs unprivileged, the write-back is elevated). |
| `leave` | No-op (skip this file). |

`--strategy` is required. Exit 1 if the apply fails (e.g. sudo
denied), exit 2 for an unknown action, exit 3 if no `.pacnew` exists.

---

## `archward history`

Every pipeline run — GUI or CLI, including dry runs, aborts, and even
crashes — writes one JSON record to `~/.local/state/archward/runs/`.
`archward history` is the TTY surface over those records; the GUI twin
is Tools → Run History. Retention is automatic via `general.keep_runs`
(default 100; `0` disables pruning).

Bare `archward history` defaults to `list` — the one subcommand with a
default action.

Runs that only *display* a result without running (a second instance
blocked by the instance lock) are not recorded. Standalone
`archward verify` is not a pipeline run and writes no record.

### `archward history list [--limit N | --all] [--json]`

```
run                          age  result                      duration  mode         snapshot
2026-08-13_084022         2h ago  RESULT:UPDATE_FAILED             18s  interactive  2026-08-13_084023
```

`--json` emits summary rows only (`run_id`, `started`, `duration_s`,
`mode`, `tag`, `fail_count`, `warn_count`, `snapshot_id`) — full detail
lives behind `show <id> --json`.

### `archward history show <run_id|latest> [--json]`

Human-readable detail for one run: result, per-phase timeline with
durations, pacman error lines, AUR failures/quarantine, pacnew count.
`latest` resolves the newest run. The run's `snapshot_id` names the
rollback point taken for that run — the snapshot itself may already be
pruned (`keep_runs` outlives `keep_snapshots` deliberately; check
`archward snapshot list`).

`--json` dumps the raw document — **this is the stable scripting
surface**:

```
schema_version    1 — increments only on a rename/removal/shape change.
                  New fields may be ADDED without a bump: consumers must
                  ignore unknown keys. (Separate namespace from
                  config.toml's schema_version.)
run_id            YYYY-MM-DD_HHMMSS (+ "-2" suffix on same-second runs).
                  NOT the snapshot id — see the snapshot_id field.
started/finished  timezone-aware local ISO-8601; duration_s float.
mode              interactive | dry-run | auto        no_aur  bool
result            {tag, secondary_tags, fail_count, warn_count,
                  reboot_needed} — tag strings are the RESULT: contract.
                  null only when the run crashed (see aborted_reason).
aborted_reason    string | null ("exception: <Type>" on a crash)
update_exit_code  int | null      update_error_lines  [string]
snapshot_id       string | null   pacnew_count  int
deselected_packages  [string]
pending           [{name, old_version, new_version, source, risk,
                  is_kernel, reason}]
phases            ordered phase executions: [{phase, status, message,
                  started, finished, duration_s}] — status/finished are
                  null for a phase that never completed; an absent phase
                  never started (unconfigured hooks, verify disabled, or
                  the run aborted earlier). Phase names and statuses are
                  the closed vocabularies from the event protocol
                  (statuses: pass/warn/fail/skipped).
verify            {fail_count, warn_count, skipped_count,
                  checks: [{bucket, name, status, message, detail}]} | null
aur               {exit_code, skipped, skip_reason,
                  failures: [{package, last_lines}],   # last 20 lines
                  quarantine: [{package, version, status, failure_count,
                  retry_after, last_error}]} | null    # retry_after is UTC
hooks             {pre: [...], post: [...]} of {command, status, exit_code}
                  (status: pass/fail/timeout). Note: hook commands are
                  your shell one-liners verbatim — they can embed secrets
                  (e.g. webhook URLs). Records stay in the private
                  (0700) state dir; mind what you paste into bug reports.
config_rewritten  bool
```

---

## `archward doctor`

One-shot **read-only** self-diagnostic — the first command to run (and the
output to attach) when archward misbehaves on a box.

```
archward doctor [--json]
```

Checks: archward identity (version, install location, interpreter),
distro, config validity (parse errors, invalid sections falling back to
defaults, unreadable/root-owned files), sudo availability, askpass
discovery (including a broken `privilege.askpass` override and untrusted
`$SUDO_ASKPASS`), pacman + pacman-contrib tooling, pacman DB lock
(live-holder vs stale), disk space, cache rollback safety, state-dir
permissions/writability, AUR helper, stale `services.to_verify` entries,
GUI stack (PySide6 import **and** Qt platform-plugin initialization —
a box can import PySide6 fine and still fail at GUI launch), and the
last recorded run.

Doctor changes nothing: no config writes, no directory creation, no
password prompts (`sudo -n` only — it never triggers askpass). Checks
that hit an error or hang are reported as rows; the report always
completes. Where a problem has a known fix, the row's detail names the
exact command.

Severities intentionally mirror the pipeline's own: a condition the
pre-flight treats as WARN (cache DANGEROUS) or FAIL (DB lock, low disk)
gets the same status here.

| Flag | Effect |
|---|---|
| `--json` | Emit the checks as a JSON document instead of the table. |

`--json` is a stable scripting surface like the run-record document:

```
schema_version    1 — increments only on a rename/removal/shape change.
                  New fields may be ADDED without a bump: consumers must
                  ignore unknown keys.
checks            [{name, status, message, detail}] — status is the
                  closed vocabulary pass/warn/fail/skipped; detail may
                  be null.
```

### Exit codes

```
0  all checks pass
1  one or more FAIL
2  warnings only (no FAIL)
```

---

## Subcommand exit-code summary

```
0  success / user declined a confirmation (a valid choice)
1  operation failed (verify FAIL, pacman -U non-zero, restore error)
2  invalid args or refused (boot-critical without --confirm, unknown
   filename/package, unknown pacnew action, no AUR helper found);
   archward gates, archward doctor: warnings only (no FAIL)
3  snapshot / run record not found or incomplete
```

---

## Profiles + subcommands

`--profile NAME` works with subcommands the same as the flag form, but
it must come **before** the subcommand:

```bash
archward --profile lab verify
archward --profile lab snapshot list
archward --profile lab rollback package 2026-05-15_134329 mesa
```

Each profile has its own snapshot directory + config, so
`--profile lab snapshot list` lists the lab profile's snapshots, not
the default config's.
