"""`archward doctor` — one-shot read-only self-diagnostic.

Answers "why doesn't archward work on this box?" in one command: identity,
distro, config validity, sudo/askpass, pacman tooling, disk, cache policy,
state dirs, AUR helper, services config, GUI stack, last recorded run.
The output (or `--json`) is the support bundle for a bug report.

Hard rules for this module:

- READ-ONLY. Doctor never mutates anything: no config bootstrap-write
  (``load_config_report`` writes defaults when the file is missing — doctor
  detects that case itself and reports it), no dir creation, no chmod, no
  sudo warmup, and above all no askpass popup (``sudo -n`` only, never
  ``-A``).
- CRASH-PROOF. Doctor exists for broken boxes. Every check runs on a
  daemon thread with a deadline: an exception becomes a FAIL row, a hang
  (dead network mount, wedged systemd) becomes a WARN row, and the report
  always prints.
- Check-specific imports are deferred into the check bodies so one broken
  module can't take the whole report down with it.
- Severities mirror the pipeline: a condition the preflight/gates treat as
  WARN (cache DANGEROUS) or FAIL (db.lck, low disk) gets the same status
  here — doctor never contradicts a sibling command.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from archward import __version__
from archward.models.verify import CheckStatus

# `--json` schema version. The document shape is a public contract like the
# run-record schema (docs/cli.md): changes within a version are additive
# only; consumers must ignore unknown keys.
DOCTOR_SCHEMA_VERSION = 1

# Per-check wall-clock budget. Checks are filesystem stats + short
# subprocesses; anything slower means a wedged mount or daemon, and the
# report must keep going (and the daemon thread lets the process exit).
_CHECK_DEADLINE_S = 15.0

_SUDO_TIMEOUT_S = 5
_GUI_PROBE_TIMEOUT_S = 10


@dataclass
class CheckRow:
    name: str
    status: CheckStatus
    message: str
    detail: str | None = None


def _run_check(name: str, fn, deadline: float | None = None) -> list[CheckRow]:
    """Run one check body with crash + hang protection.

    `fn` returns a list of CheckRow. A raised exception becomes a FAIL row;
    exceeding the deadline becomes a WARN row (the daemon thread is
    abandoned — it can't be killed, but it won't block process exit).
    `deadline=None` reads _CHECK_DEADLINE_S at call time; a check whose
    body legitimately needs longer (gui-stack runs two 10s subprocess
    probes) passes its own.
    """
    if deadline is None:
        deadline = _CHECK_DEADLINE_S
    rows: list[list[CheckRow]] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            rows.append(fn())
        except BaseException as e:  # noqa: BLE001 — the report must survive anything
            errors.append(e)

    t = threading.Thread(target=target, daemon=True, name=f"doctor-{name}")
    t.start()
    t.join(deadline)
    if t.is_alive():
        return [CheckRow(
            name, CheckStatus.WARN,
            f"check timed out after {deadline:.0f}s",
            "A probe hung — an unreachable network mount or a wedged system "
            "daemon is the usual cause.",
        )]
    if errors:
        e = errors[0]
        return [CheckRow(
            name, CheckStatus.FAIL,
            f"check crashed: {type(e).__name__}: {e}",
        )]
    return rows[0]


# ── check bodies ──────────────────────────────────────────────────────────


def _identity_rows() -> list[CheckRow]:
    import archward

    pkg_dir = Path(archward.__file__).resolve().parent
    return [CheckRow(
        "archward", CheckStatus.PASS,
        f"archward {__version__} at {pkg_dir}",
        f"interpreter: {sys.executable}",
    )]


def _root_rows() -> list[CheckRow]:
    if os.geteuid() != 0:
        return []
    return [CheckRow(
        "running-as-root", CheckStatus.WARN,
        "running as root — this report describes root's environment, not "
        "the user's",
        "archward runs as a regular user (it elevates via sudo itself). "
        "Re-run doctor as the user who runs archward.",
    )]


def _distro_rows() -> list[CheckRow]:
    from archward.system.distro import detect_distro

    info = detect_distro()
    if not info.is_arch_based:
        return [CheckRow(
            "distro", CheckStatus.FAIL,
            f"{info.pretty_name or info.id!r} is not Arch-based — archward "
            "will refuse to run",
        )]
    return [CheckRow(
        "distro", CheckStatus.PASS,
        f"{info.pretty_name} (id={info.id}, via {info.detected_via})",
    )]


def _config_rows(config_path: Path | None, cfg_holder: dict) -> list[CheckRow]:
    """Config validity. Also produces the ConfigModel every later check uses.

    NEVER goes through build_config/load_config/load_config_report: those
    bootstrap-write the config when the file is missing (and build_config
    mkdirs the snapshot/log dirs) — doctor is read-only. Reading the bytes
    ourselves removes the write branch entirely (no stat-then-load race)
    and lets read failures the loader maps to a clean report (root-owned
    file, a directory at the path, I/O errors) be diagnosed honestly.
    The parse/merge pipeline is the loader's own, so verdicts match what
    archward actually does with the file.
    """
    import tomllib

    from archward.config.defaults import default_config
    from archward.config.loader import _merge_with_defaults, default_config_path

    cfg_holder["cfg"] = default_config()  # replaced below when a file loads
    path = config_path or default_config_path()

    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if path.is_symlink():
            return [CheckRow(
                "config", CheckStatus.WARN,
                f"{path} is a symlink to a missing target — defaults in "
                "effect",
                "Fix the link target (dotfiles repo not checked out?); the "
                "first pipeline run would replace the dead link with a "
                "defaults file.",
            )]
        return [CheckRow(
            "config", CheckStatus.PASS,
            f"no config file yet — defaults in effect ({path} is created on "
            "first run)",
        )]
    except PermissionError:
        return [CheckRow(
            "config", CheckStatus.FAIL,
            f"{path} exists but is not readable — archward is silently "
            "running on defaults",
            "Commonly root-owned after a `sudo archward` run. "
            f"Fix: sudo chown $USER {path}",
        )]
    except OSError as e:
        return [CheckRow(
            "config", CheckStatus.FAIL,
            f"cannot read {path}: {e}",
            "archward would silently run on defaults.",
        )]

    try:
        raw = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as e:
        return [CheckRow(
            "config", CheckStatus.FAIL,
            f"TOML parse error in {path} — every value is falling back to "
            "defaults (file left untouched)",
            f"{e}\nFix the syntax by hand, or back the file up and "
            "regenerate with `archward --write-config`.",
        )]

    cfg, report = _merge_with_defaults(raw, cfg_holder["cfg"])
    cfg_holder["cfg"] = cfg

    rows: list[CheckRow] = []
    if report.read_only:
        rows.append(CheckRow(
            "config", CheckStatus.WARN,
            f"config schema_version {report.schema_version} is newer than "
            "this archward — running read-only (settings honored, writes "
            "refused)",
            "Upgrade archward, or expect config writes (auto-prune, "
            "aur.skip reset, Preferences) to be skipped.",
        ))
    if report.fallback_sections:
        rows.append(CheckRow(
            "config", CheckStatus.WARN,
            "invalid section(s) falling back to defaults: "
            + ", ".join(report.fallback_sections),
            f"Fix the named [section]s in {path}; an explicit Preferences "
            "save would overwrite them with defaults.",
        ))
    if not rows:
        detail = None
        if report.unknown_sections:
            detail = ("unknown sections (preserved verbatim): "
                      + ", ".join(report.unknown_sections))
        rows.append(CheckRow(
            "config", CheckStatus.PASS,
            f"valid (schema_version {report.schema_version}) — {path}",
            detail,
        ))
    return rows


def _sudo_rows(sudo_holder: dict) -> list[CheckRow]:
    if shutil.which("sudo") is None:
        return [CheckRow(
            "sudo", CheckStatus.FAIL,
            "sudo binary not found — archward cannot elevate",
        )]
    try:
        r = subprocess.run(
            ["sudo", "-n", "true"],
            check=False, capture_output=True, timeout=_SUDO_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return [CheckRow(
            "sudo", CheckStatus.WARN,
            f"`sudo -n true` timed out after {_SUDO_TIMEOUT_S}s",
        )]
    if r.returncode == 0:
        sudo_holder["ok"] = True
        return [CheckRow(
            "sudo", CheckStatus.PASS,
            "passwordless sudo available (NOPASSWD or warm timestamp)",
        )]
    # sudo -n can't distinguish a cold timestamp from a user with no sudo
    # rights at all (sudo deliberately authenticates before disclosing
    # denial) — say exactly what is and isn't known.
    return [CheckRow(
        "sudo", CheckStatus.PASS,
        "sudo will prompt for a password at update time",
        "Whether this user actually has sudo rights can't be verified "
        "without prompting — test with `sudo -v` in a terminal.",
    )]


def _askpass_rows(cfg, sudo_ok: bool) -> list[CheckRow]:
    from archward.privilege.sudo import discover_askpass

    override = cfg.privilege.askpass
    resolved = discover_askpass(override)

    rows: list[CheckRow] = []
    if resolved is None:
        rows.append(CheckRow(
            "askpass", CheckStatus.FAIL,
            "no askpass binary found — sudo password prompts will block "
            "invisibly in the GUI",
            "Install ksshaskpass / lxqt-openssh-askpass / x11-ssh-askpass "
            "(the packaged install bundles archward-askpass), or configure "
            "NOPASSWD for pacman.",
        ))
    else:
        # Re-derive which tier of discover_askpass()'s chain matched so the
        # report can flag a broken override / a suspicious env var.
        env_askpass = os.environ.get("SUDO_ASKPASS", "")

        def _resolves_to(candidate: str) -> bool:
            if os.path.isabs(candidate):
                return candidate == resolved
            return shutil.which(candidate) == resolved

        override_broken = bool(override) and not _resolves_to(override)
        if override_broken:
            rows.append(CheckRow(
                "askpass", CheckStatus.WARN,
                f"configured askpass {override!r} not found — fell back to "
                f"{resolved}",
                "Fix privilege.askpass in config.toml (or clear it to use "
                "auto-detection).",
            ))
        # The env-var warning is independent of the broken-override one: a
        # broken override + poisoned $SUDO_ASKPASS must surface BOTH.
        if (
            (not override or override_broken)
            and env_askpass == resolved
            and not env_askpass.startswith(("/usr/", "/usr/local/"))
        ):
            rows.append(CheckRow(
                "askpass", CheckStatus.WARN,
                f"honoring $SUDO_ASKPASS={env_askpass} from outside /usr — "
                "this binary will receive your sudo password",
                "Set privilege.askpass in config.toml to pin a trusted path.",
            ))
        if not rows:
            bundled = Path(resolved).name == "archward-askpass"
            rows.append(CheckRow(
                "askpass", CheckStatus.PASS,
                resolved + (" (bundled)" if bundled else ""),
            ))

    if (
        not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
        and not sudo_ok
    ):
        # Headless AND no passwordless sudo: a graphical askpass can't
        # render, so an unattended update would block. (Headless + NOPASSWD
        # is a healthy first-class setup — no warning there.)
        rows.append(CheckRow(
            "askpass", CheckStatus.WARN,
            "headless session (no DISPLAY/WAYLAND_DISPLAY) and no "
            "passwordless sudo — a graphical askpass cannot render",
            "Configure NOPASSWD for pacman, or run `sudo -v` in the "
            "terminal right before updating.",
        ))
    return rows


def _pacman_rows() -> list[CheckRow]:
    rows: list[CheckRow] = []
    if shutil.which("pacman") is None:
        rows.append(CheckRow(
            "pacman", CheckStatus.FAIL, "pacman binary not found",
        ))
    else:
        missing = [b for b in ("checkupdates", "paccache") if shutil.which(b) is None]
        if missing:
            rows.append(CheckRow(
                "pacman", CheckStatus.WARN,
                f"pacman-contrib tools missing: {', '.join(missing)}",
                "Install pacman-contrib — checkupdates drives the risk "
                "preview and paccache the cache retention.",
            ))
        else:
            rows.append(CheckRow(
                "pacman", CheckStatus.PASS,
                "pacman + pacman-contrib (checkupdates, paccache) present",
            ))
    return rows


def _db_lock_rows() -> list[CheckRow]:
    """Own check (not folded into _pacman_rows) so a hang on /var — exactly
    when the lock state matters — times out as `pacman-db-lock`, not as an
    anonymous casualty of the tooling check."""
    from archward.pacman.runner import check_pacman_db_lock

    rows: list[CheckRow] = []
    locked, owner = check_pacman_db_lock()
    if locked:
        is_stale = owner is not None and "stale" in owner
        if is_stale:
            detail = (
                "/var/lib/pacman/db.lck is present but no live pacman "
                "process holds it (likely a previous run was killed). "
                "After confirming no pacman / pacman-key / makepkg is "
                "running, remove it manually: "
                "`sudo rm /var/lib/pacman/db.lck`."
            )
        else:
            detail = (
                "/var/lib/pacman/db.lck is present and held by a live "
                "process. Wait for it to finish."
            )
        rows.append(CheckRow(
            "pacman-db-lock", CheckStatus.FAIL,
            f"pacman database is locked by {owner}",
            detail,
        ))
    else:
        rows.append(CheckRow(
            "pacman-db-lock", CheckStatus.PASS, "pacman database not locked",
        ))
    return rows


def _disk_rows(cfg) -> list[CheckRow]:
    from archward.system import disk

    free = disk.free_gb("/")
    min_gb = cfg.gates.min_disk_gb
    if free < min_gb:
        return [CheckRow(
            "disk-space", CheckStatus.FAIL,
            f"only {free}GB free on / (min {min_gb}GB)",
            "Run: sudo paccache -rk3",
        )]
    return [CheckRow(
        "disk-space", CheckStatus.PASS, f"{free}GB free on / (min {min_gb}GB)",
    )]


def _cache_rows(cfg) -> list[CheckRow]:
    from archward.system import cache_policy as cp

    policy = cp.detect_cache_policy()
    # Severity mirrors gates.preflight_checks: DANGEROUS / cleaning hooks
    # and configured-UNMANAGED are WARNs there, deliberately not FAILs.
    if policy.cleaning_hooks or policy.safety is cp.RollbackSafety.DANGEROUS:
        hook_names = ", ".join(h.name for h in policy.cleaning_hooks)
        msg = (
            f"a cache-cleaning pacman hook ({hook_names}) deletes the "
            "packages archward needs for rollback"
            if policy.cleaning_hooks
            else f"cache policy is {policy.safety.value} — rollback may not work"
        )
        return [CheckRow("cache-safety", CheckStatus.WARN, msg, policy.explanation)]
    if policy.safety is cp.RollbackSafety.UNMANAGED and cfg.cache.warn_if_unmanaged:
        return [CheckRow(
            "cache-safety", CheckStatus.WARN,
            "paccache retention is unmanaged — rollback coverage will "
            "degrade over time",
            "Run `archward cache status` to assess, or `archward cache "
            "set-keep N`.",
        )]
    return [CheckRow(
        "cache-safety", CheckStatus.PASS,
        f"rollback safety {policy.safety.value.upper()} "
        f"(keep {policy.effective_keep}, timer {policy.timer_state})",
    )]


def _state_rows(cfg) -> list[CheckRow]:
    from archward.config import paths

    problems: list[str] = []
    state_root = paths.state_dir()
    if not state_root.is_dir():
        return [CheckRow(
            "state-dirs", CheckStatus.PASS,
            f"state dir not created yet ({state_root} — first run creates it)",
        )]

    if not os.access(state_root, os.W_OK):
        problems.append(f"{state_root} is not writable")
    else:
        mode = state_root.stat().st_mode & 0o777
        if mode != 0o700:
            problems.append(
                f"{state_root} mode is {mode:o} (expected 700; the next "
                "pipeline run tightens it automatically)"
            )

    for sub in (state_root / "snapshots", state_root / "logs",
                state_root / "runs", cfg.general.snapshot_dir,
                cfg.general.log_dir):
        if sub.is_dir() and not os.access(sub, os.W_OK):
            problems.append(f"{sub} is not writable")

    if problems:
        unwritable = any("not writable" in p for p in problems)
        return [CheckRow(
            "state-dirs",
            CheckStatus.FAIL if unwritable else CheckStatus.WARN,
            "; ".join(problems),
            "Unwritable state dirs are commonly root-owned after a "
            "`sudo archward` run: sudo chown -R $USER <dir>",
        )]
    return [CheckRow(
        "state-dirs", CheckStatus.PASS, f"{state_root} writable, mode 700",
    )]


def _services_rows(cfg) -> list[CheckRow]:
    if not cfg.services.to_verify:
        return [CheckRow(
            "services-config", CheckStatus.SKIPPED,
            "no services configured in services.to_verify",
        )]
    if shutil.which("systemctl") is None:
        # detect_stale_services would silently report "nothing stale" here
        # (unit_exists returns True when systemctl is unreachable) — an
        # unearned PASS a diagnostic must not give.
        return [CheckRow(
            "services-config", CheckStatus.SKIPPED,
            "systemctl not available — cannot verify services.to_verify "
            "entries",
        )]

    from archward.config.detect import detect_stale_services

    stale = detect_stale_services(cfg)
    if stale:
        return [CheckRow(
            "services-config", CheckStatus.WARN,
            f"{len(stale)} stale entr{'y' if len(stale) == 1 else 'ies'} in "
            f"services.to_verify: {', '.join(stale)}",
            "These units have no unit file — every verify run flags them. "
            "Remove them via `archward --detect` or Preferences → Services.",
        )]
    return [CheckRow(
        "services-config", CheckStatus.PASS,
        f"all {len(cfg.services.to_verify)} services.to_verify units exist",
    )]


def _aur_rows(cfg) -> list[CheckRow]:
    from archward.config.detect import detect_aur_helper

    helper = detect_aur_helper(cfg.aur.helper_preference)
    if helper:
        return [CheckRow(
            "aur-helper", CheckStatus.PASS,
            f"{helper} (preference: {', '.join(cfg.aur.helper_preference)})",
        )]
    if cfg.aur.enabled and not cfg.aur.skip:
        return [CheckRow(
            "aur-helper", CheckStatus.WARN,
            "no AUR helper found (yay/paru/aurutils) — the AUR phase will "
            "be skipped",
        )]
    return [CheckRow(
        "aur-helper", CheckStatus.PASS,
        "no AUR helper — AUR phase disabled in config",
    )]


def _gui_rows() -> list[CheckRow]:
    import importlib.util

    from archward.locale_env import c_locale_env

    if importlib.util.find_spec("PySide6") is None:
        return [CheckRow(
            "gui-stack", CheckStatus.SKIPPED,
            "PySide6 not installed — GUI unavailable (CLI-only install)",
        )]

    def _probe(code: str) -> tuple[int | None, str]:
        try:
            r = subprocess.run(
                [sys.executable, "-c", code],
                check=False, capture_output=True, text=True,
                timeout=_GUI_PROBE_TIMEOUT_S, env=c_locale_env(),
            )
        except subprocess.TimeoutExpired:
            return None, ""
        tail = "\n".join(r.stderr.strip().splitlines()[-6:])
        return r.returncode, tail

    rc, tail = _probe("import PySide6.QtWidgets")
    if rc is None:
        return [CheckRow(
            "gui-stack", CheckStatus.WARN,
            f"PySide6 import probe timed out after {_GUI_PROBE_TIMEOUT_S}s",
        )]
    if rc != 0:
        return [CheckRow(
            "gui-stack", CheckStatus.FAIL,
            "PySide6 is installed but fails to import — the GUI will not "
            "start",
            tail or None,
        )]

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return [CheckRow(
            "gui-stack", CheckStatus.PASS,
            "PySide6 imports (Qt platform not tested — headless session)",
        )]

    # Qt loads its platform plugin (wayland/xcb + system libs) at
    # QApplication construction, NOT at import — a box can import PySide6
    # fine and still abort at GUI launch. This is the probe that catches
    # that class; its stderr names the missing/available platform plugins.
    rc, tail = _probe(
        "from PySide6.QtWidgets import QApplication; QApplication([])"
    )
    if rc is None:
        return [CheckRow(
            "gui-stack", CheckStatus.WARN,
            f"Qt platform probe timed out after {_GUI_PROBE_TIMEOUT_S}s",
        )]
    if rc != 0:
        return [CheckRow(
            "gui-stack", CheckStatus.FAIL,
            "Qt cannot initialize a platform plugin — the GUI will not "
            "start on this session",
            tail or None,
        )]
    return [CheckRow(
        "gui-stack", CheckStatus.PASS, "PySide6 import + Qt platform OK",
    )]


def _lastrun_rows() -> list[CheckRow]:
    from archward.cli_subcommands.history import _age_of
    from archward.pipeline import run_record

    files = run_record.list_run_files()
    if not files:
        return [CheckRow(
            "last-run", CheckStatus.SKIPPED, "no runs recorded yet",
        )]
    doc = run_record.load_run(files[0])
    if doc is None:
        return [CheckRow(
            "last-run", CheckStatus.WARN,
            f"newest run record is unreadable: {files[0]}",
        )]
    run_id = doc.get("run_id", files[0].stem)
    age = _age_of(doc)
    tag = (doc.get("result") or {}).get("tag")
    if tag is None:
        return [CheckRow(
            "last-run", CheckStatus.WARN,
            f"last run crashed ({run_id}, {age})",
            "See `archward history show latest` for the partial record.",
        )]
    if tag in ("RESULT:UPDATE_FAILED", "RESULT:VERIFY_FAILED"):
        return [CheckRow(
            "last-run", CheckStatus.WARN,
            f"last run {age}: {tag}",
            "See `archward history show latest` for error lines and phases.",
        )]
    return [CheckRow(
        "last-run", CheckStatus.PASS, f"last run {age}: {tag}",
    )]


# ── entry point ───────────────────────────────────────────────────────────


def cmd_doctor(args, config_path: Path | None) -> int:
    cfg_holder: dict = {}
    sudo_holder: dict = {"ok": False}

    rows: list[CheckRow] = []
    rows += _run_check("archward", _identity_rows)
    rows += _run_check("running-as-root", _root_rows)
    rows += _run_check("distro", _distro_rows)
    rows += _run_check("config", lambda: _config_rows(config_path, cfg_holder))

    if "cfg" not in cfg_holder:
        # Config check crashed or timed out before producing a model —
        # the remaining checks run against pure defaults.
        from archward.config.defaults import default_config
        cfg_holder["cfg"] = default_config()
        rows.append(CheckRow(
            "config", CheckStatus.WARN,
            "config could not be evaluated — remaining checks use defaults",
        ))
    cfg = cfg_holder["cfg"]

    rows += _run_check("sudo", lambda: _sudo_rows(sudo_holder))
    rows += _run_check("askpass", lambda: _askpass_rows(cfg, sudo_holder["ok"]))
    rows += _run_check("pacman", _pacman_rows)
    rows += _run_check("pacman-db-lock", _db_lock_rows)
    rows += _run_check("disk-space", lambda: _disk_rows(cfg))
    rows += _run_check("cache-safety", lambda: _cache_rows(cfg))
    rows += _run_check("state-dirs", lambda: _state_rows(cfg))
    rows += _run_check("aur-helper", lambda: _aur_rows(cfg))
    rows += _run_check("services-config", lambda: _services_rows(cfg))
    # gui-stack runs two sequential subprocess probes of _GUI_PROBE_TIMEOUT_S
    # each — under the default deadline the second probe's own timeout could
    # never fire, orphaning a hung Qt child when doctor exits.
    rows += _run_check(
        "gui-stack", _gui_rows, deadline=2 * _GUI_PROBE_TIMEOUT_S + 5
    )
    rows += _run_check("last-run", _lastrun_rows)

    fails = sum(1 for r in rows if r.status is CheckStatus.FAIL)
    warns = sum(1 for r in rows if r.status is CheckStatus.WARN)
    passes = sum(1 for r in rows if r.status is CheckStatus.PASS)
    skips = sum(1 for r in rows if r.status is CheckStatus.SKIPPED)

    if getattr(args, "json", False):
        doc = {
            "schema_version": DOCTOR_SCHEMA_VERSION,
            "checks": [
                {
                    "name": r.name,
                    "status": str(r.status),
                    "message": r.message,
                    "detail": r.detail,
                }
                for r in rows
            ],
        }
        print(json.dumps(doc, indent=2))
    else:
        print(f"archward {__version__} — doctor")
        print()
        for r in rows:
            print(f"  {r.name:<16} {r.status.upper():<8} {r.message}")
            # Details print for every status: the identity row's interpreter
            # path (a PASS) is exactly what a support thread needs to see.
            if r.detail:
                for line in r.detail.splitlines():
                    print(f"  {'':<16} {'':<8} {line}")
        print()
        print(
            f"summary: {passes} pass, {warns} warn, {fails} fail, "
            f"{skips} skipped"
        )

    # Same convention as `archward gates`: 0 clean, 1 any FAIL, 2 WARN only.
    if fails:
        return 1
    if warns:
        return 2
    return 0
