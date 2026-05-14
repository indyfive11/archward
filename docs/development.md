# archward — development guide

## Setup

```bash
git clone git@github.com:indyfive11/archward.git ~/dev/archward
cd ~/dev/archward
python3 -m venv venv
source venv/bin/activate
pip install -e ".[gui,dev]"
```

`[dev]` adds pytest + pytest-qt. `[gui]` adds PySide6 — leave it out if
you're only changing CLI / pipeline code.

## Running

```bash
archward --dry-run        # no mutation; snapshot + risk preview
archward                  # interactive update; prompts on HIGH-risk
archward --auto           # hands-off; aborts if HIGH-risk
archward --detect         # propose config diff against live system
archward-gui              # PySide6 GUI
```

## Tests

```bash
./venv/bin/python3 -m pytest tests/unit/ -q
```

78 tests as of v0.1.0. Most parsers/classifiers are validated against
fixtures under `tests/fixtures/` — see `tests/fixtures/README.md` for the
regeneration procedure.

## Sudo for unattended use (optional)

By default, sudo prompts via askpass (`ksshaskpass` on KDE, etc.) — which
means a real desktop session is required. For scripted / headless runs
(scheduled updates, VMs, dev iteration), opt into a narrow NOPASSWD entry:

```
# /etc/sudoers.d/archward — chmod 440
yourusername ALL=(root) NOPASSWD: /usr/bin/pacman, /usr/bin/find, /usr/bin/tee, /usr/bin/tar, /usr/bin/cp, /usr/bin/chown, /usr/bin/chmod, /usr/bin/mv, /usr/bin/rm
```

This is **opt-in security relaxation**. The default (askpass + PAM) is
correct for most users; only drop a NOPASSWD entry on machines you control
end-to-end. The list above is the minimum needed for archward's snapshot
(tar, cp, chown, chmod for the `.tar.gz` archives) and pacnew apply
(mv, cp, chown, chmod, rm).

## Code layout

```
src/archward/
  cli.py              argparse + main()/main_gui() entry points
  app.py              composition root: config, sudo, lock, event bus
  events.py           pure-Python EventBus + PhaseEvent
  models/             frozen Pydantic models (snapshot, update, gate, pacnew, verify, aur, config)
  config/             defaults.py, loader.py (TOML), detect.py (auto-detect)
  pacman/             query.py, runner.py (streaming), pacnew.py
  aur/                helper.py + adapters/{yay,paru,aurutils}.py
  pipeline/           one file per phase + pipeline.py orchestrator + report.py + prompter.py
  privilege/          sudo.py — AskpassStrategy + PersistentSudoStrategy
  system/             distro.py, disk.py, kernel.py, services.py
  ui/                 main_window.py + phase_rail.py + log_pane.py + qt_bus.py + prompter.py
  ui/views/           per-phase content widgets + result_banner.py
  ui/dialogs/         preferences.py (10-tab editor)
```

## Pipeline contract

`pipeline/pipeline.py:run_pipeline()` runs to completion and returns a
`PipelineResult`. It never raises on update failure — the result's `summary`
holds the RESULT tag. The pipeline emits `PhaseEvent` (PHASE_START /
PHASE_LOG / PHASE_RESULT) into the `EventBus` for the GUI / CLI consumer to
display.

### Cancellation

`cancel_event: threading.Event` is checked at phase boundaries and per-line
during streaming. pacman and AUR helpers are **never** killed mid-flight
(half-finished pacman transactions leave `db.lck` orphaned and the
database in an inconsistent state). Cancel during update phase only stops
emitting further log events; the subprocess is allowed to finish.

### Prompter

The pipeline asks the user yes/no questions through a `Prompter` Protocol:

- HIGH-risk approval (when classification surfaces HIGH packages)
- Recoverable gate override (e.g. disk-space gate with `allow_override=true`)

CLI uses `CliPrompter` (stdin `input()`); GUI uses `GuiPrompter` which routes
through `QMessageBox` via `BlockingQueuedConnection`. The pipeline code is
unaware of which.

## Releasing

1. Update `__version__` in `src/archward/__init__.py` and `pyproject.toml`.
2. Add a release section to `CHANGELOG.md` (move from `Unreleased` to
   `[<version>] — <date>`).
3. Run the full test suite + smoke-test `archward --dry-run` on a clean VM.
4. Tag: `git tag -a v<version> -m "Release v<version>"`.
5. Build wheel: `python -m build --wheel --no-isolation`.
6. PKGBUILD's `pkgver` and `source` URL pick up the GitHub release tarball
   automatically once the tag is pushed.

The v0.1.0 PKGBUILD uses `sha256sums=('SKIP')` for development convenience.
Replace with a real sha256 before publishing to AUR.
