# User-defined hooks

archward lets you wire arbitrary shell commands into two pipeline
checkpoints — **pre-update** (before `pacman -Syu` runs) and **post-verify**
(after the verify phase completes). Hooks are how you extend archward's
generic safe-update pipeline with checks that are specific to your machine,
your network, or your workflow.

This guide covers:

- [What hooks are for](#what-hooks-are-for)
- [Where to configure](#where-to-configure)
- [The OK/WARN message pattern](#the-okwarn-message-pattern)
- [Pre-update hooks: gating the update](#pre-update-hooks-gating-the-update)
- [Post-verify hooks: health checks beyond `systemctl is-active`](#post-verify-hooks-health-checks-beyond-systemctl-is-active)
- [Safety knobs](#safety-knobs)
- [Worked examples](#worked-examples)

---

## What hooks are for

archward's built-in pipeline covers the **universal** safe-update concerns:
snapshot freshness, disk space, pacman lock state, kernel-running vs
installed match, `.pacnew` detection, and `systemctl is-active` for any
service you put in `services.to_verify`. That works for any Arch-based
machine without configuration.

What it doesn't cover is anything specific to *your* setup:

- Is your **external backup** of the things archward can't snapshot
  (databases, remote hosts, encrypted volumes) fresh enough that you have
  a rollback path?
- Are your **network services** actually reachable, not just running?
  `systemctl is-active nginx` returns active the instant the process starts;
  it doesn't know whether nginx's listener is bound, whether the firewall
  is letting traffic through, or whether the upstream that nginx fronts is
  responding.
- Are your **mounts** present? archward doesn't iterate `/proc/mounts`.
- Are you about to update during a **known-bad time window** (a maintenance
  window, a backup run, a streaming session) where you don't want pacman
  competing for bandwidth or interrupting another process?

These are perfect hook material: small shell commands that report a
machine-readable signal back to archward.

---

## Where to configure

Either:

- **GUI**: Preferences → Hooks tab. Two text areas (one command per line),
  a per-hook timeout, and a "fail pipeline on error" checkbox.
- **TOML**: `~/.config/archward/config.toml`, `[hooks]` section.

Both surfaces edit the same data. Pick whichever feels right. The TOML
shape:

```toml
[hooks]
pre_update = [
    "command 1",
    "command 2",
]
post_verify = [
    "command 3",
    "command 4",
]
timeout_seconds = 60
fail_pipeline_on_error = false
```

Each command runs via `/bin/sh -c <command>`, so shell features (pipes,
variable expansion, redirection, `&&`/`||`) all work without quoting
gymnastics. The environment includes everything from archward's process
plus an `ARCHWARD_PHASE` variable set to `hooks_pre` or `hooks_post` so a
single shared script can branch on which checkpoint invoked it.

---

## The OK/WARN message pattern

A hook's stdout becomes the **Message** column in the Verify view's
"hooks" bucket and lands in `archward.log` for post-mortems. So your hook
should always echo a short human-readable line saying what it found, in
both the success and failure paths:

```sh
<check command> && echo "OK: <human description>" || echo "WARN: <what's wrong>"
```

This pattern:

- Prints **OK: …** on the success path so the Verify view shows something
  meaningful instead of "(no output)".
- Prints **WARN: …** on the failure path so the user knows exactly what
  went wrong without reading the full log.
- Keeps the overall exit code at **0** (the `|| echo` swallows the failure),
  so the hook is *informational only* and doesn't trigger
  `fail_pipeline_on_error`. The "WARN: …" text is the signal, not the
  exit code.

If you want a hook to **actually enforce** (abort the pipeline on failure)
instead of just warning, add an explicit `; exit 1` after the WARN echo:

```sh
<check> && echo "OK: ..." || { echo "WARN: ..."; exit 1; }
```

…and set `fail_pipeline_on_error = true` in `[hooks]`. See
[Safety knobs](#safety-knobs) below.

---

## Pre-update hooks: gating the update

Pre-update hooks run **after** archward classifies risk and the user
approves it, but **before** pacman is invoked. They're the right place
for "should this update happen *at all*?" checks.

Two common patterns:

### 1. External-state freshness (the canary effect)

If you maintain an external resource that's the rollback path for things
archward can't snapshot — a database dump on a remote host, an encrypted
tarball on a backup drive, a snapshot on a NAS — gate the update on its
freshness.

The point isn't just "is the backup fresh enough that this specific update
is safe to roll back?" It's that **if the backup pipeline ever silently
breaks, this hook is the first loud signal**. Without a freshness gate,
the backup script can fail for weeks while you keep updating; the day you
actually need the backup is the day you find out it's stale. With the
gate, the *first* day after a backup failure is the day archward refuses
to update, and you investigate same-day.

Generic pattern, using `find -mmin -N` for "any file in this directory
modified in the last N minutes":

```sh
find /path/to/backup/dir -mmin -1560 -type f 2>/dev/null | grep -q . \
  && echo "OK: backup is fresh (< 26h)" \
  || { echo "WARN: backup is stale (> 26h or path missing)"; exit 1; }
```

(`1560 minutes = 26 hours`. Sized to allow a nightly cron + 1h jitter.)

Pair with `fail_pipeline_on_error = true` so a stale backup actually
refuses the update instead of just warning.

### 2. Maintenance-window blackout

If another scheduled process runs at a known time and competes for
bandwidth, IO, or CPU, gate the update against the clock. Useful for:

- Bandwidth-shared overnight backups uploading to off-site storage.
- Streaming or recording sessions that shouldn't be interrupted.
- Cron jobs that hold a lock you don't want archward racing for.

```sh
h=$(date +%-H); m=$(date +%-M)
if { [ "$h" -eq 20 ] && [ "$m" -ge 30 ]; } || { [ "$h" -eq 21 ] && [ "$m" -le 15 ]; }; then
    echo "WARN: in maintenance window (20:30-21:15)"
else
    echo "OK: outside maintenance window"
fi
```

Default form above is *informational* (always exits 0; the WARN is just
text). Add `; exit 1` after the WARN line to make it enforcing.

Blackout windows tend to read better as informational rather than
enforcing — false positives ("I really do want to update at 20:45 just
this once") are more annoying than the cost of letting the update slip
through with a WARN.

---

## Post-verify hooks: health checks beyond `systemctl is-active`

Post-verify hooks run after archward's universal verify finishes. They
never abort the pipeline (the update has already happened); their job is
to surface *additional* health signals into the Verify view's hooks
bucket so you see them at completion without hunting through journalctl.

Universal verify checks `systemctl is-active <unit>` for every service
in `services.to_verify`. That tells you the **process** is running. It
doesn't tell you:

- The HTTP/TCP listener is bound to the right address.
- The HTTP service is responding to requests (vs being wedged on startup).
- A boot-time one-shot's success is still relevant (services with
  `Type=oneshot RemainAfterExit=yes` stay `active (exited)` forever after
  their first success, even if the underlying resource is now broken).
- The network interface a daemon depends on is still up.

These are the gaps post-verify hooks fill.

### HTTP health probes

`systemctl is-active jellyfin` returns active the moment the systemd unit
starts; the HTTP layer may still be 5-10 seconds away from accepting
requests, or could be wedged on a slow database migration after the
update. A direct HTTP probe catches this gap:

```sh
curl -sf --max-time 5 http://localhost:8096/health >/dev/null \
  && echo "OK: service HTTP responding" \
  || echo "WARN: service HTTP down"
```

Replace the URL with whatever endpoint the service exposes. Many services
have a `/health` or `/status` endpoint specifically for this kind of probe;
a plain `GET /` works too if the service returns a non-error status for
unauthenticated requests.

### Mountpoint checks

`systemctl is-active mnt-data.mount` tells you systemd thinks the mount
is up. `mountpoint -q` directly asks the kernel:

```sh
mountpoint -q /mnt/data \
  && echo "OK: /mnt/data mounted" \
  || echo "WARN: /mnt/data not mounted"
```

Useful for: NFS shares, FUSE-mounted encrypted volumes, USB-attached
backup drives, any mount that can silently go away (network blip, USB
disconnect, daemon restart) without the underlying systemd unit changing
state.

### Network interface presence

When a service depends on a specific virtual interface (VPN tunnels,
overlay networks, bridge devices), checking the daemon is active isn't
enough — the interface itself can be torn down by an unrelated event
while the daemon stays running.

```sh
ip link show wg0 >/dev/null 2>&1 \
  && echo "OK: wg0 interface present" \
  || echo "WARN: wg0 interface missing"
```

### Real-time reachability vs boot-time success

Some systemd units (e.g. `wait-for-network-online`, `wait-for-vpn`) are
`Type=oneshot RemainAfterExit=yes` — they run a check at boot, succeed,
and stay `active (exited)` forever after. `systemctl is-active` on them
tells you "the check passed *at boot*", not "the check would pass right
now."

If the underlying resource can go away post-boot (your VPN reconnects,
your DHCP lease lapses, your DNS server stops responding), a real-time
probe in a post-verify hook is the only way to catch it:

```sh
ping -c 1 -W 3 10.0.0.1 >/dev/null 2>&1 \
  && echo "OK: gateway reachable" \
  || echo "WARN: gateway unreachable"
```

The `-W 3` caps the wait at 3 seconds — important so a hung target doesn't
stretch the hook into the timeout.

### Specific bind verification

A daemon can be running but bound to the wrong addresses (default
`0.0.0.0` instead of a specific LAN interface, IPv6-only when you wanted
both, etc.). `ss -tln` lists every listening socket:

```sh
ss -tln 2>/dev/null | grep -qE "10\.0\.0\.2:22" \
  && echo "OK: sshd bound to LAN" \
  || echo "WARN: sshd not bound to LAN (10.0.0.2:22)"
```

Useful when a config change (intentional or .pacnew-merge accident)
could silently change the bind address — `systemctl is-active sshd` would
still say active.

---

## Safety knobs

### `timeout_seconds`

Per-hook timeout. Default 60s. A hook waiting on a hung network target
would otherwise lock the pipeline indefinitely. Tune up for legitimately
slow hooks (rsync over a slow link, a long-running database backup
trigger); keep default for snappy checks.

Hook stdout up to the timeout is still captured; the hook's HookResult
gets `status=TIMEOUT` and is treated as a failure for
`fail_pipeline_on_error` purposes.

### `fail_pipeline_on_error`

Default `false`. When `true`, any pre-update hook that exits non-zero
aborts the pipeline before pacman runs. Post-verify hooks ignore this
flag — they never abort, even on failure, because the update has already
happened.

The flag is global (applies to all pre-update hooks at once), so mix
enforcing and informational pre-hooks by controlling each hook's exit
code:

- **Informational** hook: always exit 0; use OK/WARN text as the signal.
- **Enforcing** hook: exit 1 on failure (typically `{ echo "WARN: ..."; exit 1; }`).

With the flag on, informational hooks stay informational (they exit 0 so
the flag never trips), while enforcing hooks actually enforce.

### Idempotence

Hooks should be idempotent. A pre-update hook might run, fail, abort the
pipeline; the user fixes the underlying issue and reruns archward; the
hook runs again. If your hook has side effects (writes a log file,
creates a flag file, triggers a remote action), make sure running it
twice is the same as running it once.

### What's safe to put in pre-update vs post-verify

- **Pre-update is for checks that should gate the update.** Don't put
  side-effects here (triggering a backup, sending a "starting" notification)
  unless they're cheap and reversible — if a later hook in the pre-update
  list aborts, the side-effect already fired.
- **Post-verify is for inspection and notification.** Put side-effects
  here when you want them to run after the update is committed: sending
  a "finished" notification, kicking off a downstream rebuild, recording
  the run in an external system.

---

## Worked examples

A complete starter set for a developer workstation with a few self-hosted
services. Drop into Preferences → Hooks or `[hooks]` in `config.toml`:

```toml
[hooks]
# Set to true once the pre-update gates are tuned to your environment.
fail_pipeline_on_error = false
timeout_seconds = 60

pre_update = [
    # Refuse update if no recent backup tarball in /mnt/backup/daily/.
    # 1560 min = 26h (nightly backup + 1h slack).
    'find /mnt/backup/daily/ -mmin -1560 -type f 2>/dev/null | grep -q . && echo "OK: backup fresh (< 26h)" || { echo "WARN: backup stale (> 26h or path missing)"; exit 1; }',

    # Informational warning during a maintenance window (20:30-21:15).
    'h=$(date +%-H); m=$(date +%-M); if { [ "$h" -eq 20 ] && [ "$m" -ge 30 ]; } || { [ "$h" -eq 21 ] && [ "$m" -le 15 ]; }; then echo "WARN: in maintenance window (20:30-21:15)"; else echo "OK: outside maintenance window"; fi',
]

post_verify = [
    # HTTP health probe — catches "service active but HTTP wedged".
    'curl -sf --max-time 5 http://localhost:8096/health >/dev/null && echo "OK: Jellyfin HTTP responding" || echo "WARN: Jellyfin HTTP down"',

    # Mountpoint — catches NFS / FUSE / USB drop-outs that leave the systemd unit intact.
    'mountpoint -q /mnt/backup && echo "OK: /mnt/backup mounted" || echo "WARN: /mnt/backup not mounted"',

    # Network interface — catches VPN/overlay teardown that leaves the daemon active.
    'ip link show wg0 >/dev/null 2>&1 && echo "OK: wg0 interface present" || echo "WARN: wg0 interface missing"',

    # Real-time reachability — catches "boot-time check passed but resource now down".
    'ping -c 1 -W 3 10.0.0.1 >/dev/null 2>&1 && echo "OK: gateway reachable" || echo "WARN: gateway unreachable"',

    # Specific bind — catches sshd binding to wrong address after a config change.
    'ss -tln 2>/dev/null | grep -qE "10\\.0\\.0\\.2:22" && echo "OK: sshd bound to LAN" || echo "WARN: sshd not bound to LAN (10.0.0.2:22)"',
]
```

Walk through this list and delete anything that doesn't apply to your
machine. Replace IPs / paths / ports / interface names with yours.
Test each hook's success and failure path manually before flipping
`fail_pipeline_on_error = true`:

```sh
/bin/sh -c '<paste hook here>'
echo "exit: $?"
```

When everything looks right and the OK paths fire cleanly during normal
runs, flip `fail_pipeline_on_error = true` to enable enforcement.

---

## Seeing hook results in the GUI

After each run with hooks configured:

- The **phase rail** shows `Pre-hooks` and `Post-hooks` rows with pass/warn/
  fail icons summarizing the batch.
- The **Verify view** has a third bucket `hooks` listing every hook with
  its command (truncated; full command in the tooltip), exit code, status,
  and the last line of its output. Multi-line output is attached as a
  child node — click to expand.
- The **log pane** at the bottom streams each hook's stdout/stderr line
  by line in real time as the hook runs.
- `~/.local/state/archward/logs/archward.log` records every hook event
  (start, command, output, exit code) for post-mortem grepping after the
  GUI session has ended.
