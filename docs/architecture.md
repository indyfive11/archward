# archward — architecture

High-level layering, top to bottom:

```
  ┌─────────────────────────────────────────────────────┐
  │  ui/             cli.py            (interfaces)     │
  └─────────────────────┬───────────────────────────────┘
                        │  PhaseEvent via EventBus
  ┌─────────────────────▼───────────────────────────────┐
  │  pipeline/                                          │
  │    pipeline.py orchestrates 8 phases                │
  │    one file per phase: snapshot, gates, risk,       │
  │    update_official, update_aur, pacnew, verify,     │
  │    report                                           │
  │    prompter.py — user-decision boundary             │
  └─────────────────────┬───────────────────────────────┘
                        │  command + parse
  ┌─────────────────────▼───────────────────────────────┐
  │  pacman/  aur/  privilege/  system/                 │
  │  query, runner, sudo strategy, distro/disk/kernel/  │
  │  services                                           │
  └─────────────────────┬───────────────────────────────┘
                        │  exec
                  ┌─────▼─────┐
                  │   OS      │
                  │   pacman  │
                  │   yay/etc │
                  └───────────┘
```

## Threading

The pipeline always runs on a **single thread**:

- CLI: pipeline runs on the main thread; events fire synchronously to the
  console subscriber.
- GUI: pipeline runs on a `QThread` (PipelineWorker). EventBus is pure
  Python; events emitted on the pipeline thread are re-emitted as Qt
  signals via `QtEventBridge` (a `QObject` living on the main thread).
  Qt's auto-connection rules deliver those signals via `QueuedConnection`
  to the main thread.

Pydantic models passed across the boundary use `frozen=True` and tuple /
Mapping for collection fields so they're effectively immutable — see
[PLAN.md audit A1].

## Event bus

`events.py:EventBus` is a thread-safe pub/sub:

```python
class PhaseEvent(BaseModel):
    kind: PhaseEventKind  # PHASE_START / PHASE_LOG / PHASE_RESULT / PIPELINE_DONE
    phase: str
    message: str | None
    payload: dict | None  # rich data — Pydantic model dumps for views to absorb
    timestamp: datetime
```

`PHASE_RESULT` events carry a `payload` so views can render typed data
(GateResult, PendingUpdate, VerifyResult, PacnewFile) without parsing log
strings.

## Sudo strategy

`privilege/sudo.py` abstracts privilege escalation:

- `AskpassStrategy` — discovers `ksshaskpass` / `lxqt-openssh-askpass` /
  `ssh-askpass`; sets `SUDO_ASKPASS`; uses `sudo -A`.
- `PersistentSudoStrategy` — wraps Askpass with an upfront `sudo -A -v` so
  the timestamp stays warm for the duration of one pipeline run.
- `pkexec` mode is reserved for a future phase.

AUR helpers run as the **invoking user** (yay/paru refuse to run as root);
they inherit `SUDO_ASKPASS` and prompt for sudo internally when installing
built packages. `pacman.runner.run_streaming(..., use_sudo=False)` is the
"don't prefix sudo" mode used for helpers.

## Config

Two layers:

1. `config/defaults.py:default_config()` — hard-coded fallback baseline.
2. `config/loader.py:load_config()` — reads `~/.config/archward/config.toml`
   with per-section ValidationError recovery (a broken `[gates]` table
   doesn't nuke `[risk]`). On first run, writes defaults out.

Auto-detection (`config/detect.py:run_full_detection`) is called only by
`archward --detect` and Preferences → Re-detect — never on the pipeline
hot path.

## RESULT precedence (audit G4)

`pipeline/report.py:derive_result()` aggregates pipeline state into a
single `RESULT:` tag:

```
preflight_failed                              → UPDATE_FAILED
update_exit_code != 0                         → UPDATE_FAILED
verify.fail_count > 0                         → VERIFY_FAILED (with secondary tags)
update_applied AND any(p.is_kernel)
 OR verify.reboot_needed                      → REBOOT_NEEDED
pacnew_count > 0                              → PACNEW_MERGE_NEEDED
was_dry_run AND any(p.risk == HIGH)           → NEEDS_REVIEW
else                                          → SUCCESS
```

Secondary tags (`+ RESULT:REBOOT_NEEDED` etc.) annotate the primary so the
result strip shows the full picture without losing the primary precedence.

## v2 seams

- `pipeline/hooks.py:HookRunner` — no-op stub; v2 fills the body and adds
  `[hooks]` to ConfigModel. `pipeline.run_pipeline()` already calls
  `hooks.run_pre_update()` and `run_post_verify()` at the right points.
- `config/loader.py` — single entry point. v2 adds
  `~/.config/archward/profiles/<name>.toml` + `--profile` flag with no
  other code changes.
- `pipeline/verify_phase.py` — v2 introduces entry-points group
  `archward.verify_checks` scanned at startup. Today the check list is
  hard-coded.

See [PLAN.md §v2 reservations] for the full list.
