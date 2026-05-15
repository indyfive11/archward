"""Pacman cache policy detection + rollback-safety assessment (v0.4.4).

archward's rollback promise rests entirely on the old `.pkg.tar.zst`
still being in `/var/cache/pacman/pkg/`. The cache policy — paccache
timer + args, pacman's CleanMethod, and any post-transaction cleanup
hooks — governs whether it survives. archward never looked at this
before; a user could run archward for months while their cache policy
silently gutted the rollback path.

This module is pure-Python and Qt-free (the GUI Cache tab and, later,
a CLI subcommand both consume it). It only DETECTS and ASSESSES;
applying a policy is the caller's job (the GUI runs the previewed
sudo commands).

Effective paccache behavior: the shipped `paccache.service` runs
`paccache -r $PACCACHE_ARGS`. The bare `-r` keeps 3 versions; an
explicit `-k N` / `-rk N` in PACCACHE_ARGS overrides that. So the
"unset PACCACHE_ARGS ⇒ keep 3" assumption is the documented default,
but we parse the explicit value when present.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

log = logging.getLogger(__name__)

PACMAN_CACHE_DIR = Path("/var/cache/pacman/pkg")
_CONF_D = Path("/etc/conf.d/pacman-contrib")
_PACMAN_CONF = Path("/etc/pacman.conf")
_HOOK_DIRS = (Path("/etc/pacman.d/hooks"), Path("/usr/share/libalpm/hooks"))

# paccache default when invoked as bare `paccache -r` (no -k): keep 3.
_PACCACHE_BARE_KEEP = 3
_SYSTEMCTL_TIMEOUT_S = 5

# Match an explicit keep count in a PACCACHE_ARGS string. Handles
# `-k 2`, `-k2`, `-rk2`, `-rk 2`, `--keep 2`, `--keep=2`.
_KEEP_RE = re.compile(r"(?:--keep[= ]|--keep |-[a-z]*k\s*)(\d+)")

# A hook's Exec line is "dangerous" (prunes the cache in-band with the
# pacman transaction) if it runs paccache, `pacman -Sc`/`-Scc`, or
# deletes from the cache dir. Matched against the Exec line ONLY — a
# loose whole-file grep false-positives (e.g. glib-compile-schemas).
_DANGEROUS_EXEC_RES = (
    re.compile(r"\bpaccache\b"),
    re.compile(r"\bpacman\b.*\s-S(c|cc)\b"),
    re.compile(r"\brm\b.*/var/cache/pacman/pkg"),
)


class RollbackSafety(StrEnum):
    DANGEROUS = "dangerous"   # rollback for the *current* update will fail
    TIGHT = "tight"           # ~1 prior version of headroom
    BALANCED = "balanced"     # ~2 rollback points (Arch-default-ish)
    GENEROUS = "generous"     # deep history (watch disk)
    UNMANAGED = "unmanaged"   # never auto-pruned: unbounded history + disk risk


@dataclass(frozen=True)
class CachePolicy:
    timer_state: str            # "enabled" | "disabled" | "not-installed"
    paccache_args: str          # raw PACCACHE_ARGS ("" if unset/empty)
    effective_keep: int         # keep-N the policy would enforce when it prunes
    clean_method: tuple[str, ...]   # ("KeepInstalled",) is the safe default
    cleaning_hooks: tuple[Path, ...]  # dangerous post-transaction pruners
    cache_size_bytes: int
    cache_file_count: int
    safety: RollbackSafety
    explanation: str            # one-paragraph plain-language verdict


@dataclass(frozen=True)
class CachePreset:
    key: str                    # "home" | "workstation" | "server" | "mission-critical"
    label: str
    paccache_args: str          # value to write into PACCACHE_ARGS
    enable_timer: bool
    description: str


CACHE_PRESETS: tuple[CachePreset, ...] = (
    CachePreset(
        key="home",
        label="Home computer",
        paccache_args="-rk3",
        enable_timer=True,
        description="Keep 3 versions, weekly auto-prune. Balanced — "
        "~2 rollback points per package, modest disk.",
    ),
    CachePreset(
        key="workstation",
        label="Workstation",
        paccache_args="-rk5 -ruk2",
        enable_timer=True,
        description="Keep 5 installed + 2 uninstalled versions, weekly. "
        "More rollback history for frequent updaters.",
    ),
    CachePreset(
        key="server",
        label="Server",
        paccache_args="-rk10",
        enable_timer=True,
        description="Keep 10 versions, weekly. Rollback is operationally "
        "critical; server disk is usually ample.",
    ),
    CachePreset(
        key="mission-critical",
        label="Mission-critical",
        paccache_args="-rk15",
        enable_timer=False,
        description="Keep 15 versions, NO timer (manual prune only). The "
        "rollback substrate must survive the full update → verify → "
        "reboot → soak window. Any post-transaction cleaning hook "
        "defeats this — remove it.",
    ),
)


# ── individual detectors ───────────────────────────────────────────────────


def paccache_timer_state() -> str:
    """Return 'enabled', 'disabled', or 'not-installed'.

    `systemctl is-enabled` needs no privilege. A missing unit (pacman-contrib
    absent, or the timer not shipped) → 'not-installed'.
    """
    try:
        r = subprocess.run(
            ["systemctl", "is-enabled", "paccache.timer"],
            check=False, capture_output=True, text=True,
            timeout=_SYSTEMCTL_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "not-installed"
    out = (r.stdout or "").strip()
    if out in ("enabled", "enabled-runtime", "static", "indirect"):
        return "enabled"
    if out in ("disabled", "masked"):
        return "disabled"
    # `is-enabled` exits non-zero + prints nothing for an unknown unit.
    return "not-installed"


def read_paccache_args(conf_path: Path = _CONF_D) -> str:
    """Parse PACCACHE_ARGS from /etc/conf.d/pacman-contrib. '' if unset."""
    if not conf_path.exists():
        return ""
    try:
        for line in conf_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() != "PACCACHE_ARGS":
                continue
            val = val.strip().strip('"').strip("'").strip()
            return val
    except OSError as e:
        log.debug("could not read %s: %s", conf_path, e)
    return ""


def effective_keep(paccache_args: str) -> int:
    """The keep-N the policy enforces. Explicit -k wins; else the bare
    `paccache -r` default of 3."""
    m = _KEEP_RE.search(paccache_args)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return _PACCACHE_BARE_KEEP


def read_cache_dirs(pacman_conf: Path = _PACMAN_CONF) -> tuple[Path, ...]:
    """Parse [options] CacheDir from pacman.conf.

    pacman lets `CacheDir` appear multiple times and/or carry several
    space-separated paths; the effective cache is the union. Unset ⇒
    the compiled-in default `/var/cache/pacman/pkg/`. archward must
    honour a relocated cache (a big-disk move is common on small-root
    installs) — scanning only the hard-coded default would report every
    just-updated package as "rollback gone" on those systems.
    """
    default = (PACMAN_CACHE_DIR,)
    if not pacman_conf.exists():
        return default
    dirs: list[Path] = []
    try:
        for line in pacman_conf.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            s = line.strip()
            if s.startswith("#") or not s.lower().startswith("cachedir"):
                continue
            _, _, val = s.partition("=")
            for tok in val.replace(",", " ").split():
                dirs.append(Path(tok.rstrip("/") or "/"))
    except OSError as e:
        log.debug("could not read %s for CacheDir: %s", pacman_conf, e)
    return tuple(dict.fromkeys(dirs)) or default


def read_clean_method(pacman_conf: Path = _PACMAN_CONF) -> tuple[str, ...]:
    """Parse [options] CleanMethod. Default is ('KeepInstalled',) — the
    safe one (KeepCurrent removes everything but the installed version,
    destroying the downgrade path)."""
    if not pacman_conf.exists():
        return ("KeepInstalled",)
    try:
        for line in pacman_conf.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith("#") or not s.lower().startswith("cleanmethod"):
                continue
            _, _, val = s.partition("=")
            methods = tuple(v for v in val.replace(",", " ").split() if v)
            return methods or ("KeepInstalled",)
    except OSError as e:
        log.debug("could not read %s: %s", pacman_conf, e)
    return ("KeepInstalled",)


def scan_cleaning_hooks(hook_dirs: tuple[Path, ...] = _HOOK_DIRS) -> tuple[Path, ...]:
    """Return hook files whose [Action] Exec line prunes the pacman cache.

    These run as part of the pacman transaction, so they delete the
    rollback substrate *during the same update archward is running*.
    Match on the Exec line ONLY — a whole-file substring match
    false-positives on unrelated hooks (glib-compile-schemas etc.).
    """
    found: list[Path] = []
    for d in hook_dirs:
        if not d.is_dir():
            continue
        try:
            entries = sorted(d.glob("*.hook"))
        except OSError:
            continue
        for hook in entries:
            try:
                text = hook.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw in text.splitlines():
                line = raw.strip()
                if not line.lower().startswith("exec"):
                    continue
                _, _, cmd = line.partition("=")
                if any(rx.search(cmd) for rx in _DANGEROUS_EXEC_RES):
                    found.append(hook)
                    break
    return tuple(found)


def cache_stats(cache_dir: Path = PACMAN_CACHE_DIR) -> tuple[int, int]:
    """Return (total_size_bytes, package_file_count) for the pacman cache.

    Kept deliberately simple: exact size + file count. Per-package
    version arithmetic is fragile (pacman cache filenames pack dashes
    in both name and version) and isn't needed for the verdict, which
    is policy-driven, not contents-driven.
    """
    if not cache_dir.is_dir():
        return 0, 0
    total = 0
    count = 0
    try:
        for entry in cache_dir.iterdir():
            name = entry.name
            if not entry.is_file():
                continue
            if not (name.endswith(".pkg.tar.zst")
                    or name.endswith(".pkg.tar.xz")
                    or name.endswith(".pkg.tar.gz")):
                continue
            try:
                total += entry.stat().st_size
            except OSError:
                continue
            count += 1
    except OSError as e:
        log.debug("could not scan cache dir %s: %s", cache_dir, e)
    return total, count


# ── assessment ─────────────────────────────────────────────────────────────


def _assess(
    timer_state: str,
    keep: int,
    clean_method: tuple[str, ...],
    cleaning_hooks: tuple[Path, ...],
) -> tuple[RollbackSafety, str]:
    if cleaning_hooks:
        names = ", ".join(h.name for h in cleaning_hooks)
        return (
            RollbackSafety.DANGEROUS,
            f"A cache-cleaning pacman hook ({names}) runs as part of every "
            "pacman transaction. It deletes old package versions during the "
            "same update archward runs, so archward cannot roll back the "
            "very update you just applied. Remove the hook, or accept that "
            "rollback won't work for fresh updates.",
        )
    # KeepCurrent alone drops everything but the in-sync-db version (no
    # downgrade target). KeepInstalled (the default) is safe. BOTH set
    # is safe — paccache keeps the union, so the installed/old versions
    # survive. Only flag DANGEROUS for KeepCurrent *without* KeepInstalled.
    if (
        "KeepCurrent" in clean_method
        and "KeepInstalled" not in clean_method
        and timer_state == "enabled"
    ):
        return (
            RollbackSafety.DANGEROUS,
            "pacman CleanMethod=KeepCurrent (without KeepInstalled) plus an "
            "enabled paccache.timer means cache cleaning keeps only the "
            "in-sync-database version — there is no older version to "
            "downgrade to. Add KeepInstalled (the default) back.",
        )
    if keep <= 1 and timer_state == "enabled":
        return (
            RollbackSafety.DANGEROUS,
            f"paccache keeps only {keep} version and the timer is enabled. "
            "After the next weekly prune you have no prior version to roll "
            "back to. Raise the keep count.",
        )
    if timer_state == "enabled" and keep == 2:
        return (
            RollbackSafety.TIGHT,
            "paccache keeps 2 versions on a timer — roughly one prior "
            "version of rollback headroom. Workable, but a single bad "
            "update uses it up.",
        )
    if timer_state == "enabled" and keep == 3:
        return (
            RollbackSafety.BALANCED,
            "paccache keeps 3 versions on a timer (the Arch-ish default) — "
            "about 2 rollback points per package. Reasonable for a desktop.",
        )
    if timer_state == "enabled" and keep >= 5:
        return (
            RollbackSafety.GENEROUS,
            f"paccache keeps {keep} versions on a timer — deep rollback "
            "history. Watch disk usage on small root partitions.",
        )
    # Timer disabled / not-installed and no in-band hook: the cache is
    # never auto-pruned. Rollback always works, but the cache grows
    # without bound.
    return (
        RollbackSafety.UNMANAGED,
        "No paccache timer and no cleaning hook — the cache is never "
        "auto-pruned. Rollback always works (every old version is kept), "
        "but /var/cache/pacman/pkg grows without bound. Enable a policy "
        "below to cap it while keeping enough rollback headroom.",
    )


def detect_cache_policy() -> CachePolicy:
    """One-shot: gather every signal + compute the rollback-safety verdict."""
    timer = paccache_timer_state()
    args = read_paccache_args()
    keep = effective_keep(args)
    method = read_clean_method()
    hooks = scan_cleaning_hooks()
    size = 0
    count = 0
    for d in read_cache_dirs():
        s, c = cache_stats(d)
        size += s
        count += c
    safety, explanation = _assess(timer, keep, method, hooks)
    return CachePolicy(
        timer_state=timer,
        paccache_args=args,
        effective_keep=keep,
        clean_method=method,
        cleaning_hooks=hooks,
        cache_size_bytes=size,
        cache_file_count=count,
        safety=safety,
        explanation=explanation,
    )


def preset_commands(preset: CachePreset) -> list[list[str]]:
    """The exact privileged argv list a preset would run (for the GUI's
    preview-before-apply dialog). Does NOT execute anything.

    PACCACHE_ARGS is written via `tee` (allowlisted) to a single small
    file; timer toggled via `systemctl` (allowlisted).
    """
    conf_line = f"PACCACHE_ARGS='{preset.paccache_args}'\n"
    cmds: list[list[str]] = [
        # `tee` reads the new content from stdin; the GUI feeds conf_line.
        ["tee", str(_CONF_D)],
    ]
    if preset.enable_timer:
        cmds.append(["systemctl", "enable", "--now", "paccache.timer"])
    else:
        cmds.append(["systemctl", "disable", "--now", "paccache.timer"])
    return cmds


def preset_conf_content(preset: CachePreset) -> str:
    """The full content archward would write to /etc/conf.d/pacman-contrib."""
    return (
        "# Managed by archward (Cache tab). PACCACHE_ARGS is appended to\n"
        "# the bare `paccache -r` the paccache.service runs.\n"
        f"PACCACHE_ARGS='{preset.paccache_args}'\n"
    )
