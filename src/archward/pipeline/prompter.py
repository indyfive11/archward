"""User-decision boundary for the pipeline.

run_pipeline asks the user one or two yes/no questions during execution:
HIGH-risk approval and (optionally) gate-override on a recoverable gate
failure. In CLI mode these are stdin input() calls; in GUI mode they're
inline RiskView interactions (v0.3.0+) or QMessageBox modals (gate override).

The Prompter Protocol abstracts both. The pipeline only calls prompter
methods; it doesn't know whether the implementation prints to terminal or
activates a widget.

v0.3.0 adds `decide_high_risk` which returns BOTH a proceed flag and a list
of package names to deselect (mapped to `pacman --ignore=<pkg>`). The GUI
implementation surfaces checkboxes in the Risk view so the user can drop
specific packages from the update without aborting the whole run. The CLI
implementation keeps the legacy Y/N flow and returns an empty ignore list;
extending CLI to interactive deselect can come later.
"""

from __future__ import annotations

from typing import Protocol

from archward.models.gate import GateResult
from archward.models.update import PendingUpdate


class Prompter(Protocol):
    """Synchronous decision points the pipeline asks the user about.

    GUI implementations block the calling (pipeline) thread until the user
    answers — typically via Qt::BlockingQueuedConnection or a
    threading.Event set from a Qt signal handler.
    """

    def decide_high_risk(
        self, high: list[PendingUpdate]
    ) -> tuple[bool, list[str]]:
        """Return (proceed, ignored_pkg_names).

        `proceed=False` aborts the update; `ignored_pkg_names` is the list of
        packages to pass to pacman as `--ignore=<pkg>`. Empty list means "run
        the full update".
        """

    def confirm_gate_override(self, gate: GateResult) -> bool: ...


class AutoYesPrompter:
    """Pipeline gets a free pass on every decision. Used by --yes and GUI dry-run."""

    def decide_high_risk(
        self, high: list[PendingUpdate]  # noqa: ARG002
    ) -> tuple[bool, list[str]]:
        return True, []

    def confirm_gate_override(self, gate: GateResult) -> bool:  # noqa: ARG002
        return True


class AutoNoPrompter:
    """Pipeline always declines. Used by --auto mode."""

    def decide_high_risk(
        self, high: list[PendingUpdate]  # noqa: ARG002
    ) -> tuple[bool, list[str]]:
        return False, []

    def confirm_gate_override(self, gate: GateResult) -> bool:  # noqa: ARG002
        return False


class CliPrompter:
    """input()-based prompter for the terminal.

    Per-package deselection isn't supported in the CLI flow yet; returns an
    empty ignore list. CLI users who want to skip a specific package today
    should add it to `pacman.extra_args` in config (e.g. `["--ignore=pkg"]`).
    """

    def decide_high_risk(
        self, high: list[PendingUpdate]
    ) -> tuple[bool, list[str]]:
        try:
            answer = input(
                f"Proceed with update including {len(high)} HIGH RISK package(s)? [y/N] "
            ).strip().lower()
        except EOFError:
            return False, []
        return (answer in ("y", "yes")), []

    def confirm_gate_override(self, gate: GateResult) -> bool:
        try:
            answer = input(
                f"Gate {gate.name} failed: {gate.message}. Override? [y/N] "
            ).strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")
