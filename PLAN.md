# Plan: archward — Safe-update GUI for Arch-based Linux distributions

## Context

Rob currently maintains three bash scripts at `/home/rob/bin/` (`pre-update-snapshot.sh`, `system-update.sh`, `post-update-verify.sh`) that implement a "safe system update pipeline" for EndeavorMain. The pipeline works well — months of refinement, robust gates, snapshot-based rollback reference, RESULT-tagged output. But it's full of EndeavorMain-specific hardcoding: VPN gateway IPs, Jellyfin health probes, Liberty Analytics backup-freshness gate, NFS mount checks, ZeroTier interface names, CachyOS-BORE-specific kernel package, MEDIUM-risk pattern list tuned to his media-server services.

The goal is a clean Python/PySide6 GUI rewrite — `archward` — that generalizes the locked-in behavior to any Arch-based machine (Arch, EndeavourOS, Manjaro, CachyOS, Garuda, Artix), integrates AUR via auto-detected helpers (yay/paru/aurutils), and ships as an AUR package. The bash scripts are the **reference implementation for behavior** but archward is a greenfield rewrite, not a port. Implementation will happen in a fresh Claude conversation; this plan must be self-contained.

**Locked decisions:**
- Name: **archward** (Arch + ward = guard against bad updates). AUR collision check confirmed free.
- Stack: Python 3.11+ with PySide6 (Qt6). Mirrors Rob's liberty-books patterns at `~/dev/liberty-books/`.
- UI: native desktop GUI (single QMainWindow with phase rail + log pane).
- AUR: integrated second phase, auto-detects helper (yay > paru > aurutils), skips gracefully if none found, `--no-aur` flag opts out.
- Config: single TOML at `~/.config/archward/config.toml`, auto-detect populates on first run.
- License: GPL-3.0 (matches `endeavoring-conky/LICENSE`).
- v1 verify scope: ONLY universal checks (kernel match, .pacnew, disk, pacman log) + `systemctl is-active` for opt-in service list. **No network probes, HTTP health, mountpoint checks** — those are EndeavorMain-specific and reserved for v2 hooks.
- Public repo: `git@github.com:indyfive11/archward.git`.

---

## Project structure (src-layout)

```
archward/
  pyproject.toml              # hatchling build, gui-scripts entry point
  README.md
  LICENSE                     # GPL-3.0 (copy from endeavoring-conky)
  CHANGELOG.md
  .gitignore
  packaging/
    archward.desktop          # XDG desktop entry
    archward.svg              # placeholder icon
    PKGBUILD                  # AUR PKGBUILD
  docs/
    architecture.md
    config.md
    development.md
    snapshots.md
  src/archward/
    __init__.py               # __version__
    __main__.py               # python -m archward → cli.main()
    cli.py                    # argparse: --no-aur/--dry-run/--auto/--detect/--yes
                              # Console entry: `archward` → CLI pipeline.
                              # GUI entry: `archward-gui` (separate gui-script) → launches Qt.
                              # No --gui flag — picking the binary picks the mode.
    app.py                    # composition root, wires Pipeline + EventBus + UI/CLI consumer
    events.py                 # PhaseEvent, EventBus (pure Python, Qt-agnostic)
    logging_setup.py          # rotating file log + Qt log-pane handler
    models/
      snapshot.py             # SnapshotMeta, Snapshot
      update.py               # PendingUpdate, RiskLevel
      gate.py                 # Gate, GateResult, GateStatus
      pacnew.py               # PacnewFile, PacnewStrategy
      verify.py               # VerifyCheck, VerifyResult, CheckStatus
      config.py               # ConfigModel (mirrors TOML schema)
    config/
      paths.py                # XDG dirs (config/data/cache/state)
      loader.py               # tomllib read, tomli-w write, schema migration
      defaults.py             # baseline TOML written on first run
      detect.py               # distro, kernels, services, helper, pacnew baseline
    pacman/
      query.py                # pacman -Q*, checkupdates wrappers
      runner.py               # streaming sudo pacman -Syu
      pacnew.py               # find/classify/render-diff/apply-strategy
    aur/
      helper.py               # AurHelper Protocol, factory
      adapters/
        yay.py                # yay -Qua, yay -Sua --noconfirm
        paru.py
        aurutils.py
    pipeline/
      pipeline.py             # orchestrates 8 phases, cancellable, emits events
      snapshot.py
      gates.py                # snapshot age + disk only (v1)
      risk.py                 # classify pending updates HIGH/MEDIUM/LOW
      update_official.py
      update_aur.py
      pacnew_phase.py
      verify_phase.py         # bucket A (universal) + bucket B (services)
      report.py               # final summary, RESULT: tags for CLI
      hooks.py                # v2 seam — no-op stub in v1
    privilege/
      sudo.py                 # AskpassStrategy, PkexecStrategy, PersistentSudoStrategy
    system/
      disk.py                 # df / statvfs
      kernel.py               # uname + installed-pkg comparison
      services.py             # systemctl wrappers
      distro.py               # /etc/os-release parser
    ui/
      main_window.py          # QMainWindow: phase rail + content + log pane + status bar
      phase_rail.py           # left-side phase indicator (status icon + name)
      log_pane.py             # collapsible monospace stream with phase filter
      qt_bus.py               # bridges events.EventBus → Qt signals (QueuedConnection)
      theme.py, icons.py
      views/
        snapshot_view.py
        gates_view.py
        risk_view.py          # 3-column tree (HIGH/MEDIUM/LOW), HIGH highlighted
        update_view.py        # shared by official + AUR
        pacnew_view.py
        diff_dialog.py        # difflib unified_diff + QSyntaxHighlighter
        verify_view.py
        result_view.py
      dialogs/
        preferences.py        # tabbed dialog: General/Gates/Risk/Services/Pacnew/AUR/Privilege/Advanced
        sudo_prompt.py        # fallback if no askpass binary found
  tests/
    unit/                     # pytest, fast, no system mutation
      test_risk.py
      test_gates.py
      test_pacnew_strategy.py
      test_config_loader.py
      test_aur_adapter_yay.py
      test_snapshot_writer.py
    integration/
      Dockerfile.archtest     # archlinux:latest dry-run pipeline
      test_pipeline_dryrun.py
    fixtures/
      pacman_output/
      checkupdates_output/
      yay_qua_output/
      config_samples/
```

### Critical files (write in dependency order)

1. `src/archward/models/config.py` — TOML schema as Pydantic models
2. `src/archward/config/detect.py` — first-run detection
3. `src/archward/privilege/sudo.py` — sudo strategy chooser
4. `src/archward/pipeline/pipeline.py` — phase orchestrator
5. `src/archward/ui/main_window.py` — Qt entry point

### Reference files (read-only — informational)

- `/home/rob/bin/system-update.sh` — gates, risk classification, pacnew strategy
- `/home/rob/bin/pre-update-snapshot.sh` — what to capture
- `/home/rob/bin/post-update-verify.sh` — universal vs Rob-specific checks
- `/home/rob/dev/liberty-books/main.py` and `~/dev/liberty-books/ui/` — PySide6 patterns
- `/home/rob/dev/endeavoring-conky/README.md` + `LICENSE` — repo conventions

---

## Core data model (Pydantic v2, all in `src/archward/models/`)

All models use `model_config = ConfigDict(frozen=True)` where mutability isn't required, so they're safe to pass across the `QThread` boundary.

### `update.py`
```python
class RiskLevel(StrEnum):
    HIGH = "high"; MEDIUM = "medium"; LOW = "low"

class PendingUpdate(BaseModel):
    name: str
    old_version: str
    new_version: str
    source: Literal["official", "aur"]
    risk: RiskLevel
    is_kernel: bool = False
    reason: str | None = None       # e.g. "matched risk.high"
```

### `snapshot.py`
```python
class SnapshotMeta(BaseModel):
    snapshot_id: str                # "2026-05-14_093015"
    created_at: datetime
    path: Path
    distro_id: str                  # from /etc/os-release
    kernel_release: str             # uname -r
    free_disk_gb: int
    helper_detected: str | None

class Snapshot(BaseModel):
    meta: SnapshotMeta
    package_files: Mapping[str, Path]    # accepts dict at construction, stored read-only
    config_files: tuple[Path, ...]       # tuple — Pydantic v2 enforces immutability
    service_files: Mapping[str, Path]
    age_seconds: int
```

**Cross-thread safety.** `frozen=True` on the model class blocks attribute reassignment
but does **not** deep-freeze mutable containers (`list`, `dict`). For fields handed
across the `QThread` boundary we use `tuple` and `Mapping` (Pydantic accepts lists/dicts
at construction time and stores as immutable forms). Treat received models as
strictly read-only in UI code; the pipeline thread is the sole writer.

### `gate.py`
```python
class GateStatus(StrEnum):
    PASS = "pass"; WARN = "warn"; FAIL = "fail"; SKIPPED = "skipped"

class GateResult(BaseModel):
    name: str
    status: GateStatus
    message: str
    detail: str | None = None
    can_override: bool = False      # GUI shows "Proceed anyway?" when True
```

### `pacnew.py`
```python
class PacnewRecommendation(StrEnum):
    """What the rule recommends — derived from config.pacnew.rules at scan time."""
    KEEP_OURS = "keep_ours"; TAKE_NEW = "take_new"; REVIEW_NEEDED = "review_needed"

class PacnewAction(StrEnum):
    """What the user ultimately chose at runtime — passed into apply_strategy()."""
    KEEP_OURS = "keep_ours"; TAKE_NEW = "take_new"; EDIT = "edit"; LEAVE = "leave"

class PacnewFile(BaseModel):
    path: Path
    original_path: Path
    recommendation: PacnewRecommendation     # from rules
    rule_pattern: str | None
    note: str | None
    detected_at: datetime
```

Separation rationale: the *recommendation* comes from the matched TOML rule and is
the only value persisted; the *action* the user takes at runtime (which may include
`edit`/`leave`, both of which have no equivalent rule) is a transient choice.

### `verify.py`
```python
class CheckStatus(StrEnum):
    PASS = "pass"; WARN = "warn"; FAIL = "fail"

class VerifyCheck(BaseModel):
    bucket: Literal["universal", "services"]
    name: str
    status: CheckStatus
    message: str
    detail: str | None = None

class VerifyResult(BaseModel):
    checks: tuple[VerifyCheck, ...]      # tuple for immutability across QThread boundary
    fail_count: int
    warn_count: int
    reboot_needed: bool
```

### `events.py` (pipeline-side, not in models/)
```python
class PhaseEventKind(StrEnum):
    PHASE_START = "phase.start"
    PHASE_PROGRESS = "phase.progress"
    PHASE_LOG = "phase.log"
    PHASE_RESULT = "phase.result"
    PIPELINE_DONE = "pipeline.done"

class PhaseEvent(BaseModel):
    kind: PhaseEventKind
    phase: str
    message: str | None = None
    payload: dict[str, Any] | None = None
    timestamp: datetime
```

---

## TOML schema (`~/.config/archward/config.toml`)

Validated by `ConfigModel`. Loader uses stdlib `tomllib`; writer uses `tomli-w`. `schema_version` enables future migrations.

```toml
schema_version = 1

[general]
snapshot_dir = "~/.local/state/archward/snapshots"
keep_snapshots = 10
log_dir = "~/.local/state/archward/logs"
keep_logs = 20

[gates]
snapshot_max_age_minutes = 60
min_disk_gb = 5
allow_override = true

[risk]
high = [
  "glibc", "lib32-glibc", "systemd", "systemd-libs",
  "openssl", "lib32-openssl",
  "mesa", "lib32-mesa",
  "pipewire", "pipewire-pulse", "wireplumber",
  "openssh",
  # Auto-detected kernel packages appended on first run
]
medium_patterns = [
  "*-server", "docker*", "qemu*", "libvirt*",
  "postgresql*", "mariadb*", "nginx*", "apache*",
]
# kernel_patterns matches both the kernel itself AND its -headers package so DKMS
# rebuilds always classify HIGH. linux-cachyos* greedily catches -bore, -headers, -zfs etc.
# Exclude linux-firmware*, linux-docs* (they're not boot-critical).
kernel_patterns = [
  "linux", "linux-headers",
  "linux-lts", "linux-lts-headers",
  "linux-zen", "linux-zen-headers",
  "linux-hardened", "linux-hardened-headers",
  "linux-cachyos*",
  "linux-api-headers",
]
kernel_pattern_exclude = ["linux-firmware*", "linux-docs*"]

[services]
to_verify = []                      # auto-populated on first run

[services.severity]
# "sddm.service" = "watch"          # default is "critical"

[pacnew]
default_strategy = "review_needed"

[[pacnew.rules]]
pattern = "*sshd_config*"
strategy = "review_needed"
note = "SSH daemon config — review carefully"

[[pacnew.rules]]
pattern = "*mirrorlist*"
strategy = "keep_ours"
note = "Keep your rate-tested mirror order"

[[pacnew.rules]]
pattern = "*pacman.conf*"
strategy = "review_needed"
note = "Pacman options — review for new repos/IgnorePkg"

[[pacnew.rules]]
pattern = "*/fstab*"
strategy = "review_needed"
note = "Filesystem mounts — review before next boot"

[[pacnew.rules]]
pattern = "*/grub*"
strategy = "review_needed"
note = "Bootloader config — review before next boot"

[[pacnew.rules]]
pattern = "*resolved.conf*"
strategy = "keep_ours"
note = "DNS / DoT customizations frequently diverge from upstream"

[[pacnew.rules]]
pattern = "*faillock.conf*"
strategy = "keep_ours"
note = "Account lockout policy — preserve your tuned values"

[[pacnew.rules]]
pattern = "*/sysctl.d/*"
strategy = "keep_ours"
note = "Kernel hardening params — preserve your tuned values"

[[pacnew.rules]]
pattern = "*.hook"
strategy = "take_new"
note = "Pacman hooks usually track upstream"

[aur]
enabled = true
helper_preference = ["yay", "paru", "aurutils"]
skip = false

[pacman]
noconfirm = true
extra_args = []

[verify]
enabled = true
reboot_log = "/var/log/reboot-recommendation-trigger.log"  # optional, "" disables

[gui]
confirm_high_risk = true
log_autoscroll = true
theme = "system"

[privilege]
mode = "auto"                       # auto|askpass|pkexec|persistent_sudo
askpass = ""                        # override path; default auto-discovers

# v2 RESERVED — DO NOT USE YET
# [hooks]
# pre_update = []
# post_verify = []
# [profile.workstation] ...
```

Loader semantics:
- On `ValidationError`, log the offending key path; fall back to defaults for that section only — don't silently nuke the whole config.
- Hand-edited files are never silently overwritten. Writer triggers only when user changes via Preferences dialog.

---

## Auto-detection (`config/detect.py`)

Runs in 3 contexts only: first-launch bootstrap, Preferences "Re-detect" button, `archward --detect` CLI flag. **Never on the hot path.**

```python
def detect_distro() -> DistroInfo:
    """Parse /etc/os-release. is_arch_based logic:
      1. If ID in {arch, endeavouros, manjaro, cachyos, garuda, artix} → True (named).
      2. Else if 'arch' appears in ID_LIKE (whitespace-separated) → True (compatible).
         Display as "Arch-compatible (detected via ID_LIKE)".
      Catches SteamOS 3, RebornOS, ArcoLinux, BlendOS etc. without an explicit allow-list."""

def detect_kernels() -> list[str]:
    """pacman -Qq filtered by ^linux(-|$), excluding -headers/-firmware/-docs.
    Returns: ['linux', 'linux-lts'] etc."""

def detect_aur_helper(preference: list[str]) -> str | None:
    """shutil.which in preference order. First match wins."""

def detect_active_enabled_services() -> list[str]:
    """systemctl list-unit-files --state=enabled --type=service,
    intersected with is-active. Filter out getty@/user-units clutter."""

def detect_pacnew_baseline() -> list[Path]:
    """Existing .pacnew files (via sudo find /etc) so first verify ignores stale ones."""

def run_full_detection(cfg: ConfigModel) -> DetectionResult: ...
```

Merge semantics (when re-running detection over an existing config):
- `risk.high` — UNION with detected kernels; never remove user entries.
- `services.to_verify` — proposed as a diff in the Preferences dialog; user opts in.
- `aur.helper_preference` — left alone; only flip `aur.enabled = false` if no helper found AND user hasn't explicitly enabled.

---

## Pipeline phases

Phase order, all emitting `PhaseEvent`s via `EventBus`:

```
1. Snapshot         capture universal state to ~/.local/state/archward/snapshots/<id>/
2. Gates            snapshot age + disk space (v1)
3. Risk             classify pacman + AUR pending; user reviews & approves
4. Update official  sudo pacman -Syu --noconfirm  (streamed)
5. Update AUR       yay/paru -Sua --noconfirm  (skipped if no helper or --no-aur)
6. Pacnew           find /etc -name '*.pacnew' since snapshot; classify; user resolves
7. Verify           universal + services checks
8. Report           summary + RESULT: tag → CLI stdout / GUI banner
```

Cancel via a `threading.Event` checked at phase boundaries and per-line during streaming. See "Cancellation contract" below for the exact semantics during the update phase.

### Cancellation contract

The pipeline must **never** kill a running pacman or AUR helper subprocess. A
half-finished pacman transaction leaves `db.lck` orphaned and may require manual
recovery (`pacman -Dk`). The contract below covers the three places cancel can
originate:

| Origin | During pre-update phases (snapshot/gates/risk/pacnew/verify) | During update_official or update_aur |
|---|---|---|
| GUI "Cancel" button | Sets `cancel_event`; pipeline aborts at next phase boundary. `RESULT:UPDATE_FAILED` with "Cancelled by user". | Button is *disabled* during these phases. A modal "Cancel is unavailable while pacman is running" appears if the user tries to close the window. |
| `Ctrl+C` (CLI mode) | SIGINT handler sets `cancel_event`. Default Python SIGINT (KeyboardInterrupt) is **not** allowed to propagate during update phase. | SIGINT prints "waiting for pacman to finish — db.lck must release cleanly" and continues. Second Ctrl+C does the same; only `kill -9` of archward (orphaning pacman) can interrupt. |
| `QMainWindow.closeEvent` | Calls cancel and exits cleanly. | Refuses close with a modal until update phase finishes. |

Per-line cancel checks during streaming exist only to **suppress further UI updates**
(stop appending to log pane, freeze phase rail). They do not terminate the subprocess.

### Pre-flight (runs before phase 1, not user-overridable)

Two hard preconditions checked before any pipeline phase starts. Both can short-circuit
to `RESULT:UPDATE_FAILED` with a clear message.

- **archward single-instance** — `QLockFile` at `~/.local/state/archward/archward.lock`
  (stale-detection enabled, 30s timeout). If another archward owns the lock, abort
  with the owning PID surfaced in the error.
- **pacman database lock** — check for `/var/lib/pacman/db.lck`. If present, read the
  file (it contains the holding PID), look up `/proc/<pid>/comm` to identify the owner
  (`pacman`, `yay`, `paru`, etc.), surface the owner + PID, abort. **Do not** auto-remove
  the lock file: a stale lock from a killed pacman process indicates a possibly
  corrupted transaction that needs manual investigation (`pacman -Dk` or similar).

### Gates (v1: only two)

- **Snapshot age** — find latest in `general.snapshot_dir`, read `.timestamp`, fail if older than `gates.snapshot_max_age_minutes`. Universal.
- **Disk space** — `df -BG /`, fail if free < `gates.min_disk_gb`. Universal.

The EndeavorMain-only backup-freshness and update-window-blackout gates from system-update.sh are **dropped from v1**. They can be reintroduced as v2 hooks.

### Risk classification (`pipeline/risk.py`)

For each `PendingUpdate`:
1. If `name` in `config.risk.high` → HIGH (reason: `"in risk.high"`)
2. Else if `name` matches any of `config.risk.kernel_patterns` (fnmatch) AND does **not** match `config.risk.kernel_pattern_exclude` → HIGH (reason: `"kernel pattern"`, `is_kernel=True`)
3. Else if `name` matches any of `config.risk.medium_patterns` → MEDIUM (reason: `"medium pattern <pat>"`)
4. Else → LOW

Same classifier runs on official + AUR updates; the `source` field distinguishes them in the GUI.

### Transaction preview (runs alongside risk)

Before showing the Risk view, `pipeline/risk.py` also runs `pacman -Sup --print-format
'%n %v'` (no `--noconfirm` needed; `-p` makes it non-mutating) to produce a
`TransactionPreview` describing operations pacman *would* perform:

```python
class TransactionPreview(BaseModel):
    replacements: tuple[tuple[str, str], ...]   # [(old_pkg, new_pkg), ...]
    provider_choices: tuple[ProviderChoice, ...] # ambiguous deps
    conflicts: tuple[str, ...]                   # warning lines from stderr
    total_count: int
    download_mb: int | None                       # parsed from "Total Download Size"
```

Surface non-empty `replacements`, `provider_choices`, and `conflicts` as distinct
banners in the Risk view above the HIGH/MEDIUM/LOW tree. **Rationale.** `pacman -Syu
--noconfirm` defaults `replaces=` to **No** and provider choices to alphabetical
first — both wrong answers in real upgrade paths. v1 doesn't resolve the choice;
it requires explicit user acknowledgment ("Proceed despite N replacements?") before
the update phase runs, and offers a "Cancel and run pacman manually" exit.

### Update phases

- **Official**: `privilege.sudo.run([...])`. Stdout streamed line-by-line into log pane
  via `PhaseEvent.PHASE_LOG`. Exit code captured.
- **AUR**: `aur.helper.run_update(ignore, emit_log)` — helper does its own sudo (inherits our `SUDO_ASKPASS`). Build failures scanned via regex on stdout (`==> ERROR:`, `failed to build`), captured as `BuildFailure` records with last 50 lines; pipeline continues to remaining packages.

#### Command flags (universal, applied to pacman + AUR helpers)

```
--noprogressbar          # the GUI's phase rail conveys progress; ASCII bars don't
                         # render correctly in a non-TTY log pane
--color=never            # avoid ANSI escapes in the captured stream
```

The pacman invocation is:
```
sudo pacman -Syu --noconfirm --noprogressbar --color=never [extra_args] [--ignore=<list>]
```

yay/paru accept the same `--noprogressbar --color=never` and forward them to their
inner pacman call. A belt-and-suspenders ANSI strip lives in the log pipeline (regex
`\x1b\[[0-9;]*[A-Za-z]`) for any output that slips through anyway.

#### Subprocess streaming recipe

```python
proc = subprocess.Popen(
    argv,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,    # merge so a single line stream represents progress
    bufsize=1,                    # line-buffered
    text=True,
    env={**os.environ,
         "LANG": "C",              # consistent locale, avoid translated parse pitfalls
         "SUDO_ASKPASS": askpass}, # askpass binary path from privilege.sudo
)
for line in proc.stdout:
    bus.emit(PhaseEventKind.PHASE_LOG, strip_ansi(line.rstrip()))
exit_code = proc.wait()
```

Choice: we use `subprocess.Popen` + `QThread` (not `QProcess`) because we need the
env override flexibility (`LANG`, `SUDO_ASKPASS`) and the explicit ANSI-strip pipeline.
`QProcess` would be simpler but less controllable. UI mirrors progress through the
phase rail and stream pane, both fed by `PhaseEvent`s on the EventBus.

### Pacnew phase

```python
def find_pacnew_files(since: datetime) -> list[Path]
def classify(path, rules, default) -> PacnewFile          # first fnmatch wins
def render_diff(orig, new) -> str                          # difflib.unified_diff
def apply_action(pacnew, action: PacnewAction) -> None
    # keep_ours → sudo rm <path>.pacnew
    # take_new  → see "take_new permission preservation" below
    # edit      → spawn $VISUAL/$EDITOR or kdiff3/meld
    # leave     → no-op
```

Default rules listed in TOML schema above.

#### take_new — preserves original file permissions and ownership

A naive `mv <orig>.pacnew <orig>` inherits the **.pacnew** file's mode (usually
644 root:root, since pacman writes it with the package's default permissions). When
the original was chmod 600 (sshd_config, sudoers.d/*, wireguard configs, shadow-like
files), this is a security regression: sensitive content becomes world-readable.

The actual sequence preserves perms:

```python
def apply_take_new(pacnew: Path) -> None:
    orig = pacnew.with_suffix('')                            # strips .pacnew
    st = orig.stat()
    backup = orig.with_suffix(orig.suffix + '.pre-archward.bak')
    privilege.sudo.run(['cp', '-a', str(orig), str(backup)]) # -a preserves perms
    privilege.sudo.run(['mv', str(pacnew), str(orig)])
    privilege.sudo.run(['chown', f'{st.st_uid}:{st.st_gid}', str(orig)])
    privilege.sudo.run(['chmod', f'{st.st_mode & 0o7777:o}', str(orig)])
```

Equivalent single-call form is `install -m <mode> -o <user> -g <group> .pacnew orig`
followed by `rm .pacnew`, but the explicit four-step sequence is auditable in the
log pane.

### Verify phase (v1 scope)

**Bucket A — Universal** (always run):
1. Kernel match — installed kernel pkg version vs `uname -r` substring. WARN: reboot needed.
2. New .pacnew files since snapshot — WARN if any.
3. Disk space — FAIL < 2 GB, WARN < 5 GB.
4. Pacman log scan — `tail -n 500 /var/log/pacman.log | grep -E '\[ALPM\] error|warning'` since snapshot timestamp; WARN with count. (Log file is mode 644 by default — no sudo needed; avoid the askpass dialog interruption mid-verify.)
5. Reboot-recommended log (if `config.verify.reboot_log` non-empty AND mtime > snapshot) — WARN.

**Bucket B — Services** (one check per `config.services.to_verify` entry):
- `systemctl is-active <unit>` → active = PASS; inactive/failed = severity lookup (`critical` default = FAIL, `watch` = WARN).

**Explicitly NOT in v1:** network probes, HTTP health checks, port-listen verification, mountpoint checks. Those are EndeavorMain-specific; reserved for v2 hooks.

### RESULT tag mapping (CLI stdout, GUI banner)

The Report phase aggregates signals from every prior phase. Reboot-needed is detected
from any of three independent sources, so a verify-phase failure does not lose the
kernel-update signal:

| Condition (precedence top-down) | Tag |
|---|---|
| Pre-flight or pacman/AUR exited nonzero | `RESULT:UPDATE_FAILED` |
| Verify produced any FAIL | `RESULT:VERIFY_FAILED` |
| Any of: (a) `PendingUpdate.is_kernel=True` was applied; (b) running kernel ≠ installed kernel pkg version; (c) `verify.reboot_log` exists and mtime > snapshot.timestamp | `RESULT:REBOOT_NEEDED` |
| Verify produced WARN with pacnew detected | `RESULT:PACNEW_MERGE_NEEDED` |
| Else | `RESULT:SUCCESS` |

If multiple tags would apply, the highest-precedence one wins; the GUI result banner
lists the others as secondary annotations ("Reboot needed AND pacnew merge pending").

---

## Privileged operation strategy (`privilege/sudo.py`)

Default: **askpass + persistent sudo timestamp**. Rationale: it's Rob's known-working pattern, works on any DE with any askpass binary, minimizes password prompts.

```python
class SudoStrategy(Protocol):
    def warmup(self) -> bool: ...                    # acquire/refresh timestamp
    def run(self, argv, **popen_kwargs) -> Popen: ...  # streaming
    def check(self, argv) -> tuple[int, str]: ...      # capture

class AskpassStrategy(SudoStrategy):
    """Discovers askpass: config override → ksshaskpass → lxqt-openssh-askpass
    → ssh-askpass → /usr/lib/openssh/ssh-askpass. Sets SUDO_ASKPASS, uses sudo -A."""

class PkexecStrategy(SudoStrategy):
    """Wraps each call in pkexec. Headless-compatible; prompts per call."""

class PersistentSudoStrategy(SudoStrategy):
    """Composes AskpassStrategy + background sudo -v refresh every 4 min,
    cleans up with sudo -k on app exit."""

def pick_strategy(cfg: PrivilegeConfig) -> SudoStrategy:
    # mode="auto": Askpass+Persistent first; fall back to Pkexec if no askpass found;
    # fall back to in-app sudo_prompt.py if no $DISPLAY/$WAYLAND_DISPLAY (CLI-only).
```

DE coverage via PKGBUILD `optdepends`: `ksshaskpass` (KDE), `lxqt-openssh-askpass` (LXQt), `x11-ssh-askpass` (XFCE/others), `polkit` (pkexec fallback).

The Qt app does NOT collect the password directly; the askpass helper renders its own native dialog. In-app `sudo_prompt.py` dialog is a last-resort fallback with a clear warning.

---

## Snapshot contents (universal only)

Storage: `~/.local/state/archward/snapshots/YYYY-MM-DD_HHMMSS/`.

```
.timestamp                 # epoch seconds (gates check)
.human-timestamp           # human-readable
meta.json                  # SnapshotMeta
packages/
  explicit.txt             # pacman -Qe
  all.txt                  # pacman -Q
  aur.txt                  # pacman -Qm
  pending-official.txt     # checkupdates
  pending-aur.txt          # yay/paru -Qua  (or empty)
  critical.txt             # versions of every pkg in [risk].high
configs/
  pacman.conf
  mirrorlist
  fstab
  grub-default             # /etc/default/grub
  sshd_config              # if /etc/ssh/sshd_config exists
  sshd_config.d.tar.gz     # if /etc/ssh/sshd_config.d/ has files (chmod 600)
  resolved.conf            # if /etc/systemd/resolved.conf exists
  sudoers.d.tar.gz         # always (chmod 600) — critical rollback target
network/
  ip-addr.txt              # ip addr — interface snapshot
  listening-ports.txt      # ss -tlnp — service binding snapshot
  wg-status.txt            # wg show — only if `wg` binary present and returns 0
services/
  running.txt              # systemctl list-units --state=running
  enabled.txt              # systemctl list-unit-files --state=enabled
  to-verify-status.txt     # is-active for each [services].to_verify
system/
  kernel-running.txt       # uname -r
  cmdline.txt              # /proc/cmdline
  disk.txt                 # df -h
  os-release.txt
  helper.txt               # detected AUR helper or "none"
pacnew-baseline.txt        # find /etc -name '*.pacnew' BEFORE update
```

Each config-file entry is gated on the source file/directory existing — minimal Arch
installs without `/etc/ssh/sshd_config.d/` get a snapshot dir that simply omits that
file rather than failing. `wg-status.txt` is gated on `shutil.which('wg')`. Archives
containing secrets (`sudoers.d.tar.gz`, `sshd_config.d.tar.gz`) are written with mode 600.

Excluded as machine-specific (reserved for v2 hooks): WireGuard configs, Jellyfin /
qBittorrent service drop-ins, NFS mount units, audit rules, custom service drop-ins.

Retention: prune oldest beyond `general.keep_snapshots` (default 10). A `.keep` file inside a snapshot dir exempts it from pruning (future GUI: right-click → Keep forever).

**Timing.** Pruning runs at app exit (after the report phase) OR at the start of the
next run, **never** at snapshot creation. Invariant: the current run's snapshot is
never the snapshot being pruned. This protects against the edge case where
`keep_snapshots = 1` and an update fails — the only useful rollback reference must
still be on disk after the failure.

### Desktop notification on completion (optional)

If `libnotify` is available (`shutil.which('notify-send')`), the Report phase emits a
desktop notification:

```python
subprocess.run(['notify-send', '-a', 'archward',
                '-i', 'archward',
                'archward update complete',
                f'{result_tag} — {fail_count} fail, {warn_count} warn'])
```

Falls through silently on systems without libnotify. Listed in PKGBUILD as
optdepends.

---

## GUI design (PySide6)

Single `QMainWindow`. `QApplication.setApplicationName("archward")` + `setOrganizationName("archward")` for QSettings.

```
┌──────────────────────────────────────────────────────────────┐
│ archward                          [Preferences]      [≡]     │
├──────────────┬───────────────────────────────────────────────┤
│ PHASE RAIL   │                                               │
│  ● Snapshot  │      MAIN CONTENT (current phase view)        │
│  ● Gates     │                                               │
│  ● Risk      │                                               │
│  ○ Update    │                                               │
│  ○ AUR       │  [ Back ]              [ Cancel ]  [ Next ]   │
│  ○ Pacnew    │                                               │
│  ○ Verify    │                                               │
│  ○ Result    │                                               │
├──────────────┴───────────────────────────────────────────────┤
│ ▼ Log (collapsible)                                          │
│  [12:34:56] gates: PASS snapshot 4m old                      │
│  ...                                                          │
├──────────────────────────────────────────────────────────────┤
│ Phase 3/8 — Risk Assessment    Helper: yay    Mode: GUI       │
└──────────────────────────────────────────────────────────────┘
```

**Phase rail** — `QListWidget` (non-interactive forward; clicking a completed phase re-views read-only). Status icons: pending (○), running (animated spinner), pass (●), warn (▲), fail (✕), skipped (–).

**Log pane** — `QPlainTextEdit` (read-only, monospace, ring-buffered ~10k lines), phase filter dropdown, copy/save/auto-scroll toggle, collapsible via splitter.

**Phase views:**
- `snapshot_view` — indeterminate progress + dynamic ticking checklist (packages → configs → services → kernel → disk).
- `gates_view` — `QTreeWidget` rows per gate (status, name, message, optional "Override" button when `can_override`).
- `risk_view` — three vertically stacked `QTreeWidget`s (HIGH/MEDIUM/LOW). HIGH section has a red border, expanded by default. Columns: ☐ | Package | Old → New | Reason. Toolbar: "Skip selected" (multi-select → pacman `--ignore`), counter labels (`5 HIGH · 12 MEDIUM · 73 LOW`), `[Proceed]` (red if HIGH > 0) + `[Cancel]`.
- `update_view` — streaming monospace pane (shared by official + AUR phases). Phase header label switches. Stop button disabled once started (pacman isn't safely interruptible).
- `pacnew_view` — `QTreeWidget` of pacnew files. Buttons per row: [View Diff] [Keep Ours] [Use New] [Edit Manually] [Leave]. Diff dialog uses `difflib.unified_diff` + `QSyntaxHighlighter` (red `-`, green `+`, gray `@@`).
- `verify_view` — grouped by bucket (Universal, Services). "Re-run" button at top.
- `result_view` — banner (SUCCESS/REBOOT_NEEDED/PACNEW/VERIFY_FAILED), summary metrics, links to log file + snapshot dir, [Run another update] / [Close].

**Preferences dialog** — `QTabWidget`: General · Gates · Risk · Services · Pacnew Rules · AUR · Privilege · Advanced (with "Re-detect" button). Save via `tomli-w`; "Reset to defaults" prompts confirmation.

**Threading:** Pipeline runs in a `QThread`. Pipeline emits `PhaseEvent`s via pure-Python `EventBus`; `qt_bus.py` (`QObject` with `Signal(PhaseEvent)`) bridges to main-thread `QueuedConnection`s. UI never imports pipeline internals — only subscribes to signals.

---

## AUR phase design

`aur/helper.py` defines an `AurHelper` Protocol:

```python
class AurHelper(Protocol):
    name: str
    @classmethod
    def is_available(cls) -> bool: ...
    def list_updates(self) -> list[PendingUpdate]: ...
    def run_update(self, ignore: list[str], emit_log: Callable[[str], None]) -> int: ...
```

**`aur/adapters/yay.py`** — `yay -Qua` (parse `pkg old -> new`), `yay -Sua --noconfirm --ignore=<list>`. Helper handles its own sudo (inherits our `SUDO_ASKPASS`).

**`aur/adapters/paru.py`** — symmetric to yay.

**`aur/adapters/aurutils.py`** — multi-step (`aur vercmp -q`, `aur sync -u --noconfirm`); documented as best-effort.

Build failure handling: regex-scan stdout for `==> ERROR:` / `failed to build`, capture last 50 lines into `BuildFailure`, continue with remaining packages, surface in result view with "Retry these later" hint. (Today's radarr/MailKit-CVE failure is the textbook case.)

### No-helper-detected behavior

When `detect_aur_helper()` returns `None`, the AUR phase does **not** silently skip.
Instead it still enumerates installed AUR packages via `pacman -Qm` and presents them
in the AUR view with a banner:

> No AUR helper detected. Install one of `yay`, `paru`, or `aurutils` to enable AUR
> update checks. Currently installed AUR packages: \<list\>.

The phase is marked `SKIPPED` in the phase rail (not `FAIL`); RESULT tag is unaffected.
This preserves the bash pipeline's information value (`pacman -Qm` always runs there)
without making a helper a hard install requirement.

### Helper preference rationale

Default order: `yay > paru > aurutils`. As of 2026, both yay and paru are actively
maintained — paru is currently more actively developed (yay's co-maintainer
Morganamilo left to write paru, which shipped 2.0 after a hiatus). yay retains the
broadest installed base and "do no harm to existing users" wins here. aurutils
remains last because its non-interactive driving is fragile (chroot setup, manual
repo registration) — its adapter ships with a documented "best-effort" disclaimer.

---

## v2 reservations (do NOT implement now — leave seams)

### `pipeline/hooks.py` — no-op stub
```python
class HookRunner:
    def __init__(self, cfg: HooksConfig | None): self.cfg = cfg
    def run_pre_update(self, ctx): return     # v2: exec each cfg.pre_update
    def run_post_verify(self, ctx, result): return  # v2: exec each cfg.post_verify
```

`Pipeline` already calls `self.hooks.run_pre_update(ctx)` between risk-approval and pacman-update, and `self.hooks.run_post_verify(ctx, result)` after verify. v2 = fill in the body + add `[hooks]` to `ConfigModel`.

### Profiles
`load_config(path: Path)` is the only config entry point. v2 adds `~/.config/archward/profiles/<name>.toml` + `--profile <name>` flag → resolves which path to pass. No other changes needed.

### Custom verify probes
v1 verify is fixed list + service iteration. v2 introduces an entry-points group `archward.verify_checks` with contract `(cfg, snapshot) -> list[VerifyCheck]`. The seam is `Verifier.collect_checkers()` in `verify_phase.py` — today hard-coded, tomorrow scans `importlib.metadata.entry_points()`.

### Preferences dialog — inline help text

v1 Preferences shows widget labels only. A new user opening Gates sees
"Snapshot max age: [60 min]" and has no idea what that controls. v2 should
add a one-line gray help label under (or beside) every field explaining what
it does and what changing it costs.

Example for Gates → "Snapshot max age":
> How fresh the snapshot must be before update runs. Older snapshots are
> rejected so the rollback reference matches the system at update time.
> Take a new snapshot if you bumped this and now archward refuses to run.

The existing Pacnew and Privilege tabs already use this pattern (gray `QLabel`
in `_lbl()` after the field). Sweep the remaining tabs:

- **General**: explain what snapshot/log retention controls; consequences of low/high values.
- **Gates**: snapshot max age, min disk GB, allow-override.
- **Risk**: HIGH list vs kernel_patterns vs medium_patterns — when to add to which; effect of changes.
- **Services**: what `to_verify` does in the post-update verify phase; what severity values map to.
- **AUR**: what `enabled`/`skip` do separately (transient skip vs permanent disable); preference-order semantics.
- **Pacman**: when to turn off `--noconfirm`; common extra_args (`--needed`, `--overwrite`).
- **Verify**: when to clear `reboot_log` (non-EOS systems).

Implementation: each `_*Tab` already uses `_lbl()` for hints — extend it. No
architectural changes needed; pure docs work. Consider sourcing the help
strings from a single dict in `defaults.py` (or a sibling `help_text.py`) so
the strings live next to the canonical schema rather than buried in the UI
file.

---

## Distribution / packaging

### `pyproject.toml`
```toml
[build-system]
requires = ["hatchling>=1.20"]
build-backend = "hatchling.build"

[project]
name = "archward"
version = "0.1.0"
description = "A safe-update GUI for Arch-based Linux distributions"
readme = "README.md"
license = { text = "GPL-3.0-or-later" }
authors = [{ name = "Rob Petersen" }]
requires-python = ">=3.11"
dependencies = [
  "PySide6>=6.6",
  "pydantic>=2.6",
  "tomli-w>=1.0",
]

[project.scripts]
archward = "archward.cli:main"

[project.gui-scripts]
archward-gui = "archward.cli:main_gui"

[tool.hatch.build.targets.wheel]
packages = ["src/archward"]
```

### `packaging/PKGBUILD`
```bash
pkgname=archward
pkgver=0.1.0
pkgrel=1
pkgdesc="Safe-update GUI for Arch-based Linux distributions"
arch=('any')
url="https://github.com/indyfive11/archward"
license=('GPL-3.0-or-later')   # SPDX identifier — preferred over legacy 'GPL3'
depends=('python>=3.11' 'pyside6' 'python-pydantic' 'python-tomli-w'
         'pacman>=6.1' 'pacman-contrib')
optdepends=(
  'yay: AUR helper (preferred)'
  'paru: alternative AUR helper'
  'aurutils: alternative AUR helper'
  'ksshaskpass: askpass for KDE/Plasma'
  'lxqt-openssh-askpass: askpass for LXQt'
  'x11-ssh-askpass: askpass for other DEs'
  'polkit: pkexec fallback'
  'meld: graphical merge tool for pacnew'
  'libnotify: desktop notifications when an update finishes'
)
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-hatchling')
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
sha256sums=('SKIP')

build()   { cd "$pkgname-$pkgver"; python -m build --wheel --no-isolation; }
package() {
  cd "$pkgname-$pkgver"
  python -m installer --destdir="$pkgdir" dist/*.whl
  install -Dm644 packaging/archward.desktop "$pkgdir/usr/share/applications/archward.desktop"
  install -Dm644 packaging/archward.svg     "$pkgdir/usr/share/icons/hicolor/scalable/apps/archward.svg"
  install -Dm644 LICENSE                    "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
```

### `packaging/archward.desktop`
```ini
[Desktop Entry]
Type=Application
Name=archward
GenericName=Safe System Update
Comment=Snapshot, gate, update, and verify your Arch-based system
Exec=archward-gui
Icon=archward
Categories=System;PackageManager;Qt;
# Phase-7 validation note: dropped 'Settings' to avoid the
# desktop-file-validate hint about appearing in two main-category menus.
Keywords=update;upgrade;pacman;aur;arch;snapshot;
Terminal=false
StartupNotify=true
```

---

## Implementation order (demoable at every step)

1. **CLI core, no GUI/AUR/config** — skeleton, models, sudo strategy, pacman query/runner, snapshot, gates, hard-coded risk, official update, universal verify, RESULT tags. Demo: matches existing bash scripts' output on Rob's machine.
2. **Config + auto-detect** — loader, defaults, `detect.py`, `--detect` flag. Replace hard-coded risk list with `config.risk.high`. Add services bucket to verify. Demo: works on a clean Arch VM.
3. **AUR phase** — `helper.py`, `yay.py` adapter, `update_aur.py`, build-failure capture. Then `paru.py`, `aurutils.py`. Demo: end-to-end CLI with official + AUR.
4. **Minimal GUI shell** — `main_window`, `phase_rail`, `log_pane`, `qt_bus`. `--gui` launches; pipeline runs in `QThread`. Demo: GUI mirrors CLI progress.
5. **Phase views** — snapshot/gates first (simplest), then risk (3-column tree), update (shared), pacnew + diff dialog, verify, result.
6. **Preferences dialog** — tab by tab. Re-detect button. Save/reload roundtrip.
7. **Packaging polish** — desktop file, icon, README, screenshots, PKGBUILD, tagged v0.1.0 release on GitHub.
8. **(v2 backlog, not v1)** — hooks, profiles, verify-check entry points.

---

## Verification / end-to-end test plan

### Unit tests (`tests/unit/`, pytest)
- `test_risk.py` — classification of named packages against config rules; glob/kernel patterns; fallback.
- `test_gates.py` — mock snapshot dirs with various `.timestamp` ages; mock `statvfs` for disk gate.
- `test_pacnew_strategy.py` — pattern matching, first-match-wins, fallback.
- `test_config_loader.py` — round-trip TOML, schema_version migration, per-section error recovery.
- `test_aur_adapter_yay.py` — fixture stdout from `tests/fixtures/yay_qua_output/` → parsed `PendingUpdate` list.
- `test_snapshot_writer.py` — run snapshot phase against `tmp_path` with `privilege.sudo` mocked to identity; assert file tree structure.
Coverage target: 85%+ on pure logic modules.

### Integration tests (`tests/integration/`)
- **Container dry-run**: `archlinux/archlinux:latest` + `pacman -S python pyside6 python-pydantic pacman-contrib` + `archward --dry-run`. Assert: no crash, RESULT tag emitted, correct exit code. Runs in GitHub Actions.
- **GUI smoke** via `pytest-qt`: launch main window, advance through phases with mock pipeline, assert phase rail updates correctly.

### Manual test matrix (pre-release checklist, README)

| Distro | DE | Helper | Notes |
|---|---|---|---|
| Arch | KDE Plasma 6 | yay | reference |
| Arch | GNOME 46 | paru | |
| Arch | XFCE | aurutils | |
| EndeavourOS | KDE | yay | Rob's machine — closest to bash-script behavior |
| Manjaro | KDE | yay | document pamac not yet supported |
| CachyOS | KDE | paru | |
| Garuda | KDE | paru | |
| (headless) | — | yay | CLI-only path, askpass→pkexec fallback |

For each: `--dry-run`, then real update in a VM, then verify phase. Confirm askpass works on each DE.

### End-to-end acceptance (Rob's machine, manual smoke test)
1. `archward --detect` writes a sane `~/.config/archward/config.toml`.
2. `archward --dry-run` produces the same `RESULT:` tag as `~/bin/system-update.sh --dry-run`.
3. `archward --gui` launches; full pipeline completes; result banner matches CLI.
4. Faked HIGH-risk situation (manually edit config to add an installed package): GUI risk view highlights it red and gates the proceed button.
5. Trigger a .pacnew (touch `/etc/pacman.conf.pacnew` with sudo): GUI pacnew view lists it with `review_needed` strategy and `View Diff` renders correctly.
6. Stop a `[services].to_verify` unit before verify: verify view shows FAIL for it.
7. AUR phase succeeds with yay; force a build failure (uninstall a dep): result view surfaces the failure with last 50 lines.

---

## Non-goals (explicit)

- No probe taxonomy in v1 (port-listen, ping, http-get, mountpoint).
- No EndeavorMain-specific defaults — only universal detection.
- No daemon; runs to completion and exits.
- No scheduling in v1.
- No multi-machine / fleet management.
- No pamac/Manjaro-store integration in v1.

---

## v0.1.0 release blockers

1. **Replace placeholder `archward.svg` icon** with a real SVG before the AUR
   submission. AUR maintainers reject packages with literal placeholder icons.
2. **Test fixtures** under `tests/fixtures/` must be populated from at least one
   real bash-pipeline run on Rob's machine (EndeavourOS + yay), sanitized of
   machine-specific paths/hostnames. Document the regeneration procedure in
   `docs/development.md`.
3. **CHANGELOG.md** entry for `v0.1.0` in Keep-a-Changelog format with an
   `Unreleased` placeholder for ongoing work.

## Documentation hooks

- `docs/development.md` — add an "Optional: NOPASSWD setup for unattended use"
  section showing a narrow `/etc/sudoers.d/archward` fragment (e.g., scoped to
  `/usr/bin/pacman`, `/usr/bin/find`, `/usr/bin/tee`). Make explicit this is opt-in
  security relaxation, not a default — most users should keep askpass.

---

## Plan-maintenance note

This plan is canonical for the v1 scope. Significant deviations discovered during
implementation should update this file *before* the deviating PR merges, so the
file remains the single source of truth for design intent.
